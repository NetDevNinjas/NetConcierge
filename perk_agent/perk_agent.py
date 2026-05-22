"""NetConcierge Perk Agent — 2-Tier Compensation System.

Receives fault-event webhooks from the network troubleshooting agent and issues
perks based on a tiered approach:

  Tier 1 (immediate): Triggered when a fault is first detected. Provides a
      same-day WiFi bill refund and a complimentary drink or appetizer at the
      hotel bar. No LLM needed — these are fixed perks.

  Tier 2 (escalation): Triggered only when the network agent cannot resolve
      the fault (status=escalated). Uses an LLM to recommend higher-level perks
      (free dinner, additional comp discounts) based on the customer profile.
"""

import contextlib
import json
import logging
import os
import random
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import requests
from flask import Flask, jsonify, request
from openai import OpenAI

# ── Configuration ──────────────────────────────────────────────────────────────
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://litellm-api.up.railway.app/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-3-5-haiku-20241022")
PROFILES_DIR = os.environ.get("PROFILES_DIR", "/app/profiles")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "")
# FORWARD_URL = os.environ.get("FORWARD_URL", "http://localhost:9000/recommendations")
AGENT_URL = os.environ.get("AGENT_URL", "http://agent:8080")
## If no update from the network agent within this many seconds, poll it for status
UPDATE_TIMEOUT_SECS = int(os.environ.get("UPDATE_TIMEOUT_SECS", "900"))  # 15 minutes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# LLM client — OpenAI-compatible; points at LiteLLM proxy
_llm = OpenAI(
    base_url=LLM_BASE_URL,
    api_key=os.environ.get("TIP_API_KEY", "no-key"),
)

## Active fault tracking: room → {detected_at, last_update}
_active_faults: dict = {}
_faults_lock = threading.Lock()

# ── Tier 1: Randomized perks pool (no LLM needed) ─────────────────────────────
TIER_1_PERK_POOL = [
    {
        "type": "wifi_refund",
        "value": "Full WiFi bill refund for today",
        "description": "Guest's WiFi charges for the current day are fully refunded.",
    },
    {
        "type": "complimentary_cocktail",
        "value": "Complimentary signature cocktail at the rooftop bar",
        "description": "Guest may enjoy one handcrafted cocktail from our rooftop lounge menu.",
    },
    {
        "type": "free_appetizer",
        "value": "Complimentary appetizer at any hotel restaurant",
        "description": "Guest may select any starter from the dinner menu, on the house.",
    },
    {
        "type": "spa_express",
        "value": "Complimentary 15-minute express massage",
        "description": "A relaxing neck & shoulder massage at the hotel spa — no appointment needed.",
    },
    {
        "type": "room_service_credit",
        "value": "$25 room service credit",
        "description": "A $25 credit applied toward any room service order during the stay.",
    },
    {
        "type": "late_checkout",
        "value": "Complimentary 2-hour late checkout",
        "description": "Guest may check out up to 2 hours past standard time at no charge.",
    },
    {
        "type": "breakfast_voucher",
        "value": "Complimentary breakfast for two",
        "description": "Full breakfast buffet for two at the hotel restaurant, next morning.",
    },
    {
        "type": "minibar_credit",
        "value": "Complimentary minibar items (up to $20)",
        "description": "Guest may enjoy up to $20 worth of minibar snacks and beverages.",
    },
    {
        "type": "pool_cabana",
        "value": "Complimentary poolside cabana for 2 hours",
        "description": "Reserved cabana at the pool deck with refreshments included.",
    },
    {
        "type": "dessert_platter",
        "value": "Complimentary dessert platter delivered to room",
        "description": "A curated selection of pastries and chocolates from our pastry chef.",
    },
]

TIER_1_APOLOGY_TEMPLATES = [
    "We sincerely apologize for the WiFi disruption. We've {perk1} and {perk2} while our team works to restore full service.",
    "We're sorry for the inconvenience with your internet connection. As a gesture of goodwill, we'd like to offer you {perk1} and {perk2}.",
    "Please accept our apologies for the connectivity issue. We've arranged {perk1} and {perk2} for you.",
]


def _build_tier_1_response(room: str) -> dict:
    """Return a randomized Tier 1 perk package (always includes WiFi refund + one random perk)."""
    # Always include WiFi refund as first perk
    wifi_refund = TIER_1_PERK_POOL[0]
    # Pick one additional random perk (excluding WiFi refund)
    bonus_perk = random.choice(TIER_1_PERK_POOL[1:])

    perks = [wifi_refund, bonus_perk]
    apology = random.choice(TIER_1_APOLOGY_TEMPLATES).format(
        perk1=wifi_refund["value"].lower(),
        perk2=bonus_perk["value"].lower(),
    )

    return {
        "tier": 1,
        "room": room,
        "perks": perks,
        "apology_note": apology,
    }

# ── Tier 2: LLM-recommended perks for unresolved escalations ──────────────────
TIER_2_SYSTEM_PROMPT = """\
You are NetConcierge's Customer Experience Agent for a luxury hotel.

A network fault could NOT be automatically resolved and has been escalated.
The guest has already received Tier 1 compensation (WiFi refund for today +
complimentary drink/appetizer at the bar). Now you must recommend ADDITIONAL
higher-level perks to make up for the extended disruption.

Given:
- A customer profile (loyalty tier, stay history, booking value, etc.)
- Details about the unresolved network fault (type, duration, resolution attempts)

Recommend ONE primary perk and optionally ONE secondary perk from this menu:
- Complimentary dinner for two at the hotel restaurant (up to $150 value)
- Spa credit ($50-$150 depending on guest tier and severity)
- Complimentary room night (current stay extension or future stay)
- Room upgrade for remainder of stay
- Loyalty points bonus (1000-5000 points)
- Late checkout + early check-in on next visit
- Percentage discount on current bill (10-25%)

Guidelines:
- Higher loyalty tier → more generous perks
- Longer disruptions (more turns_used) → more generous
- Business travelers who needed WiFi for work → prioritize meaningful compensation
- Consider the guest's booking value and lifetime spend
- These perks stack ON TOP of Tier 1 (WiFi refund + one bonus perk already given)

Respond with a JSON object:
{
  "primary_perk": {"type": "...", "value": "...", "reason": "..."},
  "secondary_perk": {"type": "...", "value": "...", "reason": "..."} or null,
  "apology_note": "A personalized escalation apology message for the guest",
  "internal_notes": "Brief justification for the elevated compensation"
}
"""


# ── Helper ─────────────────────────────────────────────────────────────────────
def _emit_event(event_type: str, message: str, data: dict | None = None, source: str = "perk-agent") -> None:
    """Push an event to the frontend dashboard (best-effort)."""
    if not FRONTEND_URL:
        return
    payload = {
        "source": source,
        "type": event_type,
        "message": message,
        "data": data,
    }
    with contextlib.suppress(Exception):
        requests.post(FRONTEND_URL, json=payload, timeout=3)


def _load_customer_profile(room: str = "412") -> str:
    """Load the customer profile for a given room number."""
    profile_path = Path(PROFILES_DIR) / f"room_{room}.txt"
    try:
        return profile_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Fallback: try any available profile
        profiles_dir = Path(PROFILES_DIR)
        if profiles_dir.exists():
            available = list(profiles_dir.glob("room_*.txt"))
            if available:
                return random.choice(available).read_text(encoding="utf-8")
        log.warning("No customer profile found for room %s", room)
        return "(no customer profile available)"


def _build_tier_2_response(fault_event: dict) -> dict:
    """Call the LLM to recommend Tier 2 perks for an unresolved escalation."""
    room = fault_event.get("room", "412")
    customer_profile = _load_customer_profile(room)

    user_message = (
        f"## Escalated Network Fault (Unresolved)\n"
        f"- Fault Type: {fault_event.get('fault_type', 'unknown')}\n"
        f"- Summary: {fault_event.get('summary', 'No summary provided')}\n"
        f"- Room: {fault_event.get('room', 'unknown')}\n"
        f"- Active Path: {fault_event.get('active_path', 'unknown')}\n"
        f"- Resolution Attempts (turns_used): {fault_event.get('turns_used', 0)}\n"
        f"- Timestamp: {fault_event.get('timestamp', 'unknown')}\n\n"
        f"## Customer Profile\n"
        f"{customer_profile}\n\n"
        f"The guest already received Tier 1 perks (WiFi refund + a bonus perk). "
        f"Please recommend additional higher-level compensation."
    )

    try:
        response = _llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": TIER_2_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
        )
        content = response.choices[0].message.content

        try:
            recommendation = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            recommendation = {"raw_recommendation": content}

    except Exception as exc:
        log.error("LLM API call failed: %s", exc)
        recommendation = {
            "error": str(exc),
            "fallback": "Offer complimentary dinner for two and 2000 loyalty points",
        }

    return {
        "tier": 2,
        "room": fault_event.get("room", "unknown"),
        "recommendation": recommendation,
    }


# ── Flask endpoints ────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "perk-agent"})


@app.get("/status")
def perk_status():
    with _faults_lock:
        return jsonify({"service": "perk-agent", "active_faults": dict(_active_faults)})


@app.post("/fault-update")
def fault_update():
    """Receive a diagnostic progress update from the network agent."""
    data = request.get_json(force=True)
    room = data.get("room", "unknown")
    turn = data.get("turn", 0)
    tool = data.get("tool", "unknown")
    with _faults_lock:
        if room in _active_faults:
            _active_faults[room]["last_update"] = time.time()
            _active_faults[room]["turn_count"] = turn
    log.info("Fault update — room=%s turn=%d tool=%s", room, turn, tool)
    return jsonify({"status": "acknowledged"})


@app.post("/guest-report")
def guest_report():
    """Receive a guest-initiated problem report."""
    data = request.get_json(force=True)
    room = data.get("room", "unknown")
    message = data.get("message", "Guest reported an issue.")
    log.info("═" * 60)
    log.info("GUEST REPORT — Room %s", room)
    log.info(message)
    log.info("Forwarding to network agent for diagnostics...")
    log.info("═" * 60)
    _emit_event(
        "info",
        f"📞 Guest self-report — Room {room}: \"{message}\"",
        {"room": room, "message": message, "channel": "guest-self-report"},
        source="network-agent",
    )
    try:
        resp = requests.get(f"{AGENT_URL}/status", timeout=5)
        agent_info = resp.json()
        log.info("Network agent current status: %s", agent_info.get("status"))
        _emit_event(
            "info",
            f"Agent status at time of guest report: {agent_info.get('status', '?')} "
            f"(turn {agent_info.get('current_turn', 0)}, last: {agent_info.get('last_tool') or 'none'})",
            agent_info,
            source="network-agent",
        )
    except Exception as exc:
        log.warning("Could not reach network agent: %s", exc)
    return jsonify(
        {
            "status": "received",
            "room": room,
            "message": "We are investigating your issue and will update you shortly.",
        }
    )


@app.post("/fault-event")
def fault_event():
    """Receive a fault-event webhook from the network agent and issue perks.

    Behaviour by status:
      - status=detected (tier=1): Fault just found — issue immediate fixed perks and open tracking
      - status=resolved  (tier=1): Issue fixed — log resolution, no additional perks
      - status=escalated (tier=2): Could not fix — LLM recommends higher-level perks
    """
    data = request.get_json(force=True)
    room = data.get("room", "unknown")
    status = data.get("status", "detected")
    tier = data.get("tier", 1)
    fault_type = data.get("fault_type", "unknown")

    log.info(
        "Received fault event — room=%s status=%s tier=%d fault_type=%s",
        room,
        status,
        tier,
        fault_type,
    )

    if status == "detected":
        ## Open fault tracking so the background poller can monitor it
        with _faults_lock:
            _active_faults[room] = {
                "detected_at": data.get("timestamp", datetime.now(UTC).isoformat()),
                "last_update": time.time(),
                "turn_count": 0,
            }
        result = _build_tier_1_response(room)
        _apology = result["apology_note"]
        _emit_event(
            "tier1",
            f'Perk Agent → Room {room}: "{_apology}"',
        )
        _emit_event(
            "tier1",
            f"🎁 Perks for Room {room}: {' | '.join(p['value'] for p in result['perks'])}",
            data={"perks": result.get("perks")},
        )
    elif status == "resolved":
        ## Close fault tracking and log resolution — tier-1 perks were already issued
        with _faults_lock:
            _active_faults.pop(room, None)
        result = {
            "tier": 1,
            "status": "resolved",
            "room": room,
            "message": "Issue resolved — tier-1 perks already issued, no further action.",
        }
        _emit_event("resolved", f"✅ Fault resolved for Room {room} — no additional perks needed")
        _emit_event(
            "resolved",
            f"Dear Guest in Room {room}, your connectivity issue has been fully resolved. "
            f"We sincerely apologize for any inconvenience and hope the rest of your stay "
            f"is seamless and enjoyable. Thank you for your patience. 🙏",
        )
    elif status == "escalation-resolved":
        ## Human operator resolved the escalated fault
        with _faults_lock:
            _active_faults.pop(room, None)
        result = {
            "tier": 2,
            "status": "escalation-resolved",
            "room": room,
            "message": "Escalated issue has been resolved — tier-2 perks were already issued.",
        }
        _emit_event(
            "resolved",
            f"✅ Escalated fault resolved for Room {room} — issue fully closed",
        )
        _emit_event(
            "resolved",
            f"Dear Guest in Room {room}, your connectivity issue has been resolved by our engineering team. "
            f"We are deeply sorry for the extended inconvenience. Your tier-2 compensation is on its way — "
            f"we hope to make the rest of your stay truly exceptional. 🙏",
        )
    elif tier == 2:
        _emit_event(
            "tier2",
            f"🏆 Escalation received for Room {room} — generating elevated perks via LLM...",
        )
        ## Escalated and unresolved — close tracking and issue elevated LLM perks
        with _faults_lock:
            _active_faults.pop(room, None)
        result = _build_tier_2_response(data)
        rec = result.get("recommendation") or {}
        apology = rec.get("apology_note", "")
        if apology:
            _emit_event("tier2", f'Perk Agent → Room {room}: "{apology}"')
        tier2_perks = " | ".join(p.get("value", "") for p in rec.get("perks", []) if p.get("value"))
        if tier2_perks:
            _emit_event("tier2", f"🏆 Tier 2 perks for Room {room}: {tier2_perks}", data=rec)
        else:
            _emit_event("tier2", f"🏆 Tier 2 perks issued for Room {room}", data=rec)
    else:
        result = _build_tier_1_response(room)
        _emit_event(
            "tier1",
            f"🎁 Tier 1 perks issued for Room {room}: WiFi refund + complimentary drink/appetizer",
            data={"perks": result.get("perks")},
        )

    log.info("═" * 60)
    log.info("TIER %d PERKS for Room %s", tier, room)
    log.info("═" * 60)
    log.info(json.dumps(result, indent=2))
    log.info("═" * 60)

    # ── Optional: Forward recommendation to an external system ──────────────
    # Uncomment the block below to POST the recommendation to another service
    # (e.g., a guest-facing app, CRM, Slack channel, or notification service).
    #
    # forward_payload = {
    #     "room": room,
    #     "tier": tier,
    #     "fault_type": fault_type,
    #     "result": result,
    # }
    # try:
    #     forward_url = os.environ.get("FORWARD_URL", "http://localhost:9000/recommendations")
    #     resp = requests.post(forward_url, json=forward_payload, timeout=10)
    #     log.info("Forwarded recommendation to %s — HTTP %d", forward_url, resp.status_code)
    # except Exception as exc:
    #     log.warning("Failed to forward recommendation: %s", exc)

    return jsonify(result)


# ── Background polling ─────────────────────────────────────────────────────────
def _poll_agent_loop() -> None:
    """Poll the network agent for status if an active fault has gone silent for UPDATE_TIMEOUT_SECS."""
    while True:
        time.sleep(60)
        with _faults_lock:
            stale = [
                room
                for room, info in _active_faults.items()
                if time.time() - info.get("last_update", 0) > UPDATE_TIMEOUT_SECS
            ]
        for room in stale:
            log.warning(
                "No update from agent for room=%s in %ds — polling agent for status",
                room,
                UPDATE_TIMEOUT_SECS,
            )
            try:
                resp = requests.get(f"{AGENT_URL}/status", timeout=5)
                agent_info = resp.json()
                log.info(
                    "Agent status poll — room=%s agent_status=%s", room, agent_info.get("status")
                )
                ## Reset the timer so we don't spam polls every 60s
                with _faults_lock:
                    if room in _active_faults:
                        _active_faults[room]["last_update"] = time.time()
            except Exception as exc:
                log.warning("Agent status poll failed for room=%s: %s", room, exc)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Perk Agent starting on port 8081")
    threading.Thread(target=_poll_agent_loop, daemon=True, name="agent-poller").start()
    app.run(host="0.0.0.0", port=8081)
