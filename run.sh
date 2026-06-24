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
QUO_KEY_FILE="$HOME/.quo/credentials"               # raw Quo API key (NO 'Bearer' prefix)
QUO_FROM="+14159367377"                             # Quo "Primary" number (sender)
ALERT_TO_FILE="$HOME/.quo/alert_to"                 # destination cell, E.164; absent => alerts disabled
MAX_LOG=$((50 * 1024 * 1024))                       # 50MB rotate threshold

ts() { date "+%Y-%m-%d %H:%M:%S"; }

notify() {  # $1 = message; SMS via Quo (api.quo.com). Never fail the run on a notify error.
  [[ -r "$QUO_KEY_FILE" && -r "$ALERT_TO_FILE" ]] || return 0
  local key to body
  key="$(tr -d '[:space:]' < "$QUO_KEY_FILE")"
  to="$(tr -d '[:space:]' < "$ALERT_TO_FILE")"
  [[ -n "$key" && -n "$to" ]] || return 0
  body="$("$PY" -c 'import json,sys; print(json.dumps({"content":sys.argv[1],"from":sys.argv[2],"to":[sys.argv[3]]}))' "$1" "$QUO_FROM" "$to")"
  /usr/bin/curl -sS --max-time 15 -X POST "https://api.quo.com/v1/messages" \
    -H "Authorization: ${key}" -H "Content-Type: application/json" --data "$body" \
    >/dev/null 2>&1 || echo "$(ts) - WARN - quo SMS notify failed" >> "$LOG"
}

# --- Single-instance guard for the WHOLE pipeline ---
# l30d_monitor.py's fcntl lock only covers its own phase; mine_comments +
# dashboard + publish run after it releases. macOS cron has no overlap guard, so
# a long run can collide with the next 6h fire. Atomic mkdir lock (no flock on
# macOS) + PID-liveness check that reclaims a stale lock from a crashed run.
LOCKDIR="$DIR/.run.lock.d"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  holder="$(cat "$LOCKDIR/pid" 2>/dev/null || true)"
  if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
    echo "$(ts) - WARN - run already in progress (pid $holder); skipping" >> "$LOG"
    exit 0
  fi
  echo "$(ts) - WARN - reclaiming stale run lock (holder ${holder:-?} gone)" >> "$LOG"
fi
echo "$$" > "$LOCKDIR/pid"
trap 'rm -rf "$LOCKDIR"' EXIT

# --- Portable timeout (no `timeout`/`gtimeout` on stock macOS) ---
# Runs the command in the background and TERMs it after N seconds; returns the
# command's own exit code, or 124 on timeout (matching GNU `timeout`).
run_with_timeout() {  # $1 = seconds; $2.. = command (may be a shell function)
  local secs="$1"; shift
  "$@" &
  local cmd_pid=$!
  ( sleep "$secs"; kill -TERM "$cmd_pid" 2>/dev/null ) &
  local killer=$!
  local rc=0
  wait "$cmd_pid" 2>/dev/null || rc=$?
  if ! kill -0 "$killer" 2>/dev/null; then
    rc=124              # killer already fired its TERM -> this was a timeout
  else
    kill -TERM "$killer" 2>/dev/null   # cmd finished first; cancel the killer
  fi
  wait "$killer" 2>/dev/null || true
  return "$rc"
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
    rm -f "$f.rot"  # clear any leftover from a prior interrupted rotation
    tail -c 5242880 "$f" > "$f.rot" && mv "$f.rot" "$f"
    echo "$(ts) - INFO - rotated $(basename "$f") (was $((sz/1048576))MB)" >> "$LOG"
    notify "🧹 reddit-music-monitor: rotated $(basename "$f") (was $((sz/1048576))MB)."
  fi
done

# --- Run the monitor; tee output to the log and capture for alerting ---
# l30d_monitor.py writes a .run_ok sentinel ("1"/"0") telling us whether this run
# is healthy enough to (re)publish — so a thin/throttled run can't present stale
# data as fresh, and the dashboard is rendered exactly ONCE per run (here, after
# comment-mining) instead of twice.
rm -f "$DIR/.run_ok"
OUT="$("$PY" l30d_monitor.py 2>&1)"
printf '%s\n' "$OUT" >> "$LOG"

# --- Alert on hard failures only (the canary already logs low-capture) ---
if line=$(grep -m1 -E "CANARY (zero-match|empty-fetch)" <<<"$OUT"); then
  notify "🔴 reddit-music-monitor: ${line#*WARNING - }"
fi

RUN_OK="$(cat "$DIR/.run_ok" 2>/dev/null || echo 0)"
if [[ "$RUN_OK" != "1" ]]; then
  echo "$(ts) - INFO - run not healthy (canary gate); skipping mine + dashboard + publish" >> "$LOG"
  exit 0
fi

# --- Comment-intelligence: mine artist recommendations from thread comments ---
# Best-effort (needs Chrome running with CDP for opencli's reddit read); never
# blocks. Hard-capped so an unresponsive Chrome/CDP can't stall the pipeline.
run_with_timeout 300 "$PY" mine_comments.py >> "$LOG" 2>&1 \
  || echo "$(ts) - WARN - mine_comments failed or timed out" >> "$LOG"

# --- Render the MINY A&R Radar dashboard (now includes mined artists) ---
"$PY" gen_dashboard.py >> "$LOG" 2>&1 || echo "$(ts) - WARN - dashboard render failed" >> "$LOG"

# --- Publish the dashboard to a stable here.now slug (updates in place) ---
SLUG=olive-monsoon-n9ct
STAGE="$(mktemp -d)/d" && mkdir -p "$STAGE" && cp "$DIR/index.html" "$STAGE/index.html"
do_publish() { ( cd "$STAGE" && bash "$HOME/.claude/skills/here-now/scripts/publish.sh" . --slug "$SLUG" --client claude-code ); }
if run_with_timeout 120 do_publish >> "$LOG" 2>&1; then
  echo "$(ts) - INFO - published -> https://$SLUG.here.now/" >> "$LOG"
else
  echo "$(ts) - WARN - here.now publish failed or timed out" >> "$LOG"
fi
rm -rf "$STAGE"
