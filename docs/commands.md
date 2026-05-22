---
title: Commands
version: 0.1
---

# Commands to make changes to the system

## Rebuild agents only

```bash
ssh ubuntu@netconcierge "cd /opt/netconcierge && sudo git pull origin main && sudo docker compose build agent perk-agent && sudo docker compose up -d agent perk-agent"
```

## Rebuild everything

```bash
ssh ubuntu@netconcierge "cd /opt/netconcierge && sudo git pull origin main && sudo docker compose build && sudo docker compose up"
```
