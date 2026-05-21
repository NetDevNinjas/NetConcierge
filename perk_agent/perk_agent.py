"""NetConcierge Perk Agent.

Receives fault-event webhooks from the network troubleshooting agent and uses
an LLM to recommend perks or refunds based on the customer profile and the
nature/severity of the network disruption experienced.
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

SYSTEM_PROMPT = """\
You are NetConcierge's Customer Experience Agent for a luxury hotel.

Your role is to recommend appropriate perks, credits, or refunds to guests who
experienced network disruptions during their stay. You balance guest satisfaction
with business sustainability.

Given:
- A customer profile (loyalty tier, stay history, booking value, etc.)
- Details about the network fault they experienced (type, duration, resolution status)

Recommend ONE primary perk and optionally ONE secondary perk from this menu:
- Complimentary late checkout
- Free room upgrade (next stay)
- Spa credit ($25–$100 depending on severity)
- Dining credit ($20–$75 depending on severity)
- Partial WiFi refund (if WiFi was a paid add-on)
- Loyalty points bonus (500–2000 points)
- Complimentary night (only for severe, unresolved outages affecting high-tier guests)
- Written apology from management

Guidelines:
- Higher loyalty tier → more generous perks
- Unresolved faults (status=escalated) → more generous than resolved ones
- Longer disruptions (more turns_used) suggest longer outages → more generous
- Business travelers needing WiFi → prioritize connectivity-related compensation
- Consider the guest's history and special circumstances

Respond with a JSON object:
{
  "primary_perk": {"type": "...", "value": "...", "reason": "..."},
  "secondary_perk": {"type": "...", "value": "...", "reason": "..."} or null,
  "apology_note": "A brief personalized apology message for the guest",
  "internal_notes": "Brief justification for the recommendation"
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


def _get_recommendation(fault_event: dict) -> dict:
    """Call the LLM with fault context + customer profile to get a perk recommendation."""
    customer_profile = _load_customer_profile()

    user_message = (
        f"## Network Fault Event\n"
        f"- Status: {fault_event.get('status', 'unknown')}\n"
        f"- Fault Type: {fault_event.get('fault_type', 'unknown')}\n"
        f"- Summary: {fault_event.get('summary', 'No summary provided')}\n"
        f"- Room: {fault_event.get('room', 'unknown')}\n"
        f"- Active Path: {fault_event.get('active_path', 'unknown')}\n"
        f"- Turns Used (proxy for disruption duration): {fault_event.get('turns_used', 0)}\n"
        f"- Timestamp: {fault_event.get('timestamp', 'unknown')}\n\n"
        f"## Customer Profile\n"
        f"{customer_profile}\n\n"
        f"Please recommend appropriate perks or compensation for this guest."
    )

    try:
        response = _llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
        )
        content = response.choices[0].message.content

        # Try to parse as JSON; fall back to raw text
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {"raw_recommendation": content}

    except Exception as exc:
        log.error("LLM API call failed: %s", exc)
        return {"error": str(exc), "fallback": "Offer standard apology and 500 loyalty points"}


# ── Flask endpoints ────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "perk-agent"})


@app.post("/fault-event")
def fault_event():
    """Receive a fault-event webhook from the network agent and recommend perks."""
    data = request.get_json(force=True)
    room = data.get("room", "unknown")
    status = data.get("status", "unknown")
    fault_type = data.get("fault_type", "unknown")

    log.info(
        "Received fault event — room=%s status=%s fault_type=%s",
        room,
        status,
        fault_type,
    )

    recommendation = _get_recommendation(data)

    log.info("═" * 60)
    log.info("PERK RECOMMENDATION for Room %s", room)
    log.info("═" * 60)
    log.info(json.dumps(recommendation, indent=2))
    log.info("═" * 60)

    # ── Optional: Forward recommendation to an external system ──────────────
    # Uncomment the block below to POST the recommendation to another service
    # (e.g., a guest-facing app, CRM, Slack channel, or notification service).
    #
    # forward_payload = {
    #     "room": room,
    #     "fault_status": status,
    #     "fault_type": fault_type,
    #     "recommendation": recommendation,
    # }
    # try:
    #     forward_url = os.environ.get("FORWARD_URL", "http://localhost:9000/recommendations")
    #     resp = requests.post(forward_url, json=forward_payload, timeout=10)
    #     log.info("Forwarded recommendation to %s — HTTP %d", forward_url, resp.status_code)
    # except Exception as exc:
    #     log.warning("Failed to forward recommendation: %s", exc)

    return jsonify({
        "status": "recommendation_generated",
        "room": room,
        "recommendation": recommendation,
    })


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Perk Agent starting on port 8081")
    app.run(host="0.0.0.0", port=8081)
