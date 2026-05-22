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

## Look up the interface for a path by asking the router which interface it uses
## to reach the path webserver. Avoids hardcoding eth0/eth1/etc which Docker may
## assign in a different order depending on network creation sequence.
get_iface() {
    local webserver_ip="$1"
    $DOCKER exec router ip route get "$webserver_ip"         | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}'
}

inject_path() {
    local path_label="$1"
    local subnet webserver_ip iface
    case "$path_label" in
        a) subnet="172.21.0.0/24"; webserver_ip="172.21.0.10" ;;
        b) subnet="172.22.0.0/24"; webserver_ip="172.22.0.10" ;;
    esac

    case "$TYPE_ARG" in
        latency)
            iface=$(get_iface "$webserver_ip")
            $DOCKER exec router tc qdisc add dev "$iface" root netem delay $VALUE_ARG
            echo "Injected latency (${VALUE_ARG}) on ${iface} (path-${path_label})"
            ;;
        loss)
            iface=$(get_iface "$webserver_ip")
            $DOCKER exec router tc qdisc add dev "$iface" root netem loss 40%
            echo "Injected 40% packet loss on ${iface} (path-${path_label})"
            ;;
        blackhole)
            ## Use subnet-based DROP rules so restore.sh and the router API can
            ## reliably delete them regardless of interface-name ordering.
            $DOCKER exec router iptables -I FORWARD -s "$subnet" -j DROP
            $DOCKER exec router iptables -I FORWARD -d "$subnet" -j DROP
            echo "Blackholed $subnet (path-${path_label})"
            ;;
        *)
            echo "Unknown fault type: ${TYPE_ARG} (must be latency|loss|blackhole)"; exit 1 ;;
    esac
}

case "$PATH_ARG" in
    a)    inject_path a ;;
    b)    inject_path b ;;
    both) inject_path a; inject_path b ;;
    *)    echo "Error: --path must be a, b, or both"; exit 1 ;;
esac
