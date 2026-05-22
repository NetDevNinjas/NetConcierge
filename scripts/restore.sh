#!/usr/bin/env bash
# Clears all injected faults and restores clean routing state.
set -euo pipefail

DOCKER="${DOCKER:-docker}"

## Clear tc qdiscs from all possible path interfaces.
for iface in eth0 eth1 eth2; do
    $DOCKER exec router tc qdisc del dev "$iface" root 2>/dev/null \
        && echo "Cleared tc qdisc on ${iface}" \
        || echo "No tc qdisc on ${iface} (already clean)"
done

## Remove ALL copies of subnet-based DROP rules.
## Use a loop because the scenario-4 re-injector may have inserted multiple copies.
for subnet in 172.21.0.0/24 172.22.0.0/24; do
    while $DOCKER exec router iptables -D FORWARD -s "$subnet" -j DROP 2>/dev/null; do :; done
    while $DOCKER exec router iptables -D FORWARD -d "$subnet" -j DROP 2>/dev/null; do :; done
done

## Remove ALL copies of legacy interface-based DROP rules.
for iface in eth0 eth1 eth2; do
    while $DOCKER exec router iptables -D FORWARD -i "$iface" -j DROP 2>/dev/null; do :; done
    while $DOCKER exec router iptables -D FORWARD -o "$iface" -j DROP 2>/dev/null; do :; done
done

## Reset DNAT back to path-a.
## Insert the correct rule first so there is zero coverage gap, then flush
## and re-add cleanly. This prevents the brief iptables-F window from causing
## a spurious ERR in the client log that triggers the fault-detection agent.
$DOCKER exec router iptables -t nat -I PREROUTING 1 \
    -d 172.20.0.254 -p tcp --dport 80 -j DNAT --to-destination 172.21.0.10:80
$DOCKER exec router sh -c \
    'iptables -t nat -F PREROUTING; iptables -t nat -A PREROUTING -d 172.20.0.254 -p tcp --dport 80 -j DNAT --to-destination 172.21.0.10:80'

$DOCKER exec router curl -s -X POST http://localhost:5000/active-path \
    -H 'Content-Type: application/json' \
    -d '{"path":"a"}' > /dev/null
echo "Router reset to path-a."

echo "Fault state cleared."
