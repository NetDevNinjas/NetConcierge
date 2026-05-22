#!/usr/bin/env bash
# NetConcierge Demo — Interactive Scenario Menu
#
# Usage (on EC2):
#   DOCKER='sudo docker' bash scripts/demo-menu.sh
#
# Optional env vars:
#   DOCKER      — docker binary (default: docker; EC2 needs 'sudo docker')
#   AGENT_HOST  — host:port for the agent       (default: localhost:8080)
#   PERK_HOST   — host:port for perk-agent      (default: localhost:8081)

set -euo pipefail

DOCKER="${DOCKER:-docker}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
AGENT_HOST="${AGENT_HOST:-localhost:8080}"
PERK_HOST="${PERK_HOST:-localhost:8081}"

# ── Colours ───────────────────────────────────────────────────────────────────
BOLD=$'\033[1m'
CYAN=$'\033[0;36m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
RED=$'\033[0;31m'
DIM=$'\033[2m'
RESET=$'\033[0m'

# ── Helpers ───────────────────────────────────────────────────────────────────
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
    read -n 1 -s -r -p "   Press any key to continue..." || true
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
    local inject_epoch="$2"
    local tail="${3:-50}"
    local elapsed=$(( $(date +%s) - inject_epoch + 5 ))
    echo
    divider
    echo -e "${CYAN}  ${BOLD}${service}${RESET}${CYAN} logs (last ~${elapsed}s)${RESET}"
    divider
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

# ── Scenario functions ────────────────────────────────────────────────────────

run_scenario_1() {
    header "Scenario 1 — Path Failure: Auto-Resolved"
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
    T=$(date +%s)
    cd "$REPO_DIR"
    DOCKER="$DOCKER" bash scripts/inject-fault.sh --path a --type blackhole
    ok "Fault injected at $(date -u +"%Y-%m-%dT%H:%M:%SZ")."
    info "Agent polls every 10s and needs 3 consecutive ERR lines (~30s to trigger)."

    poll_agent "Monitoring agent — waiting for detection and resolution..." 9

    show_logs agent     "$T" 60
    show_logs perk-agent "$T" 40

    echo
    echo -e "${GREEN}  Expected outcome${RESET}"
    echo "  ✔  agent:      Fault detected → LLM loop ran → 'Agent loop complete'"
    echo "  ✔  perk-agent: TIER 1 PERKS issued (detection)"
    echo "  ✔  perk-agent: resolution logged, no LLM call needed"

    restore
}

run_scenario_2() {
    header "Scenario 2 — Complete Outage: Auto-Resolved"
    cat <<'EOF'
  Story
  ─────
  Both paths A and B are blackholed simultaneously — a total connectivity loss.
  The agent detects the dual failure, clears both faults, and confirms via
  router state that the active path is healthy. Service is restored in seconds.

  What to watch for
  ─────────────────
  • Perk-agent issues tier-1 perks immediately at detection (same as scenario 1)
  • Agent LLM loop: get_router_state → clear_faults(both) → get_router_state
  • Router state confirms both paths unblocked → agent calls clear
  • Perk-agent logs resolution (tier-1 already issued, no extra perks needed)

EOF
    pause

    step "Injecting blackhole fault on BOTH paths..."
    T=$(date +%s)
    cd "$REPO_DIR"
    DOCKER="$DOCKER" bash scripts/inject-fault.sh --path both --type blackhole
    ok "Full blackout injected at $(date -u +"%Y-%m-%dT%H:%M:%SZ")."
    info "Agent needs 1 ERR line to trigger (~3s), then runs up to 8 LLM turns."

    poll_agent "Monitoring agent — waiting for detection and resolution..." 8

    show_logs agent     "$T" 80
    show_logs perk-agent "$T" 80

    echo
    echo -e "${GREEN}  Expected outcome${RESET}"
    echo "  ✔  agent:      Fault detected → LLM loop ran → 'Agent loop complete'"
    echo "  ✔  perk-agent: TIER 1 PERKS issued (detection)"
    echo "  ✔  perk-agent: resolution logged, no LLM call needed"

    restore
}

run_scenario_3() {
    header "Scenario 3 — Guest Initiates: /guest-report"
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
    T=$(date +%s)
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

    show_logs agent     "$T" 60
    show_logs perk-agent "$T" 50

    echo
    echo -e "${GREEN}  Expected outcome${RESET}"
    echo "  ✔  perk-agent: logged 'GUEST REPORT — Room 412' + agent status query"
    echo "  ✔  agent:      independently detected fault and resolved"
    echo "  ✔  perk-agent: TIER 1 PERKS issued, resolution logged"

    restore
}

run_scenario_4() {
    header "Scenario 4 — Persistent Fault: Human Escalation Required"
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
    T=$(date +%s)
    cd "$REPO_DIR"
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
    DOCKER="$DOCKER" bash scripts/inject-fault.sh --path both --type blackhole
    ok "Persistent fault active (re-injector PID ${REINJECT_PID}) at $(date -u +"%Y-%m-%dT%H:%M:%SZ")."
    info "Agent will try to clear the fault but it will keep coming back — expect escalation."

    poll_agent "Monitoring agent — waiting for exhaustion and human escalation..." 14

    kill "$REINJECT_PID" 2>/dev/null && wait "$REINJECT_PID" 2>/dev/null || true
    ok "Re-injector stopped."

    show_logs agent      "$T" 80
    show_logs perk-agent "$T" 60

    echo
    echo -e "${GREEN}  Expected outcome${RESET}"
    echo "  ✔  agent:      Tried clear_faults, switch_active_path, restart — all failed"
    echo "  ✔  agent:      'Escalating — status=escalated' after turn limit hit"
    echo "  ✔  perk-agent: TIER 1 PERKS issued (detection)"
    echo "  ✔  perk-agent: TIER 2 PERKS — LLM recommended elevated compensation"
    echo "  ✔  Human operator hand-off logged in escalation payload"

    restore
}

run_all() {
    run_scenario_1; pause
    run_scenario_2; pause
    run_scenario_3; pause
    run_scenario_4
}

# ── Pre-flight ────────────────────────────────────────────────────────────────
preflight() {
    clear
    header "NetConcierge Demo"
    echo -e "  ${BOLD}Infrastructure${RESET}"
    echo "  Agent:      http://${AGENT_HOST}"
    echo "  Perk-agent: http://${PERK_HOST}"
    echo
    step "Health check..."
    printf "  %-14s %s\n" "agent:"      "$(curl -s "http://${AGENT_HOST}/health" 2>/dev/null || echo '{"status":"UNREACHABLE"}')"
    printf "  %-14s %s\n" "perk-agent:" "$(curl -s "http://${PERK_HOST}/health"  2>/dev/null || echo '{"status":"UNREACHABLE"}')"
    echo
    restore
}

# ── Main menu loop ────────────────────────────────────────────────────────────
preflight

while true; do
    echo
    echo -e "${CYAN}╔$(printf '═%.0s' $(seq 1 50))╗${RESET}"
    printf  "${CYAN}║${RESET}  %-48s${CYAN}║${RESET}\n" "${BOLD}NetConcierge — Select a Scenario${RESET}"
    echo -e "${CYAN}╠$(printf '═%.0s' $(seq 1 50))╣${RESET}"
    printf  "${CYAN}║${RESET}  ${BOLD}1${RESET}  %-46s${CYAN}║${RESET}\n" "Path Failure: Auto-Resolved"
    printf  "${CYAN}║${RESET}  ${BOLD}2${RESET}  %-46s${CYAN}║${RESET}\n" "Complete Outage: Auto-Resolved"
    printf  "${CYAN}║${RESET}  ${BOLD}3${RESET}  %-46s${CYAN}║${RESET}\n" "Guest Self-Report + Auto-Resolved"
    printf  "${CYAN}║${RESET}  ${BOLD}4${RESET}  %-46s${CYAN}║${RESET}\n" "Persistent Fault: Human Escalation"
    printf  "${CYAN}║${RESET}  ${BOLD}a${RESET}  %-46s${CYAN}║${RESET}\n" "Run all scenarios (sequential)"
    printf  "${CYAN}║${RESET}  ${BOLD}r${RESET}  %-46s${CYAN}║${RESET}\n" "Reset network state"
    printf  "${CYAN}║${RESET}  ${BOLD}q${RESET}  %-46s${CYAN}║${RESET}\n" "Quit"
    echo -e "${CYAN}╚$(printf '═%.0s' $(seq 1 50))╝${RESET}"
    echo
    read -r -p "  → " choice || choice="q"
    echo

    case "${choice,,}" in
        1) run_scenario_1 ;;
        2) run_scenario_2 ;;
        3) run_scenario_3 ;;
        4) run_scenario_4 ;;
        a) run_all ;;
        r) restore ;;
        q) break ;;
        *) echo -e "${RED}  Invalid choice — enter 1, 2, 3, 4, a, r, or q.${RESET}" ;;
    esac

    echo
    read -n 1 -s -r -p "  Press any key to return to menu..." || true
    clear
done

echo
header "Demo Complete"
echo "  Thanks for watching NetConcierge in action."
echo
