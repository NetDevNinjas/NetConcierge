#!/usr/bin/env bash
# NetConcierge Demo Script — walks through four fault scenarios.
#
# Usage (on EC2):
#   DOCKER='sudo docker' bash scripts/demo.sh
#
# Optional env vars:
#   DOCKER      — docker binary (default: docker; EC2 needs 'sudo docker')
#   AGENT_HOST  — host:port for the agent  (default: localhost:8081)
#   PERK_HOST   — host:port for perk-agent (default: localhost:8081)

set -euo pipefail

DOCKER="${DOCKER:-docker}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
AGENT_HOST="${AGENT_HOST:-localhost:8080}"
PERK_HOST="${PERK_HOST:-localhost:8081}"

# ── Colours ────────────────────────────────────────────────────────────────────
BOLD=$'\033[1m'
CYAN=$'\033[0;36m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
RED=$'\033[0;31m'
DIM=$'\033[2m'
RESET=$'\033[0m'

# ── Helpers ────────────────────────────────────────────────────────────────────
header() {
    local title="$1"
    local width=66
    local pad=$(( (width - ${#title}) / 2 ))
    echo
    echo -e "${CYAN}╔$(printf '═%.0s' $(seq 1 $width))╗${RESET}"
    printf "${CYAN}║${RESET}%${pad}s${BOLD}%s${RESET}%*s${CYAN}║${RESET}\n" "" "$title" $(( width - pad - ${#title} )) ""
    echo -e "${CYAN}╚$(printf '═%.0s' $(seq 1 $width))╝${RESET}"
    echo
}

step()    { echo -e "${YELLOW}▶  $1${RESET}"; }
ok()      { echo -e "${GREEN}✔  $1${RESET}"; }
info()    { echo -e "${DIM}   $1${RESET}"; }
divider() { echo -e "${CYAN}$(printf '─%.0s' $(seq 1 68))${RESET}"; }

pause() {
    echo
    read -n 1 -s -r -p "Please clear the output and press any key to continue..." || true
    echo
}

agent_status() {
    curl -s "http://${AGENT_HOST}/status" 2>/dev/null \
        | python3 -c "
import sys, json
d = json.load(sys.stdin)
s = d.get('status','?')
t = d.get('current_turn', 0)
l = d.get('last_tool') or 'none'
print(f'{s}  (turn {t}, last: {l})')
" 2>/dev/null || echo "unreachable"
}

poll_agent() {
    local label="$1"
    local count="$2"
    local interval=5
    echo
    step "$label"
    for i in $(seq 1 "$count"); do
        sleep "$interval"
        printf "  [%2d/%d] agent → " "$i" "$count"
        agent_status
    done
    echo
}

show_logs() {
    local service="$1"
    ## epoch seconds recorded at injection time; compute relative duration for --since
    local inject_epoch="$2"
    local tail="${3:-50}"
    local elapsed=$(( $(date +%s) - inject_epoch + 5 ))
    echo
    divider
    echo -e "${CYAN}  ${BOLD}${service}${RESET}${CYAN} logs (last ~${elapsed}s)${RESET}"
    divider
    ## Filter out chatty Flask access log lines for health/status polling
    $DOCKER logs "$service" --since "${elapsed}s" 2>&1 \
        | grep -v '"GET /health ' \
        | grep -v '"GET /status ' \
        | tail -n "$tail"
    divider
}

restore() {
    step "Restoring clean network state..."
    cd "$REPO_DIR"
    DOCKER="$DOCKER" bash scripts/restore.sh
    ok "Network clean."
}

# ── Pre-flight checks ─────────────────────────────────────────────────────────
clear
header "NetConcierge Demo"
echo -e "  ${BOLD}Infrastructure${RESET}"
echo "  Agent:      http://${AGENT_HOST}"
echo "  Perk-agent: http://${PERK_HOST}"
echo
step "Health check..."
printf "  %-14s %s\n" "agent:" "$(curl -s "http://${AGENT_HOST}/health" 2>/dev/null || echo '{"status":"UNREACHABLE"}')"
printf "  %-14s %s\n" "perk-agent:" "$(curl -s "http://${PERK_HOST}/health" 2>/dev/null || echo '{"status":"UNREACHABLE"}')"
echo
restore
echo
info "Scenarios:"
info "  1  Path A blackholed        → LLM resolves, tier-1 perks"
info "  2  Both paths blackholed    → LLM exhausted, escalation + tier-2 perks"
info "  3  Guest self-reports first → proactive contact, then auto-resolved"
info "  4  Persistent hardware fault → agent exhausts all options, human escalation"
pause

# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 1 — Single path failure, auto-resolved
# ══════════════════════════════════════════════════════════════════════════════
header "Scenario 1 / 4 — Path Failure: Auto-Resolved"
cat <<'EOF'
  Story
  ─────
  Path A fails (blackhole). Guests in room 412 start seeing HTTP errors.

  What to watch for
  ─────────────────
  • Perk-agent issues tier-1 perks immediately at detection
      → WiFi bill refund for today
      → Complimentary drink or appetizer at the bar
  • Agent LLM loop: get_router_state → clear_faults / switch_active_path
  • Agent reports resolution → perk-agent logs resolution (no extra perks)

EOF
pause

step "Injecting blackhole fault on path A..."
T1=$(date +%s)
cd "$REPO_DIR"
DOCKER="$DOCKER" bash scripts/inject-fault.sh --path a --type blackhole
ok "Fault injected at $(date -u +"%Y-%m-%dT%H:%M:%SZ")."
info "Agent polls every 10s and needs 3 consecutive ERR lines (~30s to trigger)."

poll_agent "Monitoring agent — waiting for detection and resolution..." 9

show_logs agent     "$T1" 60
show_logs perk-agent "$T1" 40

echo
echo -e "${GREEN}  Expected outcome${RESET}"
echo "  ✔  agent:      Fault detected → LLM loop ran → 'Agent loop complete'"
echo "  ✔  perk-agent: TIER 1 PERKS issued (detection)"
echo "  ✔  perk-agent: resolution logged, no LLM call needed"

restore
pause

# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 2 — Complete outage, escalated + tier-2 LLM perks
# ══════════════════════════════════════════════════════════════════════════════
header "Scenario 2 / 4 — Complete Outage: Escalated"
cat <<'EOF'
  Story
  ─────
  Both paths A and B are blackholed simultaneously. The agent can't find a
  healthy path and exhausts all 8 LLM turns without resolving the issue.

  What to watch for
  ─────────────────
  • Perk-agent issues tier-1 perks immediately at detection (same as scenario 1)
  • Agent LLM loop tries clear_faults, switch_active_path, curl tests — all fail
  • Agent escalates with status=escalated (tier=2)
  • Perk-agent calls LLM for elevated compensation based on Sarah's Gold status
      → Likely: complimentary dinner for two, spa credit, or loyalty points bonus

EOF
pause

step "Injecting blackhole fault on BOTH paths..."
T2=$(date +%s)
cd "$REPO_DIR"
DOCKER="$DOCKER" bash scripts/inject-fault.sh --path both --type blackhole
ok "Full blackout injected at $(date -u +"%Y-%m-%dT%H:%M:%SZ")."
info "LLM loop runs up to 8 turns before escalating — allow ~60s."

poll_agent "Monitoring agent — waiting for escalation..." 12

show_logs agent     "$T2" 80
show_logs perk-agent "$T2" 80

echo
echo -e "${GREEN}  Expected outcome${RESET}"
echo "  ✔  agent:      'Max turns reached without escalate — forcing escalation'"
echo "  ✔  perk-agent: TIER 1 PERKS issued (detection)"
echo "  ✔  perk-agent: TIER 2 PERKS — LLM recommendation printed (═══ block)"

restore
pause

# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 3 — Guest self-reports, agent detects independently
# ══════════════════════════════════════════════════════════════════════════════
header "Scenario 3 / 4 — Guest Initiates: /guest-report"
cat <<'EOF'
  Story
  ─────
  Guest Sarah Mitchell calls the concierge before the monitoring system fires.
  Perk-agent receives her report and immediately queries the agent for status.
  We then inject a fault to show the normal detection loop also kicks in.

  What to watch for
  ─────────────────
  • POST /guest-report → perk-agent queries GET agent:8080/status in real time
  • Response to guest: "We are investigating your issue..."
  • Fault injection → agent detects independently → tier-1 perks + resolution

EOF
pause

step "Guest Sarah reporting WiFi problems via /guest-report..."
T3=$(date +%s)
echo
echo -e "${DIM}  Request:${RESET}"
echo '  POST /guest-report {"room": "412", "message": "My WiFi keeps dropping during my presentation prep."}'
echo
response=$(curl -s -X POST "http://${PERK_HOST}/guest-report" \
    -H "Content-Type: application/json" \
    -d '{"room": "412", "message": "My WiFi keeps dropping during my presentation prep."}')
echo -e "${DIM}  Response:${RESET}"
echo "$response" | python3 -m json.tool 2>/dev/null || echo "  $response"
echo

info "Perk-agent queried agent /status — visible in perk-agent logs."
info "Current agent status:"
echo -n "    → "
agent_status
echo

step "Now injecting fault on path A (agent detects independently)..."
cd "$REPO_DIR"
DOCKER="$DOCKER" bash scripts/inject-fault.sh --path a --type blackhole
ok "Fault injected — agent will detect within ~30s."

poll_agent "Monitoring agent — waiting for detection and resolution..." 9

show_logs agent     "$T3" 60
show_logs perk-agent "$T3" 50

echo
echo -e "${GREEN}  Expected outcome${RESET}"
echo "  ✔  perk-agent: logged 'GUEST REPORT — Room 412' + agent status query"
echo "  ✔  agent:      independently detected fault and resolved"
echo "  ✔  perk-agent: TIER 1 PERKS issued, resolution logged"

restore

# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 4 — Persistent hardware fault, human escalation required
# ══════════════════════════════════════════════════════════════════════════════
header "Scenario 4 / 4 — Persistent Fault: Human Escalation Required"
cat <<'EOF'
  Story
  ─────
  A physical link fault keeps reasserting itself — every time the agent clears
  the path, the underlying hardware error immediately re-introduces the failure.
  The agent tries every tool in its arsenal: clear_faults on both paths,
  switch_active_path, restart_container, curl verification — nothing sticks.

  What to watch for
  ─────────────────
  • Perk-agent issues tier-1 perks immediately at detection
  • Agent LLM loop cycles through all available tools — all fail
  • Agent exhausts turn limit and is forced to escalate with status=escalated
  • Perk-agent calls LLM for tier-2 elevated compensation
  • Escalation payload shows human operator hand-off is required

EOF
pause

step "Injecting persistent fault (background re-injector simulates hardware failure)..."
T4=$(date +%s)
cd "$REPO_DIR"
## Start a background process that re-asserts the blackhole every 4 seconds.
## This simulates a physical layer fault that survives any software clear.
(
    while true; do
        sleep 4
        $DOCKER exec router iptables -I FORWARD -i eth1 -j DROP 2>/dev/null || true
        $DOCKER exec router iptables -I FORWARD -o eth1 -j DROP 2>/dev/null || true
        $DOCKER exec router iptables -I FORWARD -i eth2 -j DROP 2>/dev/null || true
        $DOCKER exec router iptables -I FORWARD -o eth2 -j DROP 2>/dev/null || true
    done
) &
REINJECT_PID=$!
## Inject the initial blackhole on both paths
DOCKER="$DOCKER" bash scripts/inject-fault.sh --path both --type blackhole
ok "Persistent fault active (re-injector PID ${REINJECT_PID}) at $(date -u +"%Y-%m-%dT%H:%M:%SZ")."
info "Agent will try to clear the fault but it will keep coming back — expect escalation."

poll_agent "Monitoring agent — waiting for exhaustion and human escalation..." 14

## Stop the re-injector before restoring, otherwise restore.sh races it
kill "$REINJECT_PID" 2>/dev/null && wait "$REINJECT_PID" 2>/dev/null || true
ok "Re-injector stopped."

show_logs agent      "$T4" 80
show_logs perk-agent "$T4" 60

echo
echo -e "${GREEN}  Expected outcome${RESET}"
echo "  ✔  agent:      Tried clear_faults, switch_active_path, restart — all failed"
echo "  ✔  agent:      'Escalating — status=escalated' after turn limit hit"
echo "  ✔  perk-agent: TIER 1 PERKS issued (detection)"
echo "  ✔  perk-agent: TIER 2 PERKS — LLM recommended elevated compensation"
echo "  ✔  Human operator hand-off logged in escalation payload"

restore

header "Demo Complete"
echo "  Scenarios covered:"
echo "  1  Path failure (single)   → LLM resolved, tier-1 perks"
echo "  2  Complete outage         → LLM escalated, tier-1 + tier-2 LLM perks"
echo "  3  Guest self-report       → proactive contact, agent auto-resolved"
echo "  4  Persistent hardware     → agent exhausted, human escalation + tier-2 perks"
echo
step "Final service state..."
printf "  %-14s %s\n" "agent:" "$(curl -s "http://${AGENT_HOST}/status" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo '?')"
printf "  %-14s %s\n" "perk-agent:" "$(curl -s "http://${PERK_HOST}/status" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); faults=list(d.get('active_faults',{}).keys()); print('active faults: ' + (str(faults) if faults else 'none'))" 2>/dev/null || echo '?')"
echo
