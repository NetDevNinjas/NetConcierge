---
title: NetConcierge Agent Communication
version: 0.1
---

# NetConcierge Agent Communication

## Fault Event Webhook (netconcierge-agent → perk-agent)

Trigger: When the LLM calls the escalate tool as its final action.

Request:

```bash
POST http://perk-agent:8081/fault-event
Content-Type: application/json

{
  "status": "resolved" | "escalated",
  "fault_type": "latency" | "loss" | "blackhole" | "unknown",
  "active_path": "a" | "b" | "unknown",
  "resolved_by": "switch_active_path" | "clear_faults" | null,
  "turns_used": <int>,
  "room": "412",
  "summary": "<one-sentence description of what happened>",
  "timestamp": "<ISO8601>",
  "history": [
    {"turn": 1, "tool": "get_router_state", "input": {"path": "all"}, "result": "active_path: a\n..."},
    {"turn": 2, "tool": "clear_faults",      "input": {"path": "a"},   "result": "{\"path\": \"a\", \"status\": \"cleared\"}"},
    ...
    {"turn": N, "tool": "escalate",          "input": {...},           "result": "Webhook delivered: HTTP 200"}
  ]
}
```

The history array is the full diagnostic tool-use trace — every tool call and its result.

Response from perk-agent:

```json
{
  "status": "recommendation_generated",
  "room": "412",
  "recommendation": {
    "primary_perk": {"type": "...", "value": "...", "reason": "..."},
    "secondary_perk": {"type": "...", "value": "...", "reason": "..."} | null,
    "apology_note": "<personalized message for the guest>",
    "internal_notes": "<justification>"
  }
}
```

The netconcierge-agent does not use this response — it fires and forgets (no code reads the return value). The perk-agent logs the recommendation but doesn't forward it anywhere (the FORWARD_URL block is commented out).

**What perk-agent does with the webhook**

After receiving the fault event, it makes its own LLM call:

```bash
POST https://llmgw.codefest2026.marriott.com/v1/chat/completions
model: nova-pro  (from LLM_MODEL env var)

System: "You are NetConcierge's Customer Experience Agent..."
User:
  ## Network Fault Event
  - Status: resolved
  - Fault Type: unknown
  - Summary: <from agent>
  - Room: 412
  - Active Path: b
  - Turns Used (proxy for disruption duration): 7
  - Timestamp: <ISO8601>

  ## Customer Profile
  Guest Name: Sarah Mitchell
  Room: 412
  Loyalty Tier: Gold (3+ years)
  ... (full customer_profile.txt)
  ```
