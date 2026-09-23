#!/bin/sh
# Skilyst environment self-check (skilyst/doctor).
#
# Prints one report answering five questions: credential present, installed
# skills intact, platform reachable, required nodes registered, scopes granted.
# Never prints a secret: the credential values come from `skilyst config`, which
# redacts them, and this script never reads the env file itself.
#
# Usage: sh scripts/doctor.sh [skill-id]
# Exit:  0 ok | 2 a rule said no | 3 incomplete | 4 platform call failed

set -u

SKILL_ID="${1:-}"
TMPDIR_DOCTOR="$(mktemp -d "${TMPDIR:-/tmp}/skilyst-doctor.XXXXXX")"
trap 'rm -rf "$TMPDIR_DOCTOR"' EXIT INT TERM

if [ -n "${SKILYST_BIN:-}" ]; then
  SKILYST="$SKILYST_BIN"
elif command -v skilyst >/dev/null 2>&1; then
  SKILYST="skilyst"
elif [ -x "./bin/skilyst" ]; then
  SKILYST="./bin/skilyst"
else
  echo "doctor: cannot find the skilyst runtime (set SKILYST_BIN, put skilyst on PATH, or run from the repo root)" >&2
  exit 2
fi

ENV_FILE="${SKILYST_ENV_FILE:-$HOME/.skilyst/env}"
echo "== skilyst doctor =="
echo "runtime     : $SKILYST"
if [ -f "$ENV_FILE" ]; then
  echo "env file    : $ENV_FILE (present)"
else
  echo "env file    : $ENV_FILE (MISSING -- export BEEHIVE_PLATFORM_AK/SK or create it with mode 0600)"
fi

run() { # run <label> <outfile> <args...>
  label="$1"; out="$2"; shift 2
  if "$SKILYST" "$@" >"$out" 2>"$out.err"; then
    status=0
  else
    status=$?
  fi
  echo "$status"
}

echo
echo "-- 1. resolved configuration (secrets redacted) --"
rc="$(run config "$TMPDIR_DOCTOR/config.json" config)"
cat "$TMPDIR_DOCTOR/config.json" 2>/dev/null || cat "$TMPDIR_DOCTOR/config.json.err"
[ "$rc" -ne 0 ] && echo "doctor: 'skilyst config' failed (exit $rc) -- the credential is not resolvable" >&2

echo
echo "-- 2. installed-skill integrity --"
rc_doctor="$(run doctor "$TMPDIR_DOCTOR/doctor.json" doctor)"
cat "$TMPDIR_DOCTOR/doctor.json" 2>/dev/null || cat "$TMPDIR_DOCTOR/doctor.json.err"

if [ -n "$SKILL_ID" ]; then
  echo
  echo "-- 3. node preflight for $SKILL_ID --"
  rc_preflight="$(run doctor "$TMPDIR_DOCTOR/preflight.json" doctor "$SKILL_ID")"
  cat "$TMPDIR_DOCTOR/preflight.json" 2>/dev/null || cat "$TMPDIR_DOCTOR/preflight.json.err"
else
  echo
  echo "-- 3. node preflight --"
  echo "skipped: pass a skill id, e.g. 'sh scripts/doctor.sh skilyst/embed-video'"
  rc_preflight=0
fi

echo
echo "-- 4. scope boundary (client gate vs server vs anonymous) --"
rc_probe="$(run authz-probe "$TMPDIR_DOCTOR/probe.json" authz-probe)"
if command -v python3 >/dev/null 2>&1; then
  python3 - "$TMPDIR_DOCTOR/probe.json" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception as exc:                      # noqa: BLE001 - report, never hide
    print(f"doctor: could not parse the probe output: {exc}")
    raise SystemExit(0)
print("base_url    :", data.get("base_url"))
print("token_scope :", ", ".join(data.get("token_scope") or []))
print("refused by the client gate (by design):", ", ".join(data.get("denied_prefixes") or []))
for row in data.get("probes") or []:
    gate = row.get("scoped_client_gate")
    marker = "refused-client-side" if isinstance(gate, str) else gate
    print(f"  {row.get('method'):4} {row.get('path'):52} scoped={marker} anonymous={row.get('anonymous')}")
PY
else
  cat "$TMPDIR_DOCTOR/probe.json" 2>/dev/null || cat "$TMPDIR_DOCTOR/probe.json.err"
fi

echo
echo "-- 5. quota --"
echo "not readable from the agent runtime: /api/v1/billing/wallet is outside the"
echo "restricted key's scope (the client gate refuses it; the server still answers"
echo "200 to GET with a restricted key -- a recorded backend scope gap, not a"
echo "number to invent). Cost decisions stay quote-first from node pricing."

echo
echo "== summary =="
echo "config=$rc doctor=$rc_doctor preflight=$rc_preflight authz_probe=$rc_probe"
if [ "${rc:-0}" -ne 0 ]; then
  exit "$rc"
elif [ "${rc_doctor:-0}" -ne 0 ]; then
  exit "$rc_doctor"
elif [ "${rc_preflight:-0}" -ne 0 ]; then
  exit "$rc_preflight"
elif [ "${rc_probe:-0}" -ne 0 ]; then
  exit "$rc_probe"
fi
exit 0
