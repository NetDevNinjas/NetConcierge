---
title: API Calls
version: 0.1
---

# API Calls

```bash
ubuntu@ip-10-0-1-79:/opt/netconcierge$ curl -s http://localhost:8081/status | jq .
{
  "active_faults": {},
  "service": "perk-agent"
}
ubuntu@ip-10-0-1-79:/opt/netconcierge$ curl -s -X POST http://localhost:8081/fault-update \
  -H "Content-Type: application/json" \
  -d '{"room": "412", "turn": 3, "tool": "clear_faults", "status": "in_progress"}' | jq .
{
  "status": "acknowledged"
}
ubuntu@ip-10-0-1-79:/opt/netconcierge$ curl -s -X POST http://localhost:8081/guest-report \
  -H "Content-Type: application/json" \
  -d '{"room": "412", "message": "My WiFi has been down for 10 minutes"}' | jq .
{
  "message": "We are investigating your issue and will update you shortly.",
  "room": "412",
  "status": "received"
}
ubuntu@ip-10-0-1-79:/opt/netconcierge$ curl -s -X POST http://localhost:8081/fault-event \
  -H "Content-Type: application/json" \
  -d '{"tier": 1, "status": "detected", "room": "412", "consecutive_errors": 5, "summary": "Network disruption detected.", "timestamp": "2026-05-21T16:00:00Z"}' | jq .
{
  "apology_note": "We sincerely apologize for the WiFi disruption. We've refunded today's WiFi charges and would love to offer you a complimentary drink or appetizer at our lobby bar while our team works to restore full service.",
  "perks": [
    {
      "description": "Guest's WiFi charges for the current day are fully refunded.",
      "type": "wifi_refund",
      "value": "Full WiFi bill refund for today"
    },
    {
      "description": "Guest may redeem one complimentary drink or appetizer at the lobby bar.",
      "type": "complimentary_bar_item",
      "value": "Free drink or appetizer at the hotel bar"
    }
  ],
  "room": "412",
  "tier": 1
}
ubuntu@ip-10-0-1-79:/opt/netconcierge$ curl -s -X POST http://localhost:8081/fault-event \
  -H "Content-Type: application/json" \
  -d '{"tier": 1, "status": "resolved", "room": "412", "fault_type": "blackhole", "summary": "Fault cleared by switching to path b.", "active_path": "b", "turns_used": 7}' | jq .
{
  "message": "Issue resolved — tier-1 perks already issued, no further action.",
  "room": "412",
  "status": "resolved",
  "tier": 1
}
ubuntu@ip-10-0-1-79:/opt/netconcierge$ curl -s -X POST http://localhost:8081/fault-event \
  -H "Content-Type: application/json" \
  -d '{"tier": 2, "status": "escalated", "room": "412", "fault_type": "blackhole", "summary": "Agent exhausted all turns without resolving.", "active_path": "unknown", "turns_used": 8}' | jq .
{
  "recommendation": {
    "raw_recommendation": "```json\n{\n  \"primary_perk\": {\n    \"type\": \"Complimentary dinner for two at the hotel restaurant\",\n    \"value\": \"up to $150\",\n    \"reason\": \"Given the extended network disruption and Sarah's need for reliable WiFi for her business presentations, a high-value dining experience will help mitigate the inconvenience.\"\n  },\n  \"secondary_perk\": {\n    \"type\": \"Loyalty points bonus\",\n    \"value\": \"3000 points\",\n    \"reason\": \"As a Gold-tier member with a significant lifetime spend, an additional points bonus will further acknowledge her loyalty and the disruption caused.\"\n  },\n  \"apology_note\": \"Dear Ms. Mitchell,\\n\\nWe sincerely apologize for the extended network disruption you've experienced, especially during your important business conference. Your comfort and connectivity are our top priorities, and we deeply regret the inconvenience this has caused. As a token of our appreciation for your understanding and loyalty, we would like to offer you a complimentary dinner for two at our hotel restaurant, up to $150, and an additional 3000 loyalty points. We hope these gestures help make your stay more enjoyable.\\n\\nWarm regards,\\n[Your Name]\\nCustomer Experience Agent\",\n  \"internal_notes\": \"Sarah Mitchell is a Gold-tier guest with a high lifetime spend and significant booking value. The network fault has been unresolved after multiple attempts, and she requires reliable WiFi for her business presentations. The primary perk is a high-value dinner to compensate for the disruption, and the secondary perk is a substantial points bonus to recognize her loyalty.\"\n}\n```"
  },
  "room": "412",
  "tier": 2
}
```
