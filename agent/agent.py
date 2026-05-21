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


def tool_curl_endpoint(url: str) -> str:
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


def tool_get_router_state(path: str = "all") -> str:
    url = f"{ROUTER_API}/state" if path == "all" else f"{ROUTER_API}/state/{path}"
    try:
        resp = requests.get(url, timeout=5)
        return json.dumps(resp.json(), indent=2)
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
            "description": "HTTP GET a URL from the client container; returns status code and latency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to curl"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_router_state",
            "description": "Get current iptables and tc qdisc state from the router management API.",
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
                "Switch the router DNAT target to path 'a' (172.21.0.10) or "
                "path 'b' (172.22.0.10). Use when the active path is degraded "
                "and the alternate path appears healthy."
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
                "Clear all injected tc latency/loss qdiscs and iptables DROP rules "
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
You are NetConcierge, an autonomous network troubleshooting agent for a hotel WiFi system.

TOPOLOGY:
- client (172.20.0.10) sends HTTP to router (172.20.0.254)
- router DNAT-forwards to the active path's webserver IP:
    path-a → 172.21.0.10 (router eth1)
    path-b → 172.22.0.10 (router eth2)
- webserver runs nginx on both IPs

YOUR GOAL:
Diagnose why the client is seeing HTTP failures and restore service if possible.

RECOMMENDED STRATEGY:
1. Call get_router_state(path="all") to see iptables and tc state on both paths.
2. curl_endpoint("http://172.20.0.254") to test end-to-end connectivity through the router.
3. If a path has iptables DROP rules → clear_faults on that path first.
4. If a path has severe tc latency → clear_faults, or switch_active_path to the other.
5. If both paths are degraded → clear_faults(path="both").
6. If clearing faults doesn't help → switch_active_path, then verify with curl_endpoint.
7. As a last resort → restart_container("router") or restart_container("webserver").
8. ALWAYS finish by calling escalate with status, summary, and fault_type.

CONSTRAINTS:
- You have at most 8 tool calls total (including escalate) — be efficient.
- escalate MUST be your final call.
- Do not repeat the same tool call with identical arguments.
- IMPORTANT: The client and agent containers are on front-net (172.20.0.0/24) only.
  They CANNOT reach 172.21.0.10 or 172.22.0.10 directly.
  Always curl http://172.20.0.254 (the router) to test end-to-end connectivity.
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
def _run_agent_loop(trigger_lines: list[str]) -> None:
    log.info("Agent loop started")
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

            # Dispatch
            if name == "ping_host":
                result = tool_ping_host(args.get("host", ""))
            elif name == "curl_endpoint":
                result = tool_curl_endpoint(args.get("url", ""))
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


# ── Poll loop ──────────────────────────────────────────────────────────────────
def _poll_loop() -> None:
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
                global _last_loop_time
                _last_loop_time = time.time()
                _agent_busy.clear()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    poll_thread = threading.Thread(target=_poll_loop, daemon=True)
    poll_thread.start()
    app.run(host="0.0.0.0", port=8080)
