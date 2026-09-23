#!/usr/bin/env bash
# A2 evidence: drive `skilyst serve` the way the desktop shell does -- one ready
# line on stdout, then HTTP with the bearer token, then a graceful stop.
# Usage: tools/serve_smoke.sh [out-dir]   (defaults to evidence/a2-serve)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/evidence/a2-serve}"
mkdir -p "$OUT"
PORT="${SKILYST_SMOKE_PORT:-0}"
TOKEN="smoke-$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"

cd "$ROOT"
./bin/skilyst serve --port "$PORT" --token "$TOKEN" >"$OUT/ready.jsonl" 2>"$OUT/serve.log" &
SERVE_PID=$!
trap 'kill "$SERVE_PID" 2>/dev/null' EXIT

for _ in $(seq 1 100); do
  [ -s "$OUT/ready.jsonl" ] && break
  sleep 0.1
done
[ -s "$OUT/ready.jsonl" ] || { echo "serve never reported ready"; cat "$OUT/serve.log"; exit 1; }

BASE="http://127.0.0.1:$(python3 -c "import json,sys; print(json.load(open('$OUT/ready.jsonl'))['port'])")"
AUTH="Authorization: Bearer $TOKEN"
echo "base=$BASE"

# Evidence keeps the contract, never the capability: the token is redacted on disk.
python3 - "$OUT/ready.jsonl" >"$OUT/ready.redacted.jsonl" <<'PY'
import json, sys
row = json.load(open(sys.argv[1]))
row["token"] = "<redacted>"
print(json.dumps(row))
PY
mv "$OUT/ready.redacted.jsonl" "$OUT/ready.jsonl"

curl -s -H "$AUTH" "$BASE/health"  | python3 -m json.tool >"$OUT/health.json"
curl -s -H "$AUTH" "$BASE/config"  | python3 -m json.tool >"$OUT/config.json"
curl -s -H "$AUTH" "$BASE/doctor"  | python3 -m json.tool >"$OUT/doctor.json"
curl -s -H "$AUTH" "$BASE/sessions" >"$OUT/sessions-before.json"

echo "--- token required ---"
curl -s -o "$OUT/no-token.json" -w '%{http_code}\n' "$BASE/health"

echo "--- one message (non-streaming) ---"
curl -s -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"message":"In one sentence: what can you do?"}' "$BASE/message" \
  | python3 -m json.tool >"$OUT/message.json"
python3 - <<PY
import json
data = json.load(open("$OUT/message.json"))["data"]
print("session:", data["session_id"], "| ok:", data["ok"], "| model:", data["model"])
print("answer:", (data["answer"] or "")[:160].replace("\n", " "))
open("$OUT/session-id.txt", "w").write(data["session_id"])
PY

SID="$(cat "$OUT/session-id.txt")"
curl -s -H "$AUTH" "$BASE/session/$SID" | python3 -m json.tool >"$OUT/session.json"
curl -s -H "$AUTH" "$BASE/sessions" >"$OUT/sessions-after.json"

echo "--- one message (streaming / SSE) ---"
curl -s -N -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"message":"Say hello in three words.","stream":true}' "$BASE/message" >"$OUT/message.sse"
grep -c '^event: delta' "$OUT/message.sse" | sed 's/^/delta events: /'
grep -c '^event: done' "$OUT/message.sse" | sed 's/^/done events: /'

echo "--- shutdown ---"
curl -s -H "$AUTH" -X POST -d '{}' "$BASE/shutdown"; echo
for _ in $(seq 1 50); do kill -0 "$SERVE_PID" 2>/dev/null || break; sleep 0.1; done
if kill -0 "$SERVE_PID" 2>/dev/null; then echo "STILL RUNNING after /shutdown"; exit 1; fi
echo "stopped cleanly"
trap - EXIT
