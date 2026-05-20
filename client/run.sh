#!/usr/bin/env bash
TARGET_URL="${TARGET_URL:-http://172.20.0.254/}"
INTERVAL="${INTERVAL:-3}"

while true; do
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    ## Single curl call captures both values; exits non-zero on timeout/failure
    ## but the -w format string is always written, so no fallback echo needed.
    result=$(curl -s -o /dev/null -w "%{http_code} %{time_total}" \
        --max-time 5 "$TARGET_URL" 2>/dev/null) || result="000 timeout"

    http_code=$(echo "$result" | awk '{print $1}')
    latency=$(echo "$result"  | awk '{print $2}')

    if [[ "$http_code" == "200" ]]; then
        echo "[${timestamp}] OK  | HTTP ${http_code} | ${latency}s"
    else
        echo "[${timestamp}] ERR | HTTP ${http_code} | ${latency}s" >&2
    fi

    sleep "$INTERVAL"
done
