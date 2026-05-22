#!/usr/bin/env bash
set -euo pipefail

## ip_forward is set via sysctls in docker-compose.yml before this script runs.
## Writing to /proc/sys/net/ipv4/ip_forward directly would fail (read-only in container).

## DNAT: redirect HTTP traffic arriving at the router front-net IP to the webserver
## via path-a by default. The agent /active-path endpoint swaps the DNAT destination.
##
## IMPORTANT: match on destination IP (-d 172.20.0.254) rather than -i ethX.
## Docker may assign eth0/eth1/eth2 in a different order depending on which networks
## were created first, making interface-name assumptions unreliable across restarts.
iptables -t nat -A PREROUTING -d 172.20.0.254 -p tcp --dport 80 \
    -j DNAT --to-destination 172.21.0.10:80

## Allow forwarded traffic for established/related connections (return path)
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
## Allow forwarding from front-net clients to either path webserver subnet
iptables -A FORWARD -s 172.20.0.0/24 -d 172.21.0.0/24 -j ACCEPT
iptables -A FORWARD -s 172.20.0.0/24 -d 172.22.0.0/24 -j ACCEPT

## Masquerade so the webserver sees the router IP, not the client IP.
## Match on destination subnet rather than outbound interface for the same reason.
iptables -t nat -A POSTROUTING -d 172.21.0.0/24 -j MASQUERADE
iptables -t nat -A POSTROUTING -d 172.22.0.0/24 -j MASQUERADE

## Start the router management API in the background
python3 /api.py &

## Keep container alive
tail -f /dev/null
