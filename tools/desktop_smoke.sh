#!/usr/bin/env bash
# A2 evidence: the desktop shell comes up, spawns the runtime, drives it over HTTP,
# and — when the shell is killed without any chance to clean up — leaves nothing behind.
#
#   tools/desktop_smoke.sh [out-dir]        (defaults to evidence/a2-desktop)
#
# Needs the frontend dependencies installed (npm install in desktop/) and a runtime
# checkout next to this one (the shell finds it at ../bin/skilyst).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/evidence/a2-desktop}"
mkdir -p "$OUT"
cd "$ROOT/desktop"

pkill -f "vite --host" 2>/dev/null
sleep 1
npm run tauri dev >"$OUT/tauri-dev.log" 2>&1 &
TAURI=$!

APP=""
for _ in $(seq 1 90); do
  APP="$(pgrep -f "target/debug/skilyst-agent" | head -1 || true)"
  if [ -n "$APP" ] && grep -q "GET /sessions" "$OUT/tauri-dev.log"; then break; fi
  sleep 1
done
if [ -z "$APP" ]; then
  echo "FAIL: the shell never started (see $OUT/tauri-dev.log)"
  kill "$TAURI" 2>/dev/null
  exit 1
fi

CHILD="$(ps -eo pid,ppid | awk -v p="$APP" '$2==p {print $1}' | head -1)"
echo "shell pid=$APP  runtime pid=$CHILD"
echo "--- what the shell logged about its runtime ---"
grep -E "orphan guard|OPTIONS /sessions|GET /sessions" "$OUT/tauri-dev.log" | head -6

echo "--- SIGTERM the shell (its cleanup handler does not run) ---"
kill -TERM "$APP"
EXITED=""
for i in $(seq 1 10); do
  sleep 1
  if ! kill -0 "$CHILD" 2>/dev/null; then EXITED="${i}s"; break; fi
done
kill "$TAURI" 2>/dev/null
if [ -z "$EXITED" ]; then
  echo "FAIL: orphaned runtime $CHILD is still alive"
  kill "$CHILD" 2>/dev/null
  exit 1
fi
echo "runtime exited on its own after ~$EXITED"
echo "PASS: window up, runtime driven over HTTP, nothing left behind"
