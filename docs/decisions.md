# Design decisions

Each decision gives the context, the choice, and what it costs. The prototype referred to throughout is `prototype/crm_server_v0.py`.

## 1. Python standard library instead of the official MCP SDK

**Context.** The server only needs stdio transport, `initialize`, `tools/list`, `tools/call` and `ping`. Both agent runtimes it ran under already had their own MCP client.

**Choice.** The server handles newline-delimited JSON-RPC 2.0 itself, with `urllib` and `json`.

**Why.**
- There is nothing to install next to the runtime: `python3 server.py` is the whole deployment.
- The protocol is visible in about 60 lines (`CrmServer.handle`).
- A failure cannot be blamed on an SDK version mismatch between server and client. That mattered in the incident: see [postmortem-mcp-parked.md](postmortem-mcp-parked.md).

**Cost.**
- No HTTP transport, resources, prompts or progress notifications.
- Protocol changes have to be tracked by hand; `SUPPORTED_PROTOCOL_VERSIONS` lists the versions it answers with.

**When to switch.** Use the SDK or FastMCP when remote use (streamable HTTP with auth), resources or sampling are needed.

## 2. The allowlist lives in Python **and** in the database

**Context.** The prototype enforced `CAMPOS_ESCRIBIBLES` only in Python, and it authenticated with the Supabase `service_role` key, which bypasses row-level security. A bug or a new code path in the server could therefore write any column.

**Choice.** Two independent layers:
- The server refuses fields outside `CAMPOS_ESCRIBIBLES` before making any request.
- The database repeats the same list as column-level `GRANT UPDATE` for the `crm_agent` role.

`tests/test_allowlist.py` fails if the Python list, the fake backend's list and the SQL grant ever differ.

**Cost.** A new writable field needs a migration as well as a code change. That friction is intended.

## 3. A restricted agent identity replaces `service_role`

**Why the prototype used `service_role`.** It was the fastest way to reach a CRM whose tables had RLS only for the human web app's `authenticated` role. It was a single-user system on a local machine. It worked, and it was the wrong default: the agent runtime had a local terminal backend and its tool-loop hard stop was disabled.

**Choice.** The Postgres role `crm_agent` (NOLOGIN, reached through PostgREST with a JWT whose `role` claim is `crm_agent`) has:
- `SELECT` on the read columns, but not on `valor_cents`;
- `UPDATE` on the six allowlisted columns only;
- `INSERT (prospecto_id, cuerpo)` on notes;
- `SELECT` on the audit table, except the rows that record changes to `valor_cents`. The trigger copies old and new values of every column, so without this RLS filter the agent could read through the audit what the column grant hides;
- no `DELETE` anywhere, and no write access to the audit table.

The note author is set by a trigger (`agente:<actor>`), so the agent cannot claim a note was written by a human. `scripts/mint_agent_jwt.py` mints HS256 tokens for local or self-hosted PostgREST.

**Cost and limits.**
- Hosted Supabase projects that use asymmetric JWT signing keys need a different token flow. That flow is **not tested** here.
- The migration removes `anon`/`authenticated` privileges on these tables and enables RLS with policies only for `crm_agent`. A human app that shares the tables must add its own grants and policies first; the SQL file says so in its header.

## 4. Resolve by name, and require a unique match

**Context.** Agents refer to prospects by name, and names overlap ("Gestoría Ejemplo Centro" and "Gestoría Ejemplo Centro Sur").

**Choice.** `resolve()` searches with an escaped `ilike` and then:
- with 0 matches, returns an error;
- with more than one match, returns an error that lists the candidates;
- with several matches but exactly one identical name (ignoring case and spacing), uses that name, because an exact name is not a guess.

The server never picks "the most likely" match.

**Cost.** The agent sometimes needs a second turn. In the prototype's real use, 2 of 3 note attempts failed this way or on errors, and none wrote to the wrong record.

## 5. No deletes

No tool deletes anything, and the agent role has no `DELETE` privilege. Closing a prospect is a status (`cerrado` plus `resultado`), and the audit table has no foreign key, so it survives even a manual delete.

## 6. A database trigger instead of an application log for the audit

**Choice.** `registrar_cambio_prospecto` records one row per changed field for every INSERT, UPDATE or DELETE on `prospectos`, and `registrar_nota` records every note. Each row stores:
- the effective role (`crm_rol_efectivo()`, which stays correct inside `SECURITY DEFINER`);
- the session user;
- the JWT `actor` claim.

**Why.**
- The trigger records changes from **any** source: the agent, the web app, scripts or SQL.
- The audit claim "the agent never changed a status" only holds because the log does not depend on the agent's cooperation. In real use this proved 0 status changes by the agent: see [evidence.md](evidence.md).

**Cost.**
- Audit rows are stored as text, so types are lost.
- There is no retention policy yet.

## 7. Local stdio instead of remote HTTP

stdio keeps credentials in the operator's process environment, and there is no listening socket to secure. A remote transport would need authentication, rate limiting and TLS. It is listed as a possible extension, not as a gap in this design.

## 8. `CRM_WRITE_MODE` defaults to `off`, with a write budget

**Choice.**
- `off` refuses every write and says so.
- `dry_run` resolves the prospect, reads the current values and returns the exact diff and PATCH body without sending it.
- `on` writes, up to `CRM_MAX_WRITES` per server process (20 by default).
- A write that changes nothing is reported as "no change needed" and does not spend budget.

**Why.**
- An operator can connect a new agent or model safely with `off`, watch it propose changes with `dry_run`, and only then enable `on`.
- The budget limits the damage a looping agent can do.

**Cost.** The budget is per process: a runtime that restarts servers often gets a fresh budget each time.

## 9. Third-party text is delimited, not trusted

Website findings, proposals and notes are wrapped in `<untrusted source="...">` blocks. Nested tags are escaped, and every field is flattened to a single line with a length cap. Tool descriptions and the `initialize` instructions tell the model to treat those blocks as data.

This **reduces** prompt-injection risk but does not remove it. The seed contains an injection attempt on purpose, and the README explains the remaining defences.

## 10. Configuration only from the environment

The prototype read credentials from other projects' dotfiles. The server now reads `CRM_*` variables from its process environment, or from a file named explicitly with `--env-file`, which accepts only `CRM_*` keys and never overrides the environment. It never searches for configuration files on its own. Logs and errors pass through `redact()`, which removes the configured secrets, anything shaped like a JWT, and `Bearer` values.

## 11. A functional healthcheck instead of a liveness check

This decision came out of the incident. `--healthcheck` and `crm_salud` run real reads through the same code path the tools use. A fresh-process check cannot see a stuck session inside a long-lived runtime, so the repository also ships an in-band canary example and a runtime-log watcher. See [postmortem-mcp-parked.md](postmortem-mcp-parked.md).

## 12. Spanish domain identifiers

The CRM and its users are Spanish, so table, column and tool names stay in Spanish to match the schema of the original app. Code comments, messages and documentation are in English. The README has a glossary.
