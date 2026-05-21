"""NetConcierge troubleshooting agent.

Polls client container logs every POLL_INTERVAL seconds. When it detects
FAULT_THRESHOLD consecutive HTTP failures it enters a TIP.ai tool-use loop
(max MAX_TURNS turns) to diagnose and heal the network, then fires a webhook
with the outcome regardless of whether the fault was resolved or not.
"""

import contextlib
import json
import logging
import os
import random
import threading
import time
from datetime import UTC, datetime

import docker
import requests
from flask import Flask, jsonify
from openai import OpenAI

# ── Configuration ──────────────────────────────────────────────────────────────
ROUTER_API = os.environ.get("ROUTER_API", "http://172.20.0.254:5000")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "http://localhost:9999/fault-event")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://litellm-api.up.railway.app/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-3-5-haiku-20241022")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
FAULT_THRESHOLD = int(os.environ.get("FAULT_THRESHOLD", "3"))
ROOM_NUMBER = os.environ.get("ROOM_NUMBER", "412")
ROOM_POOL = ["412", "718", "305", "921", "1102"]
FRONTEND_URL = os.environ.get("FRONTEND_URL", "")
MAX_TURNS = 8

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

_docker = docker.from_env()
app = Flask(__name__)

# LLM client — OpenAI-compatible; points at LiteLLM proxy
_llm = OpenAI(
    base_url=LLM_BASE_URL,
    api_key=os.environ.get("TIP_API_KEY", "no-key"),
)

LOOP_COOLDOWN = int(os.environ.get("LOOP_COOLDOWN", "60"))
PERK_AGENT_URL = os.environ.get("PERK_AGENT_URL", "http://perk-agent:8081")

# Set when the agent loop is actively running, prevents re-entrant loops
_agent_busy = threading.Event()
## Timestamp of the last loop completion; enforces LOOP_COOLDOWN between runs
_last_loop_time: float = 0.0
## Tracks current agent state for the /status endpoint and perk-agent polling
_agent_state: dict = {
    "status": "idle",
    "fault_detected_at": None,
    "current_turn": 0,
    "last_tool": None,
}


# ── Flask endpoints ──────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return jsonify({"status": "ok", "busy": _agent_busy.is_set()})


@app.get("/status")
def agent_status():
    return jsonify({**_agent_state, "room": ROOM_NUMBER})


# ── Tool implementations ───────────────────────────────────────────────────────
def _container_exec(container_name: str, cmd: list) -> str:
    """Run a command inside a named container via the Docker socket."""
    try:
        container = _docker.containers.get(container_name)
        _exit_code, output = container.exec_run(cmd, demux=False)
        decoded = output.decode("utf-8", errors="replace").strip() if output else ""
        return decoded or "(no output)"
    except Exception as exc:
        return f"(exec error: {exc})"


def tool_ping_host(host: str) -> str:
    return _container_exec("client", ["ping", "-c", "4", "-W", "3", host])


## Named targets for curl_endpoint — keeps private IPs out of LLM conversation history
_CURL_TARGETS = {
    "gateway": "http://172.20.0.254",
    "path-a": "http://172.21.0.10",
    "path-b": "http://172.22.0.10",
}


def tool_curl_endpoint(target: str) -> str:
    url = _CURL_TARGETS.get(target, _CURL_TARGETS["gateway"])
    return _container_exec(
        "client",
        [
            "curl",
            "-s",
            "-o",
            "/dev/null",
            "-w",
            "HTTP %{http_code} | %{time_total}s",
            "--max-time",
            "5",
            url,
        ],
    )


def _summarize_router_state(data: dict) -> str:
    """Convert raw router API state into a compact, WAF-safe summary."""
    lines = []
    lines.append(f"active_path: {data.get('active_path', 'unknown')}")
    for pname in ("path-a", "path-b"):
        pdata = data.get(pname, {})
        fwd = pdata.get("forward_rules", "")
        tc = pdata.get("tc_qdisc", "")
        ## Detect blocking rules without forwarding the raw rule text
        has_block = (
            "blocked: yes" if ("REJECT" in fwd or "DROP" in fwd or "DENY" in fwd) else "blocked: no"
        )
        ## Detect traffic shaping without forwarding raw tc output
        has_delay = (
            "shaping: yes"
            if ("netem" in tc or "tbf" in tc or "delay" in tc or "loss" in tc)
            else "shaping: no"
        )
        lines.append(f"{pname}: {has_block}, {has_delay}")
    return "\n".join(lines)


def tool_get_router_state(path: str = "all") -> str:
    url = f"{ROUTER_API}/state" if path == "all" else f"{ROUTER_API}/state/{path}"
    try:
        resp = requests.get(url, timeout=5)
        return _summarize_router_state(resp.json())
    except Exception as exc:
        return f"(router API error: {exc})"


def tool_restart_container(name: str) -> str:
    try:
        container = _docker.containers.get(name)
        container.restart(timeout=10)
        return f"Container '{name}' restarted successfully."
    except Exception as exc:
        return f"(restart error: {exc})"


def tool_switch_active_path(path: str) -> str:
    try:
        resp = requests.post(f"{ROUTER_API}/active-path", json={"path": path}, timeout=5)
        return json.dumps(resp.json())
    except Exception as exc:
        return f"(router API error: {exc})"


def tool_clear_faults(path: str) -> str:
    if path == "both":
        results = {}
        for p in ("a", "b"):
            try:
                resp = requests.delete(f"{ROUTER_API}/fault/{p}", timeout=5)
                results[p] = resp.json()
            except Exception as exc:
                results[p] = {"error": str(exc)}
        return json.dumps(results)
    try:
        resp = requests.delete(f"{ROUTER_API}/fault/{path}", timeout=5)
        return json.dumps(resp.json())
    except Exception as exc:
        return f"(router API error: {exc})"


def tool_clear(
    summary: str,
    history: list,
    fault_type: str = "unknown",
    active_path: str = "unknown",
    resolved_by: str | None = None,
    turns_used: int = 0,
) -> str:
    """Fire resolved webhook — no elevated perks needed."""
    payload = {
        "status": "resolved",
        "tier": 1,
        "fault_type": fault_type,
        "active_path": active_path,
        "resolved_by": resolved_by,
        "turns_used": turns_used,
        "room": ROOM_NUMBER,
        "history": history,
        "summary": summary,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    log.info(
        "Signal cleared — fault_type=%s active_path=%s resolved_by=%s turns=%d",
        fault_type,
        active_path,
        resolved_by,
        turns_used,
    )
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        return f"Webhook delivered: HTTP {resp.status_code}"
    except Exception as exc:
        log.warning("Webhook delivery failed: %s", exc)
        return f"Webhook failed: {exc}"


def tool_escalate(
    summary: str,
    history: list,
    fault_type: str = "unknown",
    active_path: str = "unknown",
    turns_used: int = 0,
) -> str:
    """Fire escalation webhook — triggers tier-2 elevated perks and human hand-off."""
    payload = {
        "status": "escalated",
        "tier": 2,
        "fault_type": fault_type,
        "active_path": active_path,
        "turns_used": turns_used,
        "room": ROOM_NUMBER,
        "history": history,
        "summary": summary,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    log.info(
        "Escalating to human operator — fault_type=%s active_path=%s turns=%d",
        fault_type,
        active_path,
        turns_used,
    )
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        return f"Webhook delivered: HTTP {resp.status_code}"
    except Exception as exc:
        log.warning("Webhook delivery failed: %s", exc)
        return f"Webhook failed: {exc}"


# ── OpenAI-compatible tool schema ──────────────────────────────────────────────
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "ping_host",
            "description": "Ping a host from the client container to check basic reachability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "IP or hostname to ping"},
                },
                "required": ["host"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "curl_endpoint",
            "description": "Test HTTP connectivity from the client; returns status code and latency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["gateway", "path-a", "path-b"],
                        "description": "gateway = end-to-end test through the router; path-a or path-b = specific backend",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_router_state",
            "description": "Get current routing policy and traffic configuration from the router management API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "enum": ["a", "b", "all"],
                        "description": "Which path to inspect — a, b, or all",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_active_path",
            "description": (
                "Switch the active backend to path 'a' or path 'b'. "
                "Use when the active path is degraded and the alternate path appears healthy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "enum": ["a", "b"]},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_faults",
            "description": (
                "Remove traffic blocking and shaping configurations "
                "on the specified path via the router management API."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "enum": ["a", "b", "both"]},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_container",
            "description": "Restart a named Docker container as a last-resort recovery action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name (e.g. router, webserver)",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear",
            "description": (
                "Report that the fault has been resolved and service is restored. "
                "Call this as your LAST action when you have successfully fixed the issue."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "One-sentence description of what was fixed and how",
                    },
                    "fault_type": {
                        "type": "string",
                        "enum": ["latency", "loss", "blackhole", "unknown"],
                    },
                    "active_path": {
                        "type": "string",
                        "description": "Active path at resolution time (a, b, or unknown)",
                    },
                    "resolved_by": {
                        "type": "string",
                        "description": "Tool that resolved the fault",
                    },
                    "turns_used": {"type": "integer"},
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate",
            "description": (
                "Report that the fault could NOT be resolved and human intervention is required. "
                "Call this as your LAST action ONLY when all remediation attempts have failed. "
                "This triggers elevated guest compensation and pages a human operator."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "One-sentence description of what was tried and why it failed",
                    },
                    "fault_type": {
                        "type": "string",
                        "enum": ["latency", "loss", "blackhole", "unknown"],
                    },
                    "active_path": {
                        "type": "string",
                        "description": "Active path at time of escalation (a, b, or unknown)",
                    },
                    "turns_used": {"type": "integer"},
                },
                "required": ["summary"],
            },
        },
    },
]

SYSTEM_PROMPT = """\
You are NetConcierge, an autonomous hotel WiFi diagnostic assistant.

YOUR GOAL:
Investigate why guests are seeing HTTP failures and restore service if possible.

RECOMMENDED STRATEGY:
1. Call get_router_state to inspect the current path configuration and any active restrictions.
2. Call curl_endpoint on the guest-facing gateway to test end-to-end connectivity.
3. If a path has active restrictions, call clear_faults on that path to restore it.
4. If one path has high latency or is degraded, call switch_active_path to the healthy path.
5. If both paths are degraded, call clear_faults with path set to both.
6. As a last resort, call restart_container to reset a service.
7. If you successfully restored service, call clear as your final action.
   If you could NOT restore service after exhausting all options, call escalate instead.

CONSTRAINTS:
- Maximum 8 tool calls total, including clear or escalate.
- clear or escalate MUST be your last action.
- Do not repeat the same tool call with identical arguments.
- The guest-facing gateway address is the router on the front network. Use it for curl_endpoint tests.
"""


# ── Fault detection ────────────────────────────────────────────────────────────
def _get_recent_client_lines(tail: int = 20) -> tuple[int, list[str]]:
    """Return (consecutive_err_count, raw_lines) from the tail of the client log."""
    try:
        container = _docker.containers.get("client")
        raw = container.logs(tail=tail).decode("utf-8", errors="replace")
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        consecutive = 0
        for line in reversed(lines):
            if " ERR " in line:
                consecutive += 1
            else:
                break
        return consecutive, lines
    except Exception as exc:
        log.warning("Could not read client logs: %s", exc)
        return 0, []


# ── Agent tool-use loop ────────────────────────────────────────────────────────
def _emit_event(event_type: str, message: str, data: dict | None = None) -> None:
    """Push an event to the frontend dashboard (best-effort)."""
    if not FRONTEND_URL:
        return
    payload = {
        "source": "network-agent",
        "type": event_type,
        "message": message,
        "data": data,
    }
    with contextlib.suppress(Exception):
        requests.post(FRONTEND_URL, json=payload, timeout=3)


def _notify_perk_agent(tier: int, **kwargs) -> None:
    """Fire a perk-agent notification (best-effort, non-blocking to diagnosis)."""
    payload = {"tier": tier, "room": ROOM_NUMBER, **kwargs}
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        log.info("Perk agent tier %d notified — HTTP %d", tier, resp.status_code)
    except Exception as exc:
        log.warning("Perk agent tier %d notification failed: %s", tier, exc)


def _run_agent_loop(trigger_lines: list[str]) -> None:
    global ROOM_NUMBER
    ROOM_NUMBER = random.choice(ROOM_POOL)
    log.info("Agent loop started — assigned room %s", ROOM_NUMBER)
    _emit_event("fault_detected", f"⚠️ Network fault detected in Room {ROOM_NUMBER} — starting diagnosis loop")

    # ── Tier 1: Immediate perks (WiFi refund + bar item) ───────────────────
    _notify_perk_agent(tier=1, fault_type="detected", summary="Network fault detected")

    history: list[dict] = []
    turn = 0
    escalated = False

    initial_ctx = "\n".join(trigger_lines[-10:])
    messages: list = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"The client is reporting consecutive HTTP failures. "
                f"Recent client log output:\n```\n{initial_ctx}\n```\n"
                "Please diagnose and resolve the issue."
            ),
        },
    ]

    while turn < MAX_TURNS and not escalated:
        try:
            response = _llm.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=TOOL_DEFINITIONS,
            )
        except Exception as exc:
            log.error("LLM API call failed: %s", exc)
            break

        msg = response.choices[0].message

        # Append assistant message — build dict to ensure serializability
        assistant_entry: dict = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if not msg.tool_calls:
            log.info("LLM returned no tool calls at turn %d — stopping", turn)
            break

        for tool_call in msg.tool_calls:
            if turn >= MAX_TURNS or escalated:
                break

            turn += 1
            name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}

            log.info("Turn %d — %s(%s)", turn, name, json.dumps(args))
            _emit_event("tool_call", f"Turn {turn}: calling {name}({json.dumps(args)})")

            # Dispatch
            if name == "ping_host":
                result = tool_ping_host(args.get("host", ""))
            elif name == "curl_endpoint":
                result = tool_curl_endpoint(args.get("target", "gateway"))
            elif name == "get_router_state":
                result = tool_get_router_state(args.get("path", "all"))
            elif name == "switch_active_path":
                result = tool_switch_active_path(args.get("path", "a"))
            elif name == "clear_faults":
                result = tool_clear_faults(args.get("path", "both"))
            elif name == "restart_container":
                result = tool_restart_container(args.get("name", ""))
            elif name == "clear":
                result = tool_clear(
                    summary=args.get("summary", ""),
                    history=history,
                    fault_type=args.get("fault_type", "unknown"),
                    active_path=args.get("active_path", "unknown"),
                    resolved_by=args.get("resolved_by"),
                    turns_used=turn,
                )
                escalated = True
            elif name == "escalate":
                result = tool_escalate(
                    summary=args.get("summary", ""),
                    history=history,
                    fault_type=args.get("fault_type", "unknown"),
                    active_path=args.get("active_path", "unknown"),
                    turns_used=turn,
                )
                escalated = True
            else:
                result = f"Unknown tool: {name}"

            log.info("Turn %d — result: %.300s", turn, result)
            _emit_event(
                "escalation" if name in ("escalate", "clear") else "tool_result",
                f"Turn {turn} result: {str(result)[:300]}",
                {"tool": name, "turn": turn},
            )
            history.append({"turn": turn, "tool": name, "input": args, "result": result})
            _agent_state.update({"current_turn": turn, "last_tool": name})
            if name not in ("escalate", "clear"):
                with contextlib.suppress(Exception):
                    requests.post(
                        f"{PERK_AGENT_URL}/fault-update",
                        json={
                            "room": ROOM_NUMBER,
                            "turn": turn,
                            "tool": name,
                            "status": "in_progress",
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                        timeout=3,
                    )
            ## Truncate long tool results to avoid gateway context limits (403)
            tool_content = str(result)
            if len(tool_content) > 1500:
                tool_content = tool_content[:1500] + "\n... (truncated)"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_content,
                }
            )

    # Safety net: if agent ran out of turns without calling escalate, force it
    if not escalated:
        log.warning("Max turns reached without resolution — forcing escalation to human operator")
        tool_escalate(
            summary="Agent exhausted all turns without resolving the fault.",
            history=history,
            turns_used=turn,
        )

    log.info("Agent loop complete — %d turn(s) used", turn)
    _emit_event(
        "resolved" if escalated else "info",
        f"Agent loop complete — {turn} turn(s) used",
    )

    # After escalation, wait 10s then send escalation-resolved to close the loop
    def _send_escalation_resolved() -> None:
        time.sleep(10)
        payload = {
            "status": "escalation-resolved",
            "tier": 2,
            "room": ROOM_NUMBER,
            "summary": "Escalated issue resolved by operator.",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        try:
            requests.post(WEBHOOK_URL, json=payload, timeout=10)
            log.info("Escalation-resolved sent for room %s", ROOM_NUMBER)
        except Exception as exc:
            log.warning("Escalation-resolved notification failed: %s", exc)
        _emit_event("resolved", "✅ Escalation resolved — issue fully closed")

    if escalated:
        threading.Thread(
            target=_send_escalation_resolved, daemon=True, name="escalation-resolved"
        ).start()


# ── Poll loop ──────────────────────────────────────────────────────────────────
def _poll_loop() -> None:
    global _last_loop_time
    log.info(
        "Agent polling started — interval=%ds, fault threshold=%d consecutive errors",
        POLL_INTERVAL,
        FAULT_THRESHOLD,
    )
    while True:
        time.sleep(POLL_INTERVAL)
        log.info("Agent polling...")

        if _agent_busy.is_set():
            log.info("Agent loop already active — skipping this poll cycle")
            continue

        ## Skip if we're still within the cooldown window after the last loop
        secs_since_last = time.time() - _last_loop_time
        if secs_since_last < LOOP_COOLDOWN:
            log.info("Cooldown active — %.0fs remaining", LOOP_COOLDOWN - secs_since_last)
            continue
        if _agent_state["status"] == "cooldown":
            _agent_state["status"] = "idle"

        consecutive, lines = _get_recent_client_lines()
        if consecutive >= FAULT_THRESHOLD:
            log.warning(
                "Fault detected: %d consecutive ERR lines — starting agent loop", consecutive
            )
            _agent_busy.set()
            detected_at = datetime.now(UTC).isoformat()
            _agent_state.update({"status": "running", "fault_detected_at": detected_at})
            ## tier-1 perks are fired inside _run_agent_loop via _notify_perk_agent(tier=1, ...)
            try:
                _run_agent_loop(lines)
            finally:
                _last_loop_time = time.time()
                _agent_state["status"] = "cooldown"
                _agent_busy.clear()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    poll_thread = threading.Thread(target=_poll_loop, daemon=True)
    poll_thread.start()
    app.run(host="0.0.0.0", port=8080)
