"""NetConcierge troubleshooting agent.

Polls client container logs every POLL_INTERVAL seconds. When it detects
FAULT_THRESHOLD consecutive HTTP failures it enters a TIP.ai tool-use loop
(max MAX_TURNS turns) to diagnose and heal the network, then fires a webhook
with the outcome regardless of whether the fault was resolved or not.
"""

import json
import logging
import os
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

# Set when the agent loop is actively running, prevents re-entrant loops
_agent_busy = threading.Event()
## Timestamp of the last loop completion; enforces LOOP_COOLDOWN between runs
_last_loop_time: float = 0.0


# ── Flask health endpoint ──────────────────────────────────────────────────────
@app.get("/health")
def health():
    return jsonify({"status": "ok", "busy": _agent_busy.is_set()})


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
    try:
        resp = requests.delete(f"{ROUTER_API}/fault/{path}", timeout=5)
        return json.dumps(resp.json())
    except Exception as exc:
        return f"(router API error: {exc})"


def tool_escalate(
    status: str,
    summary: str,
    history: list,
    fault_type: str = "unknown",
    active_path: str = "unknown",
    resolved_by: str | None = None,
    turns_used: int = 0,
) -> str:
    payload = {
        "status": status,
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
        "Escalating — status=%s fault_type=%s active_path=%s turns=%d",
        status,
        fault_type,
        active_path,
        turns_used,
    )

    # ── Tier 2: Only trigger elevated perks when fault is unresolved ───────
    if status == "escalated":
        tier2_payload = {**payload, "tier": 2}
        try:
            requests.post(WEBHOOK_URL, json=tier2_payload, timeout=10)
            log.info("Perk agent tier 2 notified (escalation)")
        except Exception as exc:
            log.warning("Perk agent tier 2 notification failed: %s", exc)

    # ── Always fire the base escalation webhook for audit/logging ──────────
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
            "name": "escalate",
            "description": (
                "Report the final outcome to the guest-facing system. "
                "ALWAYS call this as your very last action. "
                "Use status='resolved' if you fixed the issue, 'escalated' if you could not."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["resolved", "escalated"],
                        "description": "resolved = fixed; escalated = could not fix",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-sentence human-readable description of what happened",
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
                        "description": "Tool that resolved the fault, or null if not resolved",
                    },
                    "turns_used": {"type": "integer"},
                },
                "required": ["status", "summary"],
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
7. ALWAYS finish by calling escalate with status, summary, and fault_type.

CONSTRAINTS:
- Maximum 8 tool calls total, including escalate.
- escalate MUST be your last action.
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
    try:
        requests.post(FRONTEND_URL, json=payload, timeout=3)
    except Exception:
        pass  # Non-critical — don't disrupt agent flow


def _notify_perk_agent(tier: int, **kwargs) -> None:
    """Fire a perk-agent notification (best-effort, non-blocking to diagnosis)."""
    payload = {"tier": tier, "room": ROOM_NUMBER, **kwargs}
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        log.info("Perk agent tier %d notified — HTTP %d", tier, resp.status_code)
    except Exception as exc:
        log.warning("Perk agent tier %d notification failed: %s", tier, exc)


def _run_agent_loop(trigger_lines: list[str]) -> None:
    log.info("Agent loop started")
    _emit_event("fault_detected", "⚠️ Network fault detected — starting diagnosis loop")

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
            elif name == "escalate":
                # Inject our tracked history so the webhook always has the full record
                result = tool_escalate(
                    status=args.get("status", "escalated"),
                    summary=args.get("summary", ""),
                    history=history,
                    fault_type=args.get("fault_type", "unknown"),
                    active_path=args.get("active_path", "unknown"),
                    resolved_by=args.get("resolved_by"),
                    turns_used=turn,
                )
                escalated = True
            else:
                result = f"Unknown tool: {name}"

            log.info("Turn %d — result: %.300s", turn, result)
            _emit_event(
                "escalation" if name == "escalate" else "tool_result",
                f"Turn {turn} result: {str(result)[:300]}",
                {"tool": name, "turn": turn},
            )
            history.append({"turn": turn, "tool": name, "input": args, "result": result})
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
        log.warning("Max turns reached without escalate — forcing escalation")
        tool_escalate(
            status="escalated",
            summary="Agent exhausted all turns without resolving the fault.",
            history=history,
            turns_used=turn,
        )

    log.info("Agent loop complete — %d turn(s) used", turn)
    _emit_event(
        "resolved" if escalated else "info",
        f"Agent loop complete — {turn} turn(s) used",
    )


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

        consecutive, lines = _get_recent_client_lines()
        if consecutive >= FAULT_THRESHOLD:
            log.warning(
                "Fault detected: %d consecutive ERR lines — starting agent loop", consecutive
            )
            _agent_busy.set()
            try:
                _run_agent_loop(lines)
            finally:
                _last_loop_time = time.time()
                _agent_busy.clear()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    poll_thread = threading.Thread(target=_poll_loop, daemon=True)
    poll_thread.start()
    app.run(host="0.0.0.0", port=8080)
