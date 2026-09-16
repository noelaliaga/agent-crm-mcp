# Evidence from real use (aggregates only)

The prototype in `prototype/crm_server_v0.py` was used with a real, single-user prospecting CRM in August 2026. **That dataset is not published and never will be.** It contains third-party business contact data. This page gives only counts, and every count covers activity before 13 Sep 2026. They come from the agent runtime's session database and logs, and from aggregate queries on the CRM audit table. The analysis opened no message content, cron output or credential.

## Definitions

- **Demonstrated capability:** worked end to end at least once, with evidence.
- **Sustained use:** a real time series of use.
- **Not demonstrated:** the code exists, but there is no evidence that it worked end to end.

## Counts

| What | Number | Notes |
|---|---|---|
| CRM tool calls made by the agent | **69** | 67 on 21 Aug (interactive CLI sessions, one Telegram session and background worker sessions) plus 2 inside cron runs on 24 Aug |
| `crm_cambios` (audit read) calls | 6 | 21 Aug |
| Notes written by the agent | **1 of 3 attempts** | The other 2 attempts returned errors |
| Status or next-step changes made by the agent | **0** | The write tools existed but were never called |
| Rows in the audit table (`prospecto_cambios`) | **102** | Over 3 days |
| Audit rows made with `service_role` (the agent's credential at the time) | **0** | This is how "the agent never changed a status" was proved |
| Distinct MCP clients that ran this family of hand-written servers | **3** | Hermes Agent (crm, context server) · OpenClaw (context and content servers) · Claude Code (the GoHighLevel adapter's original). The CRM server itself was used only from Hermes |
| Weekly cron job ("Monday call list", `0 9 * * 1`) | **5 runs on 3 consecutive Mondays** | 24 Aug (3 runs), 31 Aug, 7 Sep. 3 deliveries (1 with CRM data, 1 without CRM data, 1 that delivered an inactivity-timeout error), 1 "no delivery target resolved", 1 silent run |
| Cron runs that delivered a list **with CRM data** | **1** | 24 Aug, late evening, outside the schedule and probably triggered by hand |
| Provider failover in that run | **1** | The cloud model provider answered HTTP 503; the runtime switched to a local model on its own, which called the CRM tool and produced the delivered answer. Failover is a Hermes Agent feature; I configured it |
| Failed CRM attempts during the outage | ~1,234 | 26 Aug → 12 Sep; see [postmortem-mcp-parked.md](postmortem-mcp-parked.md) |

After the runtime restart described in the postmortem, the runtime registered the tools of all three servers again. No functional call through the runtime was verified at that point, so no figure is given for it.

## Capability and use

| Piece | Demonstrated capability | Sustained use |
|---|---|---|
| Agent → MCP → CRM, reads | Yes (69 calls) | No (2 days) |
| Agent writes a note | Yes, once | No |
| Agent changes a status or next step | **Not demonstrated** in real use | No |
| Audit trigger on the CRM | Yes (102 rows) | Short series (3 days) |
| Cron → CRM → Telegram with data | Yes, once, including a provider failover to a local model | No |
| Scheduler + Telegram delivery (without requiring CRM data) | Yes | Weak: 3 Mondays |
| Background worker sessions closing their task | **Not demonstrated**: 4 of 4 worker runs read the CRM but did not complete the task protocol. The model was a low-cost paid model (not a free tier); whether the cause was the model or the prompt was not isolated | No |
| Functional monitoring of the MCP tools | Did not exist | — |

## What this repository adds, and how far it has been verified

| Addition | Verified how |
|---|---|
| Restricted `crm_agent` identity (column grants, audit rows filtered to readable columns, no DELETE, database-assigned note author, `actor` in the audit) | Written in `supabase/migrations/0001_crm_min.sql`. Unit tests check that the Python allowlist, the fake backend and the SQL grants are the same list. **The Postgres integration tests (`tests/integration/`) run only in CI. They were not executed where this repository was prepared, because no Postgres was available there.** |
| Write modes, validation, redaction, name resolution | Unit tests against a fake PostgREST (`pytest`, Python 3.11–3.13) |
| `--healthcheck`, `crm_salud`, alert scripts | Unit tests and `scripts/smoke.sh` against the fake PostgREST. The alert examples have not run in production |
| Synthetic seed and offline demo | `scripts/demo_offline.py`; transcript in [media/demo-offline.txt](media/demo-offline.txt) |
