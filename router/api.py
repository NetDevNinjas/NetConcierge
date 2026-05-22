"""
Router management API.

Exposes endpoints for the agent to inspect and modify routing state
without needing shell access to the router container.

Reachable only within the Docker Compose network (front-net).
Port 5000 is NOT exposed publicly via the EC2 security group.
"""

import shlex
import subprocess

from flask import Flask, jsonify, request

app = Flask(__name__)

## WEBSERVER_MAP maps path label to the webserver's IP on its dedicated subnet.
WEBSERVER_MAP = {"a": "172.21.0.10", "b": "172.22.0.10"}
## SUBNET_MAP is used for subnet-based iptables rules (blackhole inject/clear).
## This avoids relying on interface names whose order Docker may vary.
SUBNET_MAP = {"a": "172.21.0.0/24", "b": "172.22.0.0/24"}

## Tracks which path is currently active; starts on path-a per entrypoint.sh
active_path = "a"


def _run(cmd: str) -> str:
    result = subprocess.run(shlex.split(cmd), capture_output=True, text=True)
    return (result.stdout + result.stderr).strip()


def _iface_for_path(path: str) -> str:
    """Dynamically look up the local interface for a path by routing table lookup.

    Uses the route to the path webserver to find which interface the router uses
    to reach that subnet. This is immune to Docker interface-name reordering.
    """
    webserver = WEBSERVER_MAP[path]
    out = _run(f"ip route get {webserver}")
    ## Output: "172.21.0.10 dev eth0 src 172.21.0.254 ..."
    parts = out.split()
    if "dev" in parts:
        return parts[parts.index("dev") + 1]
    ## Best-effort fallback based on known-good current assignment
    return {"a": "eth0", "b": "eth1"}[path]


def _path_state(path: str) -> dict:
    iface = _iface_for_path(path)
    return {
        "iface": iface,
        "tc_qdisc": _run(f"tc qdisc show dev {iface}"),
        "forward_rules": _run("iptables -L FORWARD -n -v"),
        "nat_prerouting": _run("iptables -t nat -L PREROUTING -n -v"),
    }


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/state")
def state_all():
    return jsonify(
        {
            "active_path": active_path,
            "path-a": _path_state("a"),
            "path-b": _path_state("b"),
        }
    )


@app.get("/state/<path>")
def state_one(path):
    if path not in ("a", "b"):
        return jsonify({"error": "path must be 'a' or 'b'"}), 400
    return jsonify(_path_state(path))


@app.post("/fault")
def inject_fault():
    data = request.get_json(force=True)
    path = data.get("path")
    fault_type = data.get("type")
    value = data.get("value", "300ms 100ms")

    if path not in ("a", "b"):
        return jsonify({"error": "path must be 'a' or 'b'"}), 400

    iface = _iface_for_path(path)
    subnet = SUBNET_MAP[path]

    if fault_type == "latency":
        _run(f"tc qdisc add dev {iface} root netem delay {value}")
    elif fault_type == "loss":
        _run(f"tc qdisc add dev {iface} root netem loss 40%")
    elif fault_type == "blackhole":
        ## Use subnet-based DROP rules so clear_fault can reliably delete them
        ## regardless of whether interface names have shifted between restarts.
        _run(f"iptables -I FORWARD -s {subnet} -j DROP")
        _run(f"iptables -I FORWARD -d {subnet} -j DROP")
    else:
        return jsonify({"error": f"unknown fault type: {fault_type}"}), 400

    return jsonify({"status": "injected", "path": path, "type": fault_type, "value": value})


@app.delete("/fault/<path>")
def clear_fault(path):
    if path not in ("a", "b"):
        return jsonify({"error": "path must be 'a' or 'b'"}), 400

    iface = _iface_for_path(path)
    subnet = SUBNET_MAP[path]
    ## tc qdisc still requires an interface name; use dynamic lookup
    _run(f"tc qdisc del dev {iface} root")
    ## iptables rules are subnet-based (see inject_fault)
    _run(f"iptables -D FORWARD -s {subnet} -j DROP")
    _run(f"iptables -D FORWARD -d {subnet} -j DROP")

    return jsonify({"status": "cleared", "path": path})


@app.post("/active-path")
def switch_path():
    global active_path
    data = request.get_json(force=True)
    new_path = data.get("path")

    if new_path not in ("a", "b"):
        return jsonify({"error": "path must be 'a' or 'b'"}), 400

    if new_path == active_path:
        return jsonify({"status": "unchanged", "active_path": active_path})

    ## Swap the DNAT rule to point at the new path webserver IP.
    ## Match on -d 172.20.0.254 (router front-net IP) rather than -i ethX
    ## for the same interface-order-independence reason as in entrypoint.sh.
    old_dest = WEBSERVER_MAP[active_path]
    new_dest = WEBSERVER_MAP[new_path]
    _run(
        f"iptables -t nat -D PREROUTING -d 172.20.0.254 -p tcp --dport 80 -j DNAT --to-destination {old_dest}:80"
    )
    _run(
        f"iptables -t nat -A PREROUTING -d 172.20.0.254 -p tcp --dport 80 -j DNAT --to-destination {new_dest}:80"
    )

    active_path = new_path
    return jsonify({"status": "switched", "active_path": active_path})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
