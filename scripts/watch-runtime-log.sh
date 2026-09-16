#!/usr/bin/env bash
# Alert when an agent runtime's log shows MCP connection failures.
#
# --healthcheck proves the server and the database work in a fresh process. It cannot see
# a long-lived agent runtime whose own MCP session is stuck (the failure described in
# docs/postmortem-mcp-parked.md). This script covers that gap from the outside by looking
# at the runtime's log.
#
#   usage: watch-runtime-log.sh LOGFILE [LINES]
#   env:   MCP_FAILURE_PATTERN  extended regex (default matches the signatures seen in the
#                               incident: keepalive failures, TaskGroup errors, "parked")
#          ALERT_WEBHOOK_URL    optional JSON webhook, as in healthcheck-alert.sh
set -uo pipefail

log="${1:?usage: watch-runtime-log.sh LOGFILE [LINES]}"
lines="${2:-2000}"
pattern="${MCP_FAILURE_PATTERN:-MCP server .*(keepalive failed|unhandled errors in a TaskGroup|parked)}"

[[ -r "$log" ]] || { echo "cannot read $log" >&2; exit 2; }
hits="$(tail -n "$lines" "$log" | grep -Ec "$pattern" || true)"
if [[ "$hits" == "0" ]]; then
  exit 0
fi

message="agent runtime log shows $hits MCP failure line(s) in the last $lines lines of $(basename "$log")"
echo "$message" >&2
logger -t agent-crm-mcp "$message" 2>/dev/null || true
if [[ -n "${ALERT_WEBHOOK_URL:-}" ]]; then
  payload="$(python3 -c 'import json, sys; print(json.dumps({"text": sys.argv[1]}))' "$message")"
  curl -fsS -m 10 -H 'Content-Type: application/json' -d "$payload" "$ALERT_WEBHOOK_URL" \
    >/dev/null || echo "alert webhook failed" >&2
fi
exit 1
