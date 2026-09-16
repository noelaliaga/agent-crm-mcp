#!/usr/bin/env bash
# Smoke test: the servers speak MCP with no network and no credentials, and the
# functional healthcheck passes against the fake PostgREST (synthetic data).
# Uses only the Python standard library. Exit code 0 = OK.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
WORK="$(mktemp -d)"
FAKE_PID=""
cleanup() {
  if [[ -n "$FAKE_PID" ]]; then kill "$FAKE_PID" 2>/dev/null || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT

# Empty HOME and a clean environment: nothing can be read from dotfiles.
clean_env=(env -i "PATH=$PATH" "HOME=$WORK" "PYTHONDONTWRITEBYTECODE=1" "PYTHONIOENCODING=utf-8")

session() {
  printf '%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    '{"jsonrpc":"2.0","id":3,"method":"ping"}' \
    "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":{}}}"
}

echo "1/4 crm server: protocol without configuration"
session crm_hoy | "${clean_env[@]}" "$PY" "$ROOT/servers/crm/server.py" >"$WORK/crm.out" 2>"$WORK/crm.err"
"$PY" - "$WORK/crm.out" "$WORK/crm.err" <<'PY'
import json, sys
out = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
assert [m["id"] for m in out] == [1, 2, 3, 4], out
assert out[0]["result"]["protocolVersion"] == "2025-11-25"
assert len(out[1]["result"]["tools"]) == 10
assert out[2]["result"] == {}
call = out[3]["result"]
assert call["isError"] is True and "CRM_SUPABASE_URL" in call["content"][0]["text"]
for line in open(sys.argv[2], encoding="utf-8"):
    json.loads(line)  # stderr must be structured JSON
print("   ok: initialize, 10 tools, ping, controlled error without config")
PY

echo "2/4 ghl_readonly server: protocol with synthetic fixtures"
session ghl_estado | "${clean_env[@]}" GHL_MOCK=1 "$PY" "$ROOT/servers/ghl_readonly/server.py" >"$WORK/ghl.out" 2>/dev/null
"$PY" - "$WORK/ghl.out" <<'PY'
import json, sys
out = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
assert len(out[1]["result"]["tools"]) == 7
assert out[3]["result"]["isError"] is False
print("   ok: 7 read-only tools, mock call")
PY

echo "3/4 --healthcheck against the fake PostgREST"
(cd "$ROOT" && exec "${clean_env[@]}" "$PY" -m devtools.fake_postgrest --token smoke-token-0123456789 \
  --port-file "$WORK/url" 2>/dev/null) &
FAKE_PID=$!
for _ in $(seq 1 50); do [[ -s "$WORK/url" ]] && break; sleep 0.1; done
[[ -s "$WORK/url" ]] || { echo "fake PostgREST did not start" >&2; exit 1; }
"${clean_env[@]}" CRM_SUPABASE_URL="$(cat "$WORK/url")" CRM_AGENT_TOKEN=smoke-token-0123456789 \
  "$PY" "$ROOT/servers/crm/server.py" --healthcheck 2>/dev/null | tee "$WORK/health.json" >/dev/null
echo "   ok: $(cat "$WORK/health.json")"

echo "4/4 --healthcheck fails with exit 1 when the backend is down"
kill "$FAKE_PID" 2>/dev/null || true
wait "$FAKE_PID" 2>/dev/null || true
FAKE_PID=""
set +e
"${clean_env[@]}" CRM_SUPABASE_URL="$(cat "$WORK/url")" CRM_AGENT_TOKEN=smoke-token-0123456789 \
  CRM_TIMEOUT_SECONDS=2 "$PY" "$ROOT/servers/crm/server.py" --healthcheck >/dev/null 2>&1
code=$?
set -e
[[ "$code" == "1" ]] || { echo "expected exit 1, got $code" >&2; exit 1; }
echo "   ok: exit 1"

echo "smoke OK"
