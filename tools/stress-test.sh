#!/usr/bin/env bash
# tools/stress-test.sh - bandwidth stress test across all 10 xProton locations.
#
# Pulls a LOT of download traffic simultaneously through every SOCKS port to
# observe whether Proton bans any account under sustained heavy load.
# (See README -> "Live test results" for what we learned.)
#
# Usage (on the server):
#   bash tools/stress-test.sh <MB_per_location>      e.g. 1000 = 1 GiB each
#   bash tools/stress-test.sh stop
#
# Only downloads (server -> Proton -> server). Nothing is uploaded back,
# so it never touches your home network.
#
# NOTE: hammering a datacenter link with terabytes can cost egress money on
# a metered VPS. You were warned. Use at your own risk.

set -uo pipefail

TARGET_MB="${1:-512}"
RUN_DIR=/run/xproton-stress
PORTS=(64201 64202 64203 64204 64205 64206 64207 64208 64209 64210)

mkdir -p "$RUN_DIR"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >> "$RUN_DIR/results.log"; echo "[$(date +%H:%M:%S)] $*"; }

# stop: kill every stress worker (be careful not to match our own shell)
if [ "${1:-}" = "stop" ]; then
  echo "stopping stress workers..."
  pkill -9 -f 'xproton-stress-runner' 2>/dev/null
  pkill -9 -f 'speed.cloudflare.com/__down' 2>/dev/null
  ps -eo pid,args | awk '/heavy_port|stress_wg/{print $1}' | xargs -r kill -9 2>/dev/null
  rm -rf "$RUN_DIR"
  echo "stopped."
  exit 0
fi

# --- worker: pull `MB` MiB through one port in 100 MiB chunks ---------------
stress_wg() {
  local P=$1 MB=$2
  local URL="https://speed.cloudflare.com/__down?bytes=104857600"  # 100 MiB
  local total=0 warns=0
  local t0=$(date +%s)
  while [ "$total" -lt "$MB" ]; do
    if curl -sS -m 120 --socks5-hostname 127.0.0.1:$P "$URL" -o /dev/null 2>/dev/null; then
      total=$((total + 100)); warns=0
      log "[port $P] $total/$MB MiB"
    else
      warns=$((warns + 1))
      if [ "$warns" -ge 3 ]; then
        log "[port $P] FAILING 3x consecutively - tunnel dead? (account banned?)"
        break
      fi
      sleep 5
    fi
  done
  local el=$(( $(date +%s) - t0 ))
  log "[port $P] DONE $total MiB in ${el}s"
}

# --- sanity: count live locations ------------------------------------------
live=0
for P in "${PORTS[@]}"; do
  if curl -sS -m 8 --socks5-hostname 127.0.0.1:$P \
      https://www.gstatic.com/generate_204 -o /dev/null 2>/dev/null; then
    live=$((live + 1))
  fi
done
echo "healthy locations: $live/10"
echo "target: ${TARGET_MB} MiB per location"

# --- launch one worker per location -----------------------------------------
for P in "${PORTS[@]}"; do
  ( 
    # keep one "runner" marker so `stop` can find us
    touch /run/xproton-stress-runner.$$
    stress_wg "$P" "$TARGET_MB"
  ) &
done

echo "launched. watch with:  tail -f $RUN_DIR/results.log"
echo "stop with:             bash tools/stress-test.sh stop"
wait
echo ""
echo "===== FINAL RESULTS ====="
cat "$RUN_DIR/results.log" 2>/dev/null