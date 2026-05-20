#!/usr/bin/env bash
# Usage: ./scripts/inject-fault.sh --path <a|b|both> --type <latency|loss|blackhole>
#        Optional: --value <tc netem delay string, e.g. "500ms 100ms">
set -euo pipefail

DOCKER="${DOCKER:-docker}"
PATH_ARG=""
TYPE_ARG=""
VALUE_ARG="300ms 100ms"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --path)  PATH_ARG="$2";  shift 2 ;;
        --type)  TYPE_ARG="$2";  shift 2 ;;
        --value) VALUE_ARG="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

[[ -z "$PATH_ARG" ]] && { echo "Error: --path is required (a|b|both)"; exit 1; }
[[ -z "$TYPE_ARG" ]] && { echo "Error: --type is required (latency|loss|blackhole)"; exit 1; }

inject_path() {
    local iface="$1"  # eth1 = path-a, eth2 = path-b
    case "$TYPE_ARG" in
        latency)
            $DOCKER exec router tc qdisc add dev "$iface" root netem delay $VALUE_ARG
            echo "Injected latency (${VALUE_ARG}) on ${iface}"
            ;;
        loss)
            $DOCKER exec router tc qdisc add dev "$iface" root netem loss 40%
            echo "Injected 40% packet loss on ${iface}"
            ;;
        blackhole)
            $DOCKER exec router iptables -I FORWARD -i "$iface" -j DROP
            $DOCKER exec router iptables -I FORWARD -o "$iface" -j DROP
            echo "Blackholed ${iface}"
            ;;
        *)
            echo "Unknown fault type: ${TYPE_ARG} (must be latency|loss|blackhole)"; exit 1 ;;
    esac
}

case "$PATH_ARG" in
    a)    inject_path eth1 ;;
    b)    inject_path eth2 ;;
    both) inject_path eth1; inject_path eth2 ;;
    *)    echo "Error: --path must be a, b, or both"; exit 1 ;;
esac
