---
title: NetConcierge Agent Communication Steps
version: 0.1
status: proposal
---

# NetConcierge Agent Communication Steps

1. Detection of an issue
  - agent will detect a degradation (periodic health polling?)
  - guest contacts perk agent reporting a problem and requesting assistance (shouldn't usually happen, but we should be prepared for it)
2. agent contacts perk-agent with a statement of the issue
3. perk-agent communicates with guest
4. agent gives updates to perk-agent as it troubleshoots (important state changes)
5. if no update in 15 minutes, perk-agent polls agent for update, agent replies with status
6. Resolution
  - when issue is resolved, agent reports resolution to perk-agent, perk-agent communicates with guest
  - If agent cannot resolve, it reports that to perk-agent and escalates to human operator
  - If resolution takes more than x minutes, perk-agent goes into 2nd level guest compensation mode (perks become more generous, guest is informed of the delay and compensation, and human operator is alerted to the issue)
