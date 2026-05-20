# NetConcierge — Network Implementation Guide

## Overview

This document covers the step-by-step build of the simulated hotel network used by the NetConcierge
troubleshooting agent. The topology runs entirely inside Docker Compose on a single EC2 instance and
is intentionally simple — it does not need to represent a production network closely, only provide
enough fidelity to demonstrate autonomous fault detection, healing, and escalation.

The guest-facing LLM component is a separate service (handled by a teammate). This guide covers
everything from the EC2 instance through the Docker network topology, fault injection scripts,
monitoring, and the webhook contract that connects the two halves of the system.

---

## Architecture

```
                        ┌─────────────────────────────────────┐
                        │           EC2 Instance               │
                        │                                      │
  front-net             │  path-a-net (172.21.0.0/24)         │
  172.20.0.0/24         │  ┌──────────────────────────────┐   │
  ┌────────┐            │  │  [path-a interface]           │   │
  │ client │────────────┼──│─────────────>[webserver]      │   │
  └────────┘  [router]  │  └──────────────────────────────┘   │
                   │    │                                      │
                   │    │  path-b-net (172.22.0.0/24)         │
                   │    │  ┌──────────────────────────────┐   │
                   │    │  │  [path-b interface]           │   │
                   └────┼──│─────────────>[webserver]      │   │
                        │  └──────────────────────────────┘   │
                        │                                      │
                        │  [agent] ──── docker.sock            │
                        │  [uptime-kuma] ── polls HTTP         │
                        └─────────────────────────────────────┘
```

**Containers:**

| Container    | Networks                              | Purpose                                      |
|--------------|---------------------------------------|----------------------------------------------|
| `client`     | `front-net`                           | Simulates a hotel guest device; HTTP loop    |
| `router`     | `front-net`, `path-a-net`, `path-b-net` | Alpine + iptables/tc; fault injection target |
| `webserver`  | `path-a-net`, `path-b-net`            | nginx; simulates hotel internet portal       |
| `agent`      | `front-net` + docker socket           | Python/TIP.ai LLM troubleshooting loop       |
| `uptime-kuma`| host port 3001                        | External monitoring / demo status board      |

**Docker Networks:**

| Network       | Subnet           | Members              |
|---------------|------------------|----------------------|
| `front-net`   | 172.20.0.0/24    | client, router       |
| `path-a-net`  | 172.21.0.0/24    | router, webserver    |
| `path-b-net`  | 172.22.0.0/24    | router, webserver    |

---

## Phase 1 — AWS Infrastructure

### Steps

1. **Launch EC2 instance**
   - AMI: Ubuntu 24.04 LTS (or Amazon Linux 2023)
   - Instance type: `t3.medium`
   - Storage: 20 GB gp3
   - Place in a public subnet with auto-assign public IP enabled
   - Tag: `Name=netconcierge-demo`

2. **Configure Security Group** — inbound rules:

   | Port  | Protocol | Source        | Purpose                  |
   |-------|----------|---------------|--------------------------|
   | 22    | TCP      | Team IPs only | SSH                      |
   | 3001  | TCP      | Team IPs only | Uptime Kuma UI           |
   | 8080  | TCP      | Team IPs only | Agent API / demo UI      |
   | -1    | ICMP     | Team IPs only | Ping / diagnostics       |

3. **Bootstrap the instance** — run `infra/setup-ec2.sh`:

   ```bash
   #!/usr/bin/env bash
   set -euo pipefail

   apt-get update -y
   apt-get install -y docker.io docker-compose-plugin git

   systemctl enable --now docker
   usermod -aG docker ubuntu

   git clone https://github.com/<org>/NetConcierge.git /opt/netconcierge
   cd /opt/netconcierge
   cp .env.example .env
   # Edit .env and fill in TIP_API_KEY, ARTIFACTORY credentials, and WEBHOOK_URL before starting
   ```

4. **Set environment variables** — create `.env` in the project root (this file is gitignored):

   ```
   TIP_API_KEY=<your-tip-api-key>
   ARTIFACTORY_USERNAME=<your-username>
   ARTIFACTORY_TOKEN=<your-artifactory-token>
   WEBHOOK_URL=http://<teammate-service>/fault-event
   ```

### Phase 1 Tests

- [x] SSH into the EC2 instance successfully
- [x] `docker --version` returns a version ≥ 24
- [x] `docker compose version` returns a version ≥ 2.x
- [x] Security group is allowing expected ports — from your local machine: `nc -zv -w 3 34.193.130.8 22` should print `Connection to 34.193.130.8 22 port [tcp/ssh] succeeded`
- [x] Security group is blocking unexpected ports — from your local machine: `nc -zv -w 3 34.193.130.8 80` should **time out** (not "connection refused" — a timeout means the SG dropped the packet; a refusal would mean no SG rule but nothing listening)

---

## Phase 2 — Docker Compose Network Topology

### File: `docker-compose.yml`

```yaml
name: netconcierge

networks:
  front-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/24
  path-a-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.21.0.0/24
  path-b-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.22.0.0/24

services:

  webserver:
    image: nginx:alpine
    container_name: webserver
    networks:
      path-a-net:
        ipv4_address: 172.21.0.10
      path-b-net:
        ipv4_address: 172.22.0.10
    volumes:
      - ./webserver/html:/usr/share/nginx/html:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost/"]
      interval: 5s
      timeout: 3s
      retries: 3

  router:
    build: ./router
    container_name: router
    cap_add:
      - NET_ADMIN
    sysctls:
      - net.ipv4.ip_forward=1
    networks:
      front-net:
        ipv4_address: 172.20.0.254
      path-a-net:
        ipv4_address: 172.21.0.254
      path-b-net:
        ipv4_address: 172.22.0.254
    ports:
      - "5000:5000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 5s
      timeout: 3s
      retries: 3

  client:
    build: ./client
    container_name: client
    networks:
      front-net:
        ipv4_address: 172.20.0.10
    depends_on:
      router:
        condition: service_healthy
    environment:
      - TARGET_URL=http://172.20.0.254/  # router proxies to webserver

  agent:
    build: ./agent
    container_name: agent
    networks:
      - front-net
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    env_file:
      - .env
    environment:
      - ROUTER_API=http://172.20.0.254:5000
    ports:
      - "8080:8080"
    depends_on:
      router:
        condition: service_healthy

  uptime-kuma:
    image: louislam/uptime-kuma:1
    container_name: uptime-kuma
    volumes:
      - uptime-kuma-data:/app/data
    ports:
      - "3001:3001"
    restart: unless-stopped

volumes:
  uptime-kuma-data:
```

### Phase 2 Tests

- [x] `docker compose config` exits 0 with no warnings
- [x] `docker compose up -d` starts all 5 containers without error
- [x] `docker compose ps` shows all containers as `healthy` (allow up to 60s for healthchecks)
- [x] `docker network ls` shows `netconcierge_front-net`, `netconcierge_path-a-net`, `netconcierge_path-b-net`
- [x] `docker network inspect netconcierge_path-a-net` confirms `webserver` and `router` are members with the expected IPs
- [x] `docker exec client ping -c 3 172.20.0.254` succeeds (client → router)
- [x] `docker exec router ping -c 3 172.21.0.10` succeeds (router → webserver via path-a)
- [x] `docker exec router ping -c 3 172.22.0.10` succeeds (router → webserver via path-b)
- [x] `.env` file exists at repo root and contains both required variables

---

## Phase 3 — Router Container

### File: `router/Dockerfile`

```dockerfile
FROM alpine:3.21

RUN apk add --no-cache \
    iptables \
    iproute2 \
    curl \
    python3 \
    py3-pip \
    bash

RUN pip3 install --no-cache-dir flask --break-system-packages

COPY entrypoint.sh /entrypoint.sh
COPY api.py /api.py
RUN chmod +x /entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
```

### File: `router/entrypoint.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# Enable IP forwarding (also set via sysctls in compose, belt-and-suspenders)
echo 1 > /proc/sys/net/ipv4/ip_forward

# Allow established connections
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# Default: forward client traffic through path-a to webserver
iptables -A FORWARD -i eth0 -o eth1 -j ACCEPT
iptables -A FORWARD -i eth1 -o eth0 -j ACCEPT

# Path-b also permitted (standby; agent can switch active path)
iptables -A FORWARD -i eth0 -o eth2 -j ACCEPT
iptables -A FORWARD -i eth2 -o eth0 -j ACCEPT

# NAT outbound traffic leaving toward webserver
iptables -t nat -A POSTROUTING -o eth1 -j MASQUERADE
iptables -t nat -A POSTROUTING -o eth2 -j MASQUERADE

# Start the router management API in the background
python3 /api.py &

# Keep container alive
tail -f /dev/null
```

### File: `router/api.py`

A minimal Flask API (~60 lines) that lets the agent inspect and modify router state without
shell access. Implement the following endpoints:

| Method | Endpoint               | Purpose                                           |
|--------|------------------------|---------------------------------------------------|
| GET    | `/health`              | Returns `{"status": "ok"}`                        |
| GET    | `/state`               | Returns current iptables FORWARD rules + tc qdiscs |
| GET    | `/state/<path>`        | State for `path-a` or `path-b` only              |
| POST   | `/fault`               | Body: `{"path": "a", "type": "latency\|loss\|blackhole", "value": "200ms"}` |
| DELETE | `/fault/<path>`        | Clears all tc/iptables faults on named path       |
| POST   | `/active-path`         | Body: `{"path": "a\|b"}` — sets preferred path    |

> **Security note:** This API has no authentication — it is reachable only within the Docker
> Compose network and must never be exposed publicly. The security group already blocks port 5000
> from the internet; verify this is the case.

### Phase 3 Tests

- [ ] `curl http://localhost:5000/health` from the EC2 host returns `{"status": "ok"}`
- [ ] `docker exec router iptables -L FORWARD -n` shows the expected ACCEPT rules
- [ ] `docker exec router sysctl net.ipv4.ip_forward` returns `net.ipv4.ip_forward = 1`
- [ ] `curl http://localhost:5000/state` returns JSON with both `path-a` and `path-b` entries
- [ ] `docker exec client curl -s http://172.20.0.254/` returns the nginx welcome page (end-to-end path-a)

---

## Phase 4 — Client Container

### File: `client/Dockerfile`

```dockerfile
FROM alpine:3.21
RUN apk add --no-cache curl bash
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
```

### File: `client/run.sh`

```bash
#!/usr/bin/env bash
TARGET_URL="${TARGET_URL:-http://172.20.0.254/}"
INTERVAL="${INTERVAL:-3}"

while true; do
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$TARGET_URL" || echo "000")
    latency=$(curl -s -o /dev/null -w "%{time_total}" --max-time 5 "$TARGET_URL" 2>/dev/null || echo "timeout")

    if [[ "$http_code" == "200" ]]; then
        echo "[${timestamp}] OK  | HTTP ${http_code} | ${latency}s"
    else
        echo "[${timestamp}] ERR | HTTP ${http_code} | ${latency}s" >&2
    fi

    sleep "$INTERVAL"
done
```

### Phase 4 Tests

- [ ] `docker logs client` shows a continuous stream of `OK | HTTP 200` lines
- [ ] No `ERR` lines appear in the client log under normal (no-fault) conditions
- [ ] `docker logs client --tail 10` updates every ~3 seconds

---

## Phase 5 — Fault Injection Scripts

### File: `scripts/inject-fault.sh`

```bash
#!/usr/bin/env bash
# Usage: ./inject-fault.sh --path <a|b|both> --type <latency|loss|blackhole>
#        Optional: --value <tc delay value, e.g. "300ms 50ms">
set -euo pipefail

PATH_ARG=""
TYPE_ARG=""
VALUE_ARG="300ms 100ms"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --path)  PATH_ARG="$2"; shift 2 ;;
        --type)  TYPE_ARG="$2"; shift 2 ;;
        --value) VALUE_ARG="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

inject_path() {
    local iface="$1"  # eth1 = path-a, eth2 = path-b
    case "$TYPE_ARG" in
        latency)
            docker exec router tc qdisc add dev "$iface" root netem delay $VALUE_ARG
            echo "Injected latency (${VALUE_ARG}) on ${iface}"
            ;;
        loss)
            docker exec router tc qdisc add dev "$iface" root netem loss 40%
            echo "Injected 40% packet loss on ${iface}"
            ;;
        blackhole)
            docker exec router iptables -I FORWARD -i "$iface" -j DROP
            docker exec router iptables -I FORWARD -o "$iface" -j DROP
            echo "Blackholed ${iface}"
            ;;
        *)
            echo "Unknown fault type: $TYPE_ARG"; exit 1 ;;
    esac
}

case "$PATH_ARG" in
    a)    inject_path eth1 ;;
    b)    inject_path eth2 ;;
    both) inject_path eth1; inject_path eth2 ;;
    *)    echo "--path must be a, b, or both"; exit 1 ;;
esac
```

### File: `scripts/restore.sh`

```bash
#!/usr/bin/env bash
# Clears all injected faults and restores clean routing state
set -euo pipefail

for iface in eth1 eth2; do
    docker exec router tc qdisc del dev "$iface" root 2>/dev/null && \
        echo "Cleared tc qdisc on ${iface}" || \
        echo "No tc qdisc on ${iface} (already clean)"
done

# Flush only the injected DROP rules (not the baseline ACCEPT rules)
docker exec router iptables -D FORWARD -i eth1 -j DROP 2>/dev/null || true
docker exec router iptables -D FORWARD -o eth1 -j DROP 2>/dev/null || true
docker exec router iptables -D FORWARD -i eth2 -j DROP 2>/dev/null || true
docker exec router iptables -D FORWARD -o eth2 -j DROP 2>/dev/null || true

echo "Fault state cleared."
```

### Phase 5 Tests

- [ ] `./scripts/inject-fault.sh --path a --type latency --value "500ms 100ms"` exits 0
- [ ] `docker logs client --tail 5` shows latency values above 500ms (or ERR if latency > 5s timeout)
- [ ] `./scripts/restore.sh` exits 0 and client log returns to `OK` within ~10s
- [ ] `./scripts/inject-fault.sh --path a --type blackhole` causes client log to switch to `ERR | HTTP 000`
- [ ] `./scripts/restore.sh` clears the blackhole and client recovers
- [ ] `./scripts/inject-fault.sh --path both --type blackhole` causes sustained errors (dual-fail scenario)
- [ ] `./scripts/restore.sh` after dual-fail restores connectivity

---

## Phase 6 — LLM Troubleshooting Agent

### File: `agent/Dockerfile`

```dockerfile
FROM python:3.12-slim

# Artifactory credentials are passed as build args so they never land in a layer
ARG ARTIFACTORY_USERNAME
ARG ARTIFACTORY_TOKEN

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --index-url "https://${ARTIFACTORY_USERNAME}:${ARTIFACTORY_TOKEN}@artifactory.marriott.com/artifactory/api/pypi/emergingtech-pypi-local/simple/" \
    --extra-index-url https://pypi.org/simple/ \
    -r requirements.txt
COPY agent.py .
EXPOSE 8080
CMD ["python", "agent.py"]
```

The `ARTIFACTORY_USERNAME` and `ARTIFACTORY_TOKEN` build args are passed via the `agent` service
`build.args` block in `docker-compose.yml` (loaded from `.env` at build time).

### File: `agent/requirements.txt`

```
tip-sdk
docker>=7.0
flask>=3.0
requests>=2.31
```

### Agent Design

The agent runs two concurrent threads:
1. **Poll loop** — every 10 seconds, checks client HTTP success rate over the last 30s
2. **Flask health endpoint** — `GET /health` for Uptime Kuma

On detecting failures (≥3 consecutive `HTTP 000` or non-200 responses from client logs), the
agent enters a tool-calling loop with a maximum of 8 turns.

**LLM client** — uses the TIP.ai SDK (Marriott's internal LLM provisioner, backed by LiteLLM):

```python
from tip_sdk import TipClient

client = TipClient()  # reads TIP_API_KEY from environment
response = client.chat.completions.create(
    model="anthropic.claude-sonnet-4-20250514-v1:0",
    messages=messages,
    tools=tool_definitions,  # OpenAI-compatible tool schema
)
```

The interface is OpenAI-compatible — tools are defined as JSON schema objects and
tool calls come back in `response.choices[0].message.tool_calls`.

**LLM client** — uses the TIP.ai SDK (Marriott's internal LLM provisioner, backed by LiteLLM):

```python
from tip_sdk import TipClient

client = TipClient()  # reads TIP_API_KEY from environment
response = client.chat.completions.create(
    model="anthropic.claude-sonnet-4-20250514-v1:0",
    messages=messages,
    tools=tool_definitions,  # OpenAI-compatible tool schema
)
```

The interface is OpenAI-compatible — tools are defined as JSON schema objects and
tool calls come back in `response.choices[0].message.tool_calls`.

**Tools exposed to the LLM:**

| Tool name            | What it does                                                               |
|----------------------|----------------------------------------------------------------------------|
| `ping_host`          | `docker exec client ping -c 4 <host>` — basic reachability                |
| `curl_endpoint`      | `docker exec client curl -sv <url>` — HTTP check with timing              |
| `get_router_state`   | `GET /state/<path>` on router API — returns tc and iptables state          |
| `restart_container`  | Docker SDK `container.restart(name)` — restarts a named container         |
| `switch_active_path` | `POST /active-path` on router API — switches preferred path               |
| `clear_faults`       | `DELETE /fault/<path>` on router API — clears tc/iptables faults          |
| `escalate`           | Formats troubleshooting history and POSTs to `WEBHOOK_URL`                |

**Escalation payload** (sent to the guest-facing LLM service):

```json
{
  "status": "detected | resolved | escalated",
  "fault_type": "latency | loss | blackhole | unknown",
  "active_path": "a | b | both",
  "resolved_by": "switch_path | clear_faults | restart | null",
  "turns_used": 5,
  "history": [
    {"turn": 1, "tool": "ping_host", "input": {"host": "172.21.0.10"}, "result": "..."},
    {"turn": 2, "tool": "get_router_state", "input": {"path": "a"}, "result": "..."}
  ],
  "timestamp": "2026-05-20T14:23:00Z"
}
```

> When `status` is `resolved`, the webhook still fires so the guest-facing LLM can inform
> guests that the issue has been corrected. When `status` is `escalated`, the history is
> included so the guest LLM has full context.

### Phase 6 Tests

- [ ] `docker logs agent` shows `Agent polling...` lines every 10s under normal conditions
- [ ] `curl http://localhost:8080/health` returns `{"status": "ok"}`
- [ ] Inject a blackhole on path-a: within 30s, agent logs show fault detection and tool loop start
- [ ] Agent calls `get_router_state` (visible in logs) before attempting a fix
- [ ] Agent calls `switch_active_path` or `clear_faults` and client recovers
- [ ] `docker logs client` returns to `OK` lines after agent resolves the fault
- [ ] Webhook POST is sent with `status: resolved` and visible in teammate's service logs
- [ ] Inject dual-path blackhole: agent exhausts 8 turns and sends `status: escalated` webhook
- [ ] Escalation payload contains full `history` array with all tool calls

---

## Phase 7 — Uptime Kuma Monitoring

### Setup steps

1. Access Uptime Kuma at `http://<EC2-IP>:3001` and complete first-time setup (create admin account).
2. Add the following monitors:

   | Monitor Name        | Type | URL / Host                    | Interval |
   |---------------------|------|-------------------------------|----------|
   | Path A → Webserver  | HTTP | `http://172.21.0.10/`         | 30s      |
   | Path B → Webserver  | HTTP | `http://172.22.0.10/`         | 30s      |
   | Client Target (via Router) | HTTP | `http://172.20.0.254/` | 30s |
   | Agent Health        | HTTP | `http://agent:8080/health`    | 30s      |
   | Webserver Direct    | HTTP | `http://webserver/`           | 30s      |

   > **Note:** Uptime Kuma runs on the host network by default. To monitor container IPs directly,
   > either add `uptime-kuma` to the relevant docker networks in `docker-compose.yml`, or use
   > container names (which resolve via Docker's internal DNS when on the same network).

3. Enable the **Status Page** feature in Uptime Kuma and add all monitors to it.
   Share the status page URL — this is the "external visibility" screen for the demo.

### Phase 7 Tests

- [ ] Uptime Kuma UI is accessible at `http://<EC2-IP>:3001`
- [ ] All 5 monitors show green / `Up` under normal conditions
- [ ] Inject a blackhole on path-a: "Path A → Webserver" monitor turns red within 60s
- [ ] Restore fault: monitor returns to green within 60s
- [ ] Status page URL loads without authentication and shows all monitors

---

## Phase 8 — Integration Test (Full Demo Run-Through)

This is the end-to-end validation. Run this before the presentation.

### Pre-conditions

- [ ] All containers are `healthy`: `docker compose ps`
- [ ] Uptime Kuma shows all green
- [ ] `docker logs client --tail 5` shows all `OK` lines
- [ ] Teammate's guest-facing LLM service is running and the `WEBHOOK_URL` is reachable

### Scenario 1 — Single path degraded (agent self-heals)

- [ ] Run: `./scripts/inject-fault.sh --path a --type blackhole`
- [ ] Uptime Kuma "Path A → Webserver" turns red
- [ ] Agent detects failure within 30s (check `docker logs agent`)
- [ ] Agent switches to path-b or clears fault (tool calls visible in logs)
- [ ] `docker logs client` returns to `OK` without manual intervention
- [ ] Webhook received by teammate's service with `status: resolved`
- [ ] Uptime Kuma returns to all-green

### Scenario 2 — Latency degradation (partial fault)

- [ ] Run: `./scripts/inject-fault.sh --path a --type latency --value "2000ms 500ms"`
- [ ] `docker logs client` shows high latency values (or intermittent `ERR` on timeout)
- [ ] Agent detects degradation and clears the tc fault
- [ ] Client latency returns to normal
- [ ] Run `./scripts/restore.sh` as cleanup if agent doesn't catch it

### Scenario 3 — Total failure / escalation

- [ ] Run: `./scripts/inject-fault.sh --path both --type blackhole`
- [ ] `docker logs client` shows sustained `ERR | HTTP 000`
- [ ] Uptime Kuma shows all path monitors red
- [ ] Agent works through 8 turns and cannot resolve (verify in logs)
- [ ] Webhook received with `status: escalated` and populated `history` array
- [ ] Guest-facing LLM service receives escalation and (teammate's concern) notifies guest
- [ ] Run `./scripts/restore.sh` to clean up after demo

---

## Project File Structure

```
NetConcierge/
├── .env.example                  # Template — copy to .env, fill in secrets
├── .gitignore                    # Must include .env
├── docker-compose.yml
├── infra/
│   └── setup-ec2.sh
├── router/
│   ├── Dockerfile
│   ├── entrypoint.sh
│   └── api.py
├── client/
│   ├── Dockerfile
│   └── run.sh
├── webserver/
│   └── html/
│       └── index.html            # Simple "Hotel WiFi Portal" page
├── agent/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── agent.py
├── scripts/
│   ├── inject-fault.sh
│   └── restore.sh
├── monitoring/
│   └── uptime-kuma/              # Persistent volume mount point
└── docs/
    ├── claude-initial-conversation.md
    └── NETWORK_IMPLEMENTATION.md  # This file
```

---

## Key Constraints & Reminders

- **`TIP_API_KEY`, `ARTIFACTORY_USERNAME`, and `ARTIFACTORY_TOKEN`** must never be committed to git. The `.env` file must be in `.gitignore`. Retrieve all three from gopass before building.
- **Docker socket mount** on the agent container is a privileged operation. It is acceptable for a
  demo but would never be done in production. Flag this when presenting.
- **`NET_ADMIN` capability** is scoped only to the `router` container.
- **Port 5000** (router API) must not be exposed publicly — verify the security group blocks it.
- The router's `entrypoint.sh` assumes `eth1` = path-a interface and `eth2` = path-b interface.
  Docker assigns interfaces in network attachment order. If you see routing issues, run
  `docker exec router ip link` to verify interface names and adjust the script accordingly.
- Terminate the EC2 instance after the presentation to avoid ongoing charges (~$0.04/hr for t3.medium).
