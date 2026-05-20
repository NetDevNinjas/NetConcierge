#!/usr/bin/env bash
TARGET_URL="${TARGET_URL:-http://172.20.0.254/}"
INTERVAL="${INTERVAL:-3}"

while true; do
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$TARGET_URL" || echo "000")
    latency=$(curl -s -o /dev/null -w "%{time_total}" --max-time 5 "$TARGET_URL" 2>/dev/null || echo "timeout")

    if [[ "$http_code" == "200" ]]; then
        echo "[${timestamp}] OK  | HTTP ${http_code} | ${latency}s"
    else
        echo "[${timestamp}] ERR | HTTP ${http_code} | ${latency}s" >&2
    fi

    sleep "$INTERVAL"
done
