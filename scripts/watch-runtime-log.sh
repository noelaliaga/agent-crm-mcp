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
#          WATCH_STATE_FILE     recommended; remembers how far the log was read, so each
#                               run only looks at lines added since the previous run (at
#                               most LINES of them). Without it, every run re-reads the
#                               last LINES lines, and failure lines that are still in that
#                               window keep alerting after the runtime has recovered.
#          ALERT_WEBHOOK_URL    optional JSON webhook, as in healthcheck-alert.sh
#
# Exit codes: 0 no new failures, 1 failures found (alert sent), 2 usage or read error.
# A log that shrank (rotated or truncated) is read again from the start.
set -uo pipefail

log="${1:?usage: watch-runtime-log.sh LOGFILE [LINES]}"
lines="${2:-2000}"
pattern="${MCP_FAILURE_PATTERN:-MCP server .*(keepalive failed|unhandled errors in a TaskGroup|parked)}"
state="${WATCH_STATE_FILE:-}"

[[ -r "$log" ]] || { echo "cannot read $log" >&2; exit 2; }
[[ "$lines" =~ ^[0-9]+$ ]] || { echo "LINES must be a number" >&2; exit 2; }

if [[ -n "$state" ]]; then
  mkdir -p "$(dirname "$state")" || exit 2
  size="$(wc -c <"$log" | tr -d '[:space:]')"
  offset="$(cat "$state" 2>/dev/null || echo 0)"
  [[ "$offset" =~ ^[0-9]+$ ]] || offset=0
  if (( offset > size )); then offset=0; fi
  # Read exactly the bytes between the saved offset and the size measured above, so
  # lines appended meanwhile are left for the next run.
  hits="$(head -c "$size" "$log" | tail -c +"$((offset + 1))" | tail -n "$lines" |
    grep -Ec "$pattern" || true)"
  printf '%s\n' "$size" >"$state.tmp" && mv "$state.tmp" "$state"
  scope="new lines"
else
  hits="$(tail -n "$lines" "$log" | grep -Ec "$pattern" || true)"
  scope="the last $lines lines"
fi

if [[ "$hits" == "0" ]]; then
  exit 0
fi

message="agent runtime log shows $hits MCP failure line(s) in $scope of $(basename "$log")"
echo "$message" >&2
logger -t agent-crm-mcp "$message" 2>/dev/null || true
if [[ -n "${ALERT_WEBHOOK_URL:-}" ]]; then
  payload="$(python3 -c 'import json, sys; print(json.dumps({"text": sys.argv[1]}))' "$message")"
  curl -fsS -m 10 -H 'Content-Type: application/json' -d "$payload" "$ALERT_WEBHOOK_URL" \
    >/dev/null || echo "alert webhook failed" >&2
fi
exit 1
