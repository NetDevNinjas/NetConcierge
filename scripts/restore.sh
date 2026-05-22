#!/usr/bin/env bash
# Clears all injected faults and restores clean routing state.
set -euo pipefail

DOCKER="${DOCKER:-docker}"

for iface in eth1 eth2; do
    $DOCKER exec router tc qdisc del dev "$iface" root 2>/dev/null \
        && echo "Cleared tc qdisc on ${iface}" \
        || echo "No tc qdisc on ${iface} (already clean)"
done

## Remove only injected DROP rules; baseline ACCEPT rules are left intact
for iface in eth1 eth2; do
    $DOCKER exec router iptables -D FORWARD -i "$iface" -j DROP 2>/dev/null || true
    $DOCKER exec router iptables -D FORWARD -o "$iface" -j DROP 2>/dev/null || true
done

## Reset DNAT back to path-a and sync the router API's active_path variable.
## Previous agent runs may have called switch_active_path(b), leaving the DNAT
## pointing at the path-b webserver (172.22.0.10) after restore.
$DOCKER exec router iptables -t nat -F PREROUTING
$DOCKER exec router iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 80 \
    -j DNAT --to-destination 172.21.0.10:80
$DOCKER exec router curl -s -X POST http://localhost:5000/active-path \
    -H 'Content-Type: application/json' \
    -d '{"path":"a"}' > /dev/null
echo "Router reset to path-a."

echo "Fault state cleared."
