#!/usr/bin/env bash
# Clears all injected faults and restores clean routing state.
set -euo pipefail

DOCKER="${DOCKER:-docker}"

## Clear tc qdiscs from all possible path interfaces.
## Try eth0 through eth2 since Docker may assign them in varying order.
for iface in eth0 eth1 eth2; do
    $DOCKER exec router tc qdisc del dev "$iface" root 2>/dev/null \
        && echo "Cleared tc qdisc on ${iface}" \
        || echo "No tc qdisc on ${iface} (already clean)"
done

## Remove subnet-based DROP rules (current format after interface-order fix)
for subnet in 172.21.0.0/24 172.22.0.0/24; do
    $DOCKER exec router iptables -D FORWARD -s "$subnet" -j DROP 2>/dev/null || true
    $DOCKER exec router iptables -D FORWARD -d "$subnet" -j DROP 2>/dev/null || true
done
## Also remove any legacy interface-based DROP rules left from before the fix
for iface in eth0 eth1 eth2; do
    $DOCKER exec router iptables -D FORWARD -i "$iface" -j DROP 2>/dev/null || true
    $DOCKER exec router iptables -D FORWARD -o "$iface" -j DROP 2>/dev/null || true
done

## Reset DNAT back to path-a and sync the router API active_path variable.
## Use IP-based matching (-d 172.20.0.254) consistent with entrypoint.sh.
$DOCKER exec router iptables -t nat -F PREROUTING
$DOCKER exec router iptables -t nat -A PREROUTING -d 172.20.0.254 -p tcp --dport 80 \
    -j DNAT --to-destination 172.21.0.10:80
$DOCKER exec router curl -s -X POST http://localhost:5000/active-path \
    -H 'Content-Type: application/json' \
    -d '{"path":"a"}' > /dev/null
echo "Router reset to path-a."

echo "Fault state cleared."
