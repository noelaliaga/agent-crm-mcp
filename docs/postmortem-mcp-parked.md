# Postmortem: three MCP servers "parked" for 20 days while every dashboard was green

**Period:** 26 Aug 2026 → 15 Sep 2026
**Impact:** the agent (Hermes Agent) could not use its CRM, GoHighLevel or context tools. Every attempt failed and nobody was alerted. Between 26 Aug and 12 Sep the runtime logged about **1,234** failed attempts for the CRM server alone. The figures in this repository stop before 13 Sep 2026; failures continued after that date until the restart.
**Status:** resolved by restarting the runtime process. The root cause is in a third-party runtime and is not fixed upstream by this repository. This repository adds the detection that was missing.
**Blameless:** the servers in this repository were not the cause. The failure was in how the monitoring was designed, so this postmortem describes a design gap, not a person.

## Summary

A short network outage made a long-lived agent runtime lose its stdio MCP sessions. Its reconnect path never recovered. All three MCP servers stayed marked as unavailable ("parked") for 20 days. During that time:

- the runtime process reported `running`;
- the operations dashboard stayed green;
- a separate monitoring service reported the system as "healthy".

All three signals measured whether a process was alive, not whether a tool worked.

## Timeline (from the runtime logs; identifiers masked)

| When | What happened |
|---|---|
| 21–24 Aug | Normal operation. At every start or reconnect, the runtime logs that the `crm` server registered its tools. |
| 25–26 Aug | Network errors reach the runtime's Telegram connector (DNS resolution failures). |
| **26 Aug, afternoon** | A network cut, also visible in Telegram. |
| **About a minute later** | The runtime logs `MCP server 'ghl' keepalive failed, triggering reconnect`. |
| 26 Aug onwards | `crm`, `ghl` and a third, context server fail **at the same time** with `unhandled errors in a TaskGroup (1 sub-exception)` and are parked. The servers' own stderr contains only start-up lines, with no traceback. |
| 26 Aug → 12 Sep | Failed `crm` attempts per day: 26/08 = 4 · 07/09 = 56 · 08/09 = 182 · 09/09 = 92 · 10/09 = 138 · 11/09 = 428 · 12/09 = 334 → **1,234**. On the days not listed, no CRM attempts were recorded. The other two servers show the same pattern. |
| 7 Sep | A run of the weekly cron job loses the MCP connection midway and still delivers a message, without CRM data. The scheduler looks healthy. |
| **15 Sep (diagnosis)** | Each server is started in a **fresh process** with a minimal protocol client (no network, empty home directory). All of them initialize and list their tools. This points at the long-lived runtime process, not at the servers. |
| **15 Sep (restart)** | The runtime gateway is restarted through its process supervisor (launchd). |
| Seconds later | The new process registers the tools of all three servers again. No functional tool call through the runtime was verified at that point. |

## Root cause

The third-party runtime's reconnect path for stdio MCP sessions did not recover after the network outage, and the process stayed up for a long time afterwards. The inference is strong but not closed by a traceback, because the runtime logged only the TaskGroup summary. The evidence:

- The three servers failed **at the same instant**, even though only one of them (`ghl`) uses the network for its keepalive.
- Between the working and the failing period, the servers did not change, and neither did the runtime's MCP SDK or its Python installation (checked from package metadata and file dates).
- The same servers initialized in a fresh process, and the runtime registered them again right after the restart.

## Why it lasted 20 days: detection

| Layer | What it checked | What it said |
|---|---|---|
| Process supervisor | The process exists | running |
| Operations dashboard | Heartbeat or last activity | green |
| Monitoring service | Its own ingestion and state | "healthy" |
| **Nothing** | **Can the agent call a tool and get data?** | — |

Nothing checked that function. The failures were only visible in a log that nobody alerted on.

## What this repository changes

1. **Functional healthcheck.** `servers/crm/server.py --healthcheck` runs the same read path the tools use against the real backend. It prints a JSON report and exits with `0` (ok), `1` (backend error) or `2` (misconfigured). `scripts/healthcheck-alert.sh` and `examples/monitoring/` run it every 15 minutes and send a webhook or syslog alert when it fails.
2. **In-band canary.** The `crm_salud` tool, together with `examples/hermes/cron-canary.example.json`, asks the *runtime itself* to call a tool. A fresh-process healthcheck **cannot** detect a stuck session inside a long-lived runtime, which is exactly this incident. The canary can, if it is paired with a dead-man's switch that alerts when `HEALTH_OK` stops arriving.
3. **Runtime log watcher.** `scripts/watch-runtime-log.sh` alerts on the failure lines seen in this incident (`keepalive failed`, `unhandled errors in a TaskGroup`, `parked`). With `WATCH_STATE_FILE` it only looks at lines added since its previous run, so old failures do not alert again after recovery.
4. **Useful server logs.** The servers now write structured JSON lines to stderr (`startup`, `tool_call` with duration and error kind, `shutdown`), with no arguments and no PII. A parked session then shows up as silence after `startup`, not as an empty log.

## What is still open

- The runtime's reconnect bug has not been reported or fixed upstream from here.
- Restarting the runtime is still manual. An automatic restart on repeated canary failure is possible but has not been implemented.
- The canary and the log watcher are examples: they have not run against the live runtime.
- A cron run whose data tool fails should not be delivered as a normal result. The scheduler used here does not support that rule; the canary is the workaround.
