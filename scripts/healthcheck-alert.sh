#!/usr/bin/env bash
# Run the functional healthcheck and raise an alert when it fails.
# Meant for cron or launchd (see examples/monitoring/).
#
#   CRM_ENV_FILE       optional chmod-600 file with CRM_* variables (passed as --env-file)
#   ALERT_WEBHOOK_URL  optional; receives {"text": "..."} as JSON (Slack-style webhook)
#   PYTHON             interpreter (default python3)
#
# Exit code is the healthcheck's: 0 ok, 1 backend error, 2 misconfigured.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
args=(--healthcheck)
if [[ -n "${CRM_ENV_FILE:-}" ]]; then args+=(--env-file "$CRM_ENV_FILE"); fi

errfile="$(mktemp)"
trap 'rm -f "$errfile"' EXIT

report="$("$PY" "$ROOT/servers/crm/server.py" "${args[@]}" 2>"$errfile")"
code=$?
if [[ "$code" == "0" ]]; then
  exit 0
fi

# The server redacts its own logs; this second pass also covers tracebacks and messages
# printed before logging starts (for example "cannot read --env-file").
last_err="$(grep -v '^[[:space:]]*$' "$errfile" | tail -n 1 |
  sed -E 's/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*/[redacted-jwt]/g;
          s/([Bb]earer)[[:space:]]+[^[:space:]"]+/\1 [redacted]/g' | cut -c1-300)"

message="agent-crm-mcp healthcheck FAILED on $(hostname -s) (exit $code): ${report:-no output}"
if [[ -n "$last_err" ]]; then message+=" | stderr: $last_err"; fi
echo "$message" >&2
logger -t agent-crm-mcp "$message" 2>/dev/null || true

if [[ -n "${ALERT_WEBHOOK_URL:-}" ]]; then
  payload="$("$PY" -c 'import json, sys; print(json.dumps({"text": sys.argv[1]}))' "$message")"
  curl -fsS -m 10 -H 'Content-Type: application/json' -d "$payload" "$ALERT_WEBHOOK_URL" \
    >/dev/null || echo "alert webhook failed" >&2
fi
exit "$code"
