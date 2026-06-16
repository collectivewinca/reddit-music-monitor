#!/usr/bin/env bash
# Guarded runner for the reddit-music-monitor 6h cron.
#
# Wraps l30d_monitor.py with operational automations that don't belong in the
# scraper itself:
#   - Watchdog: alert if the retired webshare-proxy zombie is running again;
#     rotate any monitor log that blows past 50MB (the original zombie hit 210MB).
#   - Alerting: push hard CANARY failures (zero-match / empty-fetch) to Telegram.
#     Low-capture is logged by the canary but NOT pushed — at the current ~3%
#     baseline that would just be noise until the retrieval fix lands.
#
# Telegram is OPT-IN and safe-by-default: alerts are a no-op until a chat id is
# written to .telegram_chat (one line). Until then this is pure local hygiene.
#
# Cron line:
#   0 */6 * * * cd /Users/aletviegas/projects/reddit-music-monitor && ./run.sh
set -uo pipefail

cd "$(dirname "$0")"
DIR="$(pwd -P)"
LOG="$DIR/cron.log"
PY=/opt/homebrew/bin/python3
TG=/Users/aletviegas/bin/telegram-via-ops          # @discoopsbot sender
CHAT_FILE="$DIR/.telegram_chat"                     # absent/empty => alerts disabled
MAX_LOG=$((50 * 1024 * 1024))                       # 50MB rotate threshold

ts() { date "+%Y-%m-%d %H:%M:%S"; }

notify() {  # $1 = message; never fail the run on a notify error
  [[ -r "$CHAT_FILE" ]] || return 0
  local cid; cid="$(tr -d '[:space:]' < "$CHAT_FILE")"
  [[ -n "$cid" ]] || return 0
  "$TG" "$cid" -m "$1" >/dev/null 2>&1 || echo "$(ts) - WARN - telegram notify failed" >> "$LOG"
}

# --- Watchdog 1: retired webshare-proxy zombie resurrected? ---
if pgrep -fl 'reddit-webshare-monitor' >/dev/null 2>&1; then
  echo "$(ts) - WARN - retired webshare zombie process detected" >> "$LOG"
  notify "⚠️ reddit-music-monitor: the retired webshare-proxy monitor is running again — kill it (it burns the shared Reddit budget)."
fi

# --- Watchdog 2: rotate oversized logs (keep last 5MB) ---
shopt -s nullglob
for f in "$DIR"/*.log; do
  sz=$(stat -f%z "$f" 2>/dev/null || echo 0)
  if (( sz > MAX_LOG )); then
    tail -c 5242880 "$f" > "$f.rot" && mv "$f.rot" "$f"
    echo "$(ts) - INFO - rotated $(basename "$f") (was $((sz/1048576))MB)" >> "$LOG"
    notify "🧹 reddit-music-monitor: rotated $(basename "$f") (was $((sz/1048576))MB)."
  fi
done

# --- Run the monitor; tee output to the log and capture for alerting ---
OUT="$("$PY" l30d_monitor.py 2>&1)"
printf '%s\n' "$OUT" >> "$LOG"

# --- Alert on hard failures only (the canary already logs low-capture) ---
if line=$(grep -m1 -E "CANARY (zero-match|empty-fetch)" <<<"$OUT"); then
  notify "🔴 reddit-music-monitor: ${line#*WARNING - }"
fi
