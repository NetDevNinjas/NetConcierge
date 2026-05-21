---
title: NetConcierge Agent Communication
version: 0.2
---

# NetConcierge Agent Communication

## Communication Flow

```
fault detected
     │
     ▼
agent ──POST /fault-event (tier=1, status=detected)──► perk-agent  ← tier-1 perks issued immediately
     │                                                       │
     │  (each tool call)                                     │  (if silent > 15 min)
     ├──POST /fault-update ──────────────────────────► perk-agent  ← timer reset
     │                                                       │
     │                                              GET /status ──► agent  ← current turn/state
     │
     ▼ (loop complete)
     ├── resolved ──POST /fault-event (tier=1, status=resolved)──► perk-agent  ← logs resolution
     └── escalated ─POST /fault-event (tier=2, status=escalated)─► perk-agent  ← LLM tier-2 perks

guest ──POST /guest-report ──────────────────────────► perk-agent  ← queries agent /status
```

---

## Endpoint Reference

### Health checks (Docker `depends_on` / liveness)

```bash
GET http://agent:8080/health
# {"status": "ok", "busy": false}

GET http://perk-agent:8081/health
# {"status": "ok", "service": "perk-agent"}
```

---

### Status queries

```bash
# Agent current state (also polled by perk-agent after 15 min silence)
curl -s http://localhost:8080/status | jq .
# {"status": "idle|running|cooldown", "fault_detected_at": "...", "current_turn": 3,
#  "last_tool": "clear_faults", "room": "412"}

# Perk-agent active fault tracking
curl -s http://localhost:8081/status | jq .
# {"service": "perk-agent", "active_faults": {"412": {"detected_at": "...", "last_update": 1234567890, "turn_count": 3}}}
```

---

### Step 1 — Fault detected (agent → perk-agent, automatic)

Fires immediately when the agent detects FAULT_THRESHOLD consecutive errors, before the LLM loop starts.

```bash
curl -s -X POST http://localhost:8081/fault-event \
  -H "Content-Type: application/json" \
  -d '{
    "tier": 1,
    "status": "detected",
    "room": "412",
    "consecutive_errors": 5,
    "summary": "Network disruption detected: consecutive guest request failures.",
    "timestamp": "2026-05-21T16:00:00Z"
  }' | jq .
# perk-agent: opens fault tracking, issues tier-1 fixed perks, logs guest message
# {"tier": 1, "perks": [...], "apology_note": "...", "room": "412"}
```

**Tier-1 fixed perks (always issued at detection):**
- Full WiFi bill refund for today
- Complimentary drink or appetizer at the hotel bar

---

### Steps 3–4 — In-progress updates (agent → perk-agent, once per tool call)

```bash
curl -s -X POST http://localhost:8081/fault-update \
  -H "Content-Type: application/json" \
  -d '{
    "room": "412",
    "turn": 3,
    "tool": "clear_faults",
    "status": "in_progress",
    "timestamp": "2026-05-21T16:00:30Z"
  }' | jq .
# {"status": "acknowledged"}
# perk-agent resets its 15-minute silence timer
```

---

### Step 5 — Perk-agent polls agent (automatic, after 15 min silence)

Background thread in perk-agent fires every 60s and checks if any active fault has had no
`/fault-update` for `UPDATE_TIMEOUT_SECS` (default 900s / 15 min).

```bash
# Called internally by perk-agent; can be tested manually:
curl -s http://localhost:8080/status | jq .
```

---

### Step 6a — Resolved (agent → perk-agent, automatic)

```bash
curl -s -X POST http://localhost:8081/fault-event \
  -H "Content-Type: application/json" \
  -d '{
    "tier": 1,
    "status": "resolved",
    "room": "412",
    "fault_type": "blackhole",
    "summary": "Fault cleared by switching to path b.",
    "active_path": "b",
    "resolved_by": "switch_active_path",
    "turns_used": 7,
    "timestamp": "2026-05-21T16:01:00Z"
  }' | jq .
# perk-agent: closes fault tracking, logs resolution — no additional perks (tier-1 already issued)
# {"tier": 1, "status": "resolved", "room": "412", "message": "Issue resolved — tier-1 perks already issued, no further action."}
```

---

### Step 6b — Escalated / unresolved (agent → perk-agent, automatic)

```bash
curl -s -X POST http://localhost:8081/fault-event \
  -H "Content-Type: application/json" \
  -d '{
    "tier": 2,
    "status": "escalated",
    "room": "412",
    "fault_type": "blackhole",
    "summary": "Agent exhausted all turns without resolving the fault.",
    "active_path": "unknown",
    "turns_used": 8,
    "timestamp": "2026-05-21T16:01:00Z"
  }' | jq .
# perk-agent: closes fault tracking, calls LLM for elevated tier-2 perks
# {"tier": 2, "room": "412", "recommendation": {"primary_perk": {...}, "secondary_perk": {...}, "apology_note": "..."}}
```

**Tier-2 LLM-recommended perks (escalations only, stacks on top of tier-1):**
- Complimentary dinner for two (up to $150)
- Spa credit ($50–$150)
- Complimentary room night
- Room upgrade for remainder of stay
- Loyalty points bonus (1000–5000)
- Late checkout + early check-in next visit
- Percentage discount on current bill (10–25%)

---

### Guest-initiated contact (guest → perk-agent)

```bash
curl -s -X POST http://localhost:8081/guest-report \
  -H "Content-Type: application/json" \
  -d '{
    "room": "412",
    "message": "My WiFi has been down for 10 minutes"
  }' | jq .
# perk-agent: queries GET agent:8080/status, logs agent state
# {"status": "received", "room": "412", "message": "We are investigating your issue and will update you shortly."}
```

---

## What perk-agent does on tier-2 escalation

Calls the LLM with the fault event + customer profile:

```
POST https://llmgw.codefest2026.marriott.com/v1/chat/completions
model: nova-pro

System: "You are NetConcierge's Customer Experience Agent. A network fault could NOT be
         automatically resolved... Guest already received tier-1 perks (WiFi refund + bar item)."
User:
  ## Escalated Network Fault (Unresolved)
  - Fault Type: blackhole
  - Summary: Agent exhausted all turns without resolving the fault.
  - Room: 412
  - Turns Used: 8
  - Timestamp: 2026-05-21T16:01:00Z

  ## Customer Profile
  Guest Name: Sarah Mitchell
  Loyalty Tier: Gold (3+ years)
  Current Booking Value: $489/night (Executive Suite, 4 nights)
  Special Notes: Celebrating promotion; mentioned needing reliable WiFi for presentations
  ... (full customer_profile.txt)
```

---

## Environment variables

| Variable              | Default                              | Service    | Purpose                                           |
|-----------------------|--------------------------------------|------------|---------------------------------------------------|
| `PERK_AGENT_URL`      | `http://perk-agent:8081`             | agent      | Base URL for all perk-agent notifications         |
| `WEBHOOK_URL`         | `http://perk-agent:8081/fault-event` | agent      | Final escalate destination                        |
| `AGENT_URL`           | `http://agent:8080`                  | perk-agent | Used for `/guest-report` status query and polling |
| `UPDATE_TIMEOUT_SECS` | `900` (15 min)                       | perk-agent | Silence threshold before polling agent            |
| `LOOP_COOLDOWN`       | `60`                                 | agent      | Seconds between agent loop runs                   |
