#!/usr/bin/env bash
set -euo pipefail

## ip_forward is set via sysctls in docker-compose.yml before this script runs.
## Writing to /proc/sys/net/ipv4/ip_forward directly would fail (read-only in container).

## DNAT: redirect HTTP traffic arriving from the client (front-net/eth0)
## to the webserver via path-a by default (172.21.0.10).
## The agent's /active-path endpoint swaps this rule to path-b when needed.
iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 80 \
    -j DNAT --to-destination 172.21.0.10:80

## Allow forwarded traffic in both directions
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -i eth0 -o eth1 -j ACCEPT
iptables -A FORWARD -i eth0 -o eth2 -j ACCEPT

## Masquerade so the webserver sees the router's IP, not the client's
iptables -t nat -A POSTROUTING -o eth1 -j MASQUERADE
iptables -t nat -A POSTROUTING -o eth2 -j MASQUERADE

## Start the router management API in the background
python3 /api.py &

## Keep container alive
tail -f /dev/null
