---
title: NetConcierge Developer Guide
description: Complete developer documentation for NetConcierge autonomous troubleshooting & compensation system
layout: default
---

# NetConcierge — Developer Guide

Welcome to the **NetConcierge** Developer Guide! This documentation is specifically designed for developers, systems architects, and Marriott engineers who want to understand, build upon, or troubleshoot the NetConcierge system.

For a non-technical overview and the end-to-end guest experience story, please refer to the [NetConcierge Guest Experience Demo](DEMO.htm).

---

## 1. System Overview & Core Value

NetConcierge is an intelligent, autonomous network troubleshooting and hospitality compensation agent. Built for hotel guest WiFi environments, it bridges the gap between **network reliability engineering** and **guest relationship management (CRM)**:

*   **Autonomous Fault Detection:** Continuously polls and monitors Guest WiFi performance metrics (latency, packet loss, outages).
*   **Intelligent Self-Healing:** Employs an LLM-driven troubleshooting loop that executes diagnostics (pings, curls) and takes corrective action (clearing faults, switching backup paths, restarting containers).
*   **Tiered Automated Compensation:** 
    *   *Tier 1 (Immediate):* Provides instant WiFi refund and a complimentary lobby bar item at the moment of outage detection—all without waiting for human intervention or loop completion.
    *   *Tier 2 (Escalation):* If the autonomous troubleshooting agent cannot heal the network after 8 attempts, the issue escalates to human engineers, and an LLM analyzes the guest's profile to issue custom, high-value perks (e.g., free dinners, room upgrades, spa credit, loyalty points).

---

## 2. Technical Architecture

NetConcierge runs as a set of containerized services orchestrated by Docker Compose. The topology simulates a complete hotel guest WiFi routing setup:

### Network Topology Diagram

```
                              +---------------------------------------+
                              |              EC2 Instance             |
                              |                                       |
  front-net                   |  path-a-net (172.21.0.0/24)           |
  172.20.0.0/24               |  +--------------------------------+   |
  +────────+                  |  | [eth1 interface]               |   |
  | client |──────────────────┼──|───────────────>[webserver:80]  |   |
  +────────+  [router:5000]   |  +--------------------------------+   |
                   │          |                                       |
                   │          |  path-b-net (172.22.0.0/24)           |
                   │          |  +--------------------------------+   |
                   │          |  | [eth2 interface]               |   |
                   └──────────┼──|───────────────>[webserver:80]  |   |
                              |  +--------------------------------+   |
                              |                                       |
                              |  [agent:8080] ---- docker.sock        |
                              |  [perk-agent:8081]                    |
                              |  [frontend:3000]                      |
                              +---------------------------------------+
```

### Container Services Reference

| Container | Image / Source | Port | IP Addresses | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **`client`** | `./client` | N/A | `172.20.0.10` on `front-net` | Simulates guest traffic by performing persistent HTTP curl requests. |
| **`router`** | `./router` | `5000` | `172.20.0.254` (`front-net`), `172.21.0.254` (`path-a-net`), `172.22.0.254` (`path-b-net`) | Alpine + `iptables`/`tc`. Represents the default gateway. Exposes a local Flask API to manipulate routing states. |
| **`webserver`** | `nginx:alpine` | `80` | `172.21.0.10` (`path-a-net`), `172.22.0.10` (`path-b-net`) | Simulates the hotel portal/internet destination. |
| **`agent`** | `./agent` | `8080` | Dynamic IP on `front-net` | Core LLM (TIP.ai) troubleshooting engine. Polls client logs and executes self-healing. |
| **`perk-agent`** | `./perk_agent` | `8081` | Dynamic IP on `front-net` | Host CRM and hospitality engine. Handles Tier-1 and Tier-2 automated compensation. |
| **`frontend`** | `./frontend` | `3000` | Dynamic IP on `front-net` (Mapped to host `80` and `3000`) | React-based Next.js dashboard displaying real-time events. |
| **`uptime-kuma`** | `louislam/uptime-kuma:1` | `3001` | Host Network | Independent status monitoring page. |

For a deep-dive into the AWS infrastructure and networking configuration, see the <ref_file file="/home/prube194/git/codefest/NetConcierge/docs/architecture/network_implementation.md" />.

---

## 3. Development Workflow & Setup

### Requirements
*   Docker & Docker Compose v2.x
*   A valid Marriott TIP API Key (retrieved from your system profile)
*   Artifactory credentials (to fetch the Marriott emerging-tech `tip-sdk` package)

### Step-by-Step Local Setup

1.  **Configure Environment Variables:** Create a `.env` file at the root of `/home/prube194/git/codefest/NetConcierge/` (copy from `.env.example`):
    ```ini
    TIP_API_KEY=your_marriott_tip_api_key_here
    ARTIFACTORY_USERNAME=your_artifactory_username
    ARTIFACTORY_TOKEN=your_artifactory_token
    # Optional Configurations
    WEBHOOK_URL=http://perk-agent:8081/fault-event
    ROOM_NUMBER=412
    FRONTEND_URL=http://frontend:3000/api/events
    ```

2.  **Launch the Services:**
    ```bash
    docker compose build
    docker compose up -d
    ```

3.  **Verify Running Status:**
    ```bash
    docker compose ps
    ```
    Ensure all containers report as `healthy` (this may take up to 60 seconds as the Next.js frontend and Flask services boot up).

---

## 4. How the Systems Communicate

The automated workflow relies on structured webhooks between the network agent, router API, and perk-agent.

```
+---------------+              +---------------+              +---------------+
|     Agent     |              |    Router     |              |  Perk-Agent   |
+---------------+              +---------------+              +---------------+
  |                              |                              |
  |-- (Log Polling) ------------>|                              |
  |   consecutive_errors >= THRESHOLD                           |
  |                              |                              |
  |-- POST /fault-event ───────────────────────────────────────>| (Tier-1 Fixed Perks Issued)
  |   (status=detected, room=412)                               |
  |                              |                              |
  |-- START LLM LOOP (Max 8 Turns)                              |
  |                              |                              |
  |==== Turn N: Tool Call ====================================| |
  |-- GET /state --------------->|                              |
  |-- POST /fault-update ──────────────────────────────────────>| (Timer reset)
  |                              |                              |
  |-- POST /active-path -------->|                              |
  |-- DELETE /fault/<path> ------>|                              |
  |===========================================================| |
  |                              |                              |
  |-- (Success) ────────────────────────────────────────────────|
  |-- POST /fault-event (status=resolved) ─────────────────────>| (Close fault, log info)
  |                              |                              |
  |-- (Failure / Turn Limit) ───────────────────────────────────|
  |-- POST /fault-event (status=escalated) ────────────────────>| (Call LLM with Guest Profile)
  |                                                             | (Tier-2 Custom Perks Recommended)
```

For endpoint definitions, request payload JSON examples, and communication step timelines, see the <ref_file file="/home/prube194/git/codefest/NetConcierge/docs/api/communication.md" />.
For copy-pasteable curl commands to manually mock these HTTP API requests, refer to <ref_file file="/home/prube194/git/codefest/NetConcierge/docs/api/calls.md" />.

---

## 5. Building Upon NetConcierge

NetConcierge is highly extensible. Developers can easily write new troubleshooting tools, tweak hospitality logic, or expand simulation scripts:

### A. Adding New Troubleshooting Tools to the Agent
The LLM agent in `agent/agent.py` can be granted additional network diagnostics or repair tools.
1.  **Define the Python function** in `agent/agent.py`. E.g., adding a DNS resolution check:
    ```python
    def tool_dns_resolve(hostname: str) -> str:
        return _container_exec("client", ["nslookup", hostname])
    ```
2.  **Add it to the LLM tool schemas list** (OpenAI/LiteLLM format) inside the code:
    ```python
    {
        "type": "function",
        "function": {
            "name": "dns_resolve",
            "description": "Check if a hostname resolves to an IP address from the client.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hostname": {"type": "string", "description": "e.g., 'webserver'"}
                },
                "required": ["hostname"]
            }
        }
    }
    ```
3.  **Register the function call mapping** inside the agent's turn executor loop.

For more details on the agent's Python code, review the implementation directly in <ref_file file="/home/prube194/git/codefest/NetConcierge/agent/agent.py" />.

### B. Modifying Hospitality Perks Logic
The compensation rules and options live inside `perk_agent/perk_agent.py`.
*   **Customize Tier-1 Fixed Perks:** Edit the `TIER_1_PERK_POOL` dictionary array inside `perk_agent/perk_agent.py` to add new automatic awards (e.g., breakfast vouchers, late checkouts).
*   **Customize Tier-2 Recommendations Prompt:** Search for the system prompt sent to the LLM gateway. You can instruct the model to weigh customer loyalty tiers (such as Silver, Gold, Platinum) more heavily or adjust the recommended value caps.

Read more in <ref_file file="/home/prube194/git/codefest/NetConcierge/perk_agent/perk_agent.py" />.

### C. Adding Router Fault Types
If you want to simulate more real-world failure types (like IP address conflicts, DNS poisoning, packet duplication):
1.  Open <ref_file file="/home/prube194/git/codefest/NetConcierge/router/api.py" />.
2.  Add a new block under `inject_fault()` handling your new type (e.g., using `iptables` or `tc`).
3.  Add the clean-up command under `clear_fault()` to ensure developers can reset it.
4.  Modify `scripts/inject-fault.sh` to make the bash injection easier.

---

## 6. Troubleshooting the System

When debugging development environments, follow this systematic approach:

### A. Monitor Container Logs
Container logging is the single most valuable source of information. Open individual terminals to follow live logs:
```bash
# Network Agent (See log parsing, LLM tool choices, and webhook submissions)
docker compose logs -f agent

# Perk Agent (See webhook receptions, Tier-1 actions, and Tier-2 LLM reasoning)
docker compose logs -f perk-agent

# Router (See Flask api invocations and iptables commands)
docker compose logs -f router

# Client (See persistent OK/ERR ping results)
docker compose logs -f client
```

### B. Checking Health and Status Endpoints
To verify liveness, check health status:
```bash
# Verify Router API State
curl -s http://localhost:5000/state | jq .

# Verify Agent loop state
curl -s http://localhost:8080/status | jq .

# Verify Perk-Agent's active faults map
curl -s http://localhost:8081/status | jq .
```

### C. Commonly Encountered Issues & Fixes

*   **WAF Private IP Blocking (HTTP 403 / 400 from LLM Gateway):**
    *   *Symptom:* LLM throws errors when processing `get_router_state` output.
    *   *Cause:* The Marriott Security Gateway Web Application Firewall (WAF) blocks private IPs (172.20...) in the body of chat requests to prevent data exfiltration.
    *   *Solution:* Raw router states must be summarized before feeding to the LLM. Ensure `_summarize_router_state` in `agent.py` does not leak raw IP lists. Similarly, ensure `curl_endpoint` uses only named aliases like `path-a` and `path-b` instead of raw IPs.
*   **Docker Socket Permission Errors on Agent Startup:**
    *   *Symptom:* Agent logs say `docker.errors.DockerException: Error while fetching server API version: Permission denied`.
    *   *Cause:* The agent needs access to `/var/run/docker.sock` to control containers. On some Linux instances, the socket permissions are restricted.
    *   *Solution:* Grant Docker socket read/write permissions on the host system:
        ```bash
        sudo chmod 666 /var/run/docker.sock
        ```
*   **Docker Network Attachment Order (Inverted eth1/eth2):**
    *   *Symptom:* Switching to `path-b` resolves `path-a` failures but doesn't actually route properly, or vice-versa.
    *   *Cause:* Docker determines network interface names (`eth1`, `eth2`) inside the `router` container based on the order of attachment at boot time.
    *   *Solution:* Inspect the network attachment order:
        ```bash
        docker exec router ip address
        ```
        Confirm `eth1` is bound to the subnet `172.21.0.0/24` (path-a) and `eth2` is bound to `172.22.0.0/24` (path-b). If they are inverted, restart the docker compose cluster.

---

## 7. Rebuilding & Restoring Commands

For convenience during fast-iteration coding, use these standard restore commands.

### Clean Fault Injection State
To immediately clear all iptables drops and traffic shaping delays, run:
```bash
bash scripts/restore.sh
```

### Hot Rebuild Particular Services
If you make a code change to `agent.py` or `perk_agent.py`, you can rebuild and launch them individually without interrupting other containers:
```bash
docker compose build agent perk-agent
docker compose up -d agent perk-agent
```

For remote administration commands (ssh-based git pulls and remote container restarts), check <ref_file file="/home/prube194/git/codefest/NetConcierge/docs/operations/commands.md" />.
