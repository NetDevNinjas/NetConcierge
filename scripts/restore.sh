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

echo "Fault state cleared."
