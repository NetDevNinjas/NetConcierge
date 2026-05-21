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

import json
import logging
import os
from pathlib import Path

import requests
from flask import Flask, jsonify, request
from openai import OpenAI

# ── Configuration ──────────────────────────────────────────────────────────────
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://litellm-api.up.railway.app/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-3-5-haiku-20241022")
CUSTOMER_PROFILE_PATH = os.environ.get("CUSTOMER_PROFILE_PATH", "/app/customer_profile.txt")
# FORWARD_URL = os.environ.get("FORWARD_URL", "http://localhost:9000/recommendations")

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

# ── Tier 1: Fixed perks (no LLM needed) ───────────────────────────────────────
TIER_1_PERKS = {
    "tier": 1,
    "perks": [
        {
            "type": "wifi_refund",
            "value": "Full WiFi bill refund for today",
            "description": "Guest's WiFi charges for the current day are fully refunded.",
        },
        {
            "type": "complimentary_bar_item",
            "value": "Free drink or appetizer at the hotel bar",
            "description": "Guest may redeem one complimentary drink or appetizer at the lobby bar.",
        },
    ],
    "apology_note": (
        "We sincerely apologize for the WiFi disruption. We've refunded today's "
        "WiFi charges and would love to offer you a complimentary drink or appetizer "
        "at our lobby bar while our team works to restore full service."
    ),
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
- Spa credit ($50–$150 depending on guest tier and severity)
- Complimentary room night (current stay extension or future stay)
- Room upgrade for remainder of stay
- Loyalty points bonus (1000–5000 points)
- Late checkout + early check-in on next visit
- Percentage discount on current bill (10–25%)

Guidelines:
- Higher loyalty tier → more generous perks
- Longer disruptions (more turns_used) → more generous
- Business travelers who needed WiFi for work → prioritize meaningful compensation
- Consider the guest's booking value and lifetime spend
- These perks stack ON TOP of Tier 1 (WiFi refund + bar item already given)

Respond with a JSON object:
{
  "primary_perk": {"type": "...", "value": "...", "reason": "..."},
  "secondary_perk": {"type": "...", "value": "...", "reason": "..."} or null,
  "apology_note": "A personalized escalation apology message for the guest",
  "internal_notes": "Brief justification for the elevated compensation"
}
"""


# ── Helper ─────────────────────────────────────────────────────────────────────
def _load_customer_profile() -> str:
    """Load the customer profile text file."""
    try:
        return Path(CUSTOMER_PROFILE_PATH).read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("Customer profile not found at %s", CUSTOMER_PROFILE_PATH)
        return "(no customer profile available)"


def _build_tier_1_response(room: str) -> dict:
    """Return the fixed Tier 1 perk package."""
    return {
        **TIER_1_PERKS,
        "room": room,
    }


def _build_tier_2_response(fault_event: dict) -> dict:
    """Call the LLM to recommend Tier 2 perks for an unresolved escalation."""
    customer_profile = _load_customer_profile()

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
        f"The guest already received Tier 1 perks (WiFi refund + bar item). "
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


@app.post("/fault-event")
def fault_event():
    """Receive a fault-event webhook from the network agent and issue perks.

    Tier is determined by the 'tier' field in the payload:
      - tier=1 (default): Fault detected — issue immediate fixed perks
      - tier=2: Fault escalated (unresolved) — LLM recommends higher-level perks
    """
    data = request.get_json(force=True)
    room = data.get("room", "unknown")
    tier = data.get("tier", 1)
    fault_type = data.get("fault_type", "unknown")

    log.info(
        "Received fault event — room=%s tier=%d fault_type=%s",
        room,
        tier,
        fault_type,
    )

    if tier == 2:
        result = _build_tier_2_response(data)
    else:
        result = _build_tier_1_response(room)

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


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Perk Agent starting on port 8081")
    app.run(host="0.0.0.0", port=8081)
