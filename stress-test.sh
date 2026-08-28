#!/usr/bin/env bash
# stress-test.sh - hammer all 10 xProton SOCKS ports with download traffic.
#
# Downloads a *lot* of data through each location's SOCKS5 proxy so we can
# watch whether Proton bans any account under sustained heavy load.
#
#   bash stress-test.sh <MB_per_loc>        e.g. 1000 = 1 GB per location
#   STOP: bash stress-test.sh stop
#
# Only downloads (server -> Proton -> server). No upload back to you.

set -uo pipefail

DUR_MB="${1:-500}"          # MiB to pull through EACH location
RUN_DIR=/run/xproton-stress
mkdir -p "$RUN_DIR"

PORTS=(64201 64202 64203 64204 64205 64206 64207 64208 64209 64210)

if [ "${1:-}" = "stop" ]; then
  echo "stopping stress workers..."
  pkill -f "stress_wg" 2>/dev/null
  pkill -f "speed.cloudflare.com/__down" 2>/dev/null
  rm -rf "$RUN_DIR"
  echo "stopped"
  exit 0
fi

LIVE=0
for P in "${PORTS[@]}"; do
  if curl -sS -m 8 --socks5-hostname 127.0.0.1:$P \
      https://www.gstatic.com/generate_204 -o /dev/null 2>/dev/null; then
    LIVE=$((LIVE+1))
  fi
done
echo "locations combined total *baseline* download: $(nproc) workers/port"
echo "healthy proxies: $LIVE/10"

# --- helper: download MB through one port in a loop -----------------------
stress_wg() {
  local P=$1 MB=$2 ID=$3
  local URL="https://speed.cloudflare.com/__down?bytes=104857600"  # 100 MiB per GET
  local total=0 start_done warns=0
  start_done=$(date +%s)
  while [ "$total" -lt "$MB" ]; do
    # 100 MiB curl through the proxy; write to /dev/null (no disk use)
    local got=0
    if curl -sS -m 120 --socks5-hostname 127.0.0.1:$P "$URL" -o /dev/null 2>/dev/null; then
      got=100
      warns=0
    else
      warns=$((warns+1))
      # 3 consecutive failures = the tunnel/account is probably dead/banned
      if [ "$warns" -ge 3 ]; then
        echo "$(date +%H:%M:%S) [port $P] FAILING consistently - tunnel dead? (account banned?)" \
          >> "$RUN_DIR/results.log"
        break
      fi
      sleep 5
      continue
    fi
    total=$((total+got))
    echo "$(date +%H:%M:%S) [port $P] $total/$MB MiB" >> "$RUN_DIR/results.log"
  done
  local el=$(( $(date +%s) - start_done ))
  echo "$(date +%H:%M:%S) [port $P] DONE $total MiB in ${el}s (~$(( total*8/el )) kbps w/1 worker)" >> "$RUN_DIR/progress.log"
}

for i in "${!PORTS[@]}"; do
  P="${PORTS[$i]}"
  # skip ports that aren't listening
  if ! curl -sS -m 8 --socks5-hostname 127.0.0.1:$P \
      https://www.gstatic.com/generate_204 -o /dev/null 2>/dev/null; then
    echo "skipping dead port $P"
    continue
  fi
  : > "$RUN_DIR/w$i.marker"
  ( stress_wg "$P" "$DUR_MB" "$i" > "$RUN_DIR/w$i.out" 2>&1 ) &
done

echo "workers launched for ports 64201-64210. Live progress:"
echo "  tail -f /run/xproton-stress/results.log"
echo "  (all 10 ports hammering simultaneously, ~100 MiB requests)"
wait
echo ""
echo "===== FINAL RESULTS ====="
cat "$RUN_DIR/results.log" 2>/dev/null
echo ""
echo "total data pulled: $(grep -c DONE "$RUN_DIR/progress.log" 2>/dev/null || echo 0) locations finished"