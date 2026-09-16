# Demo

Every demo uses the synthetic seed only. Never demo against a real CRM, a real Telegram chat or a `service_role` key.

## 1. Offline, scripted (no database, no network, no LLM)

```bash
python3 scripts/demo_offline.py
```

The script starts the real server over stdio, backed by the fake PostgREST, and replays these calls:
- `crm_hoy`;
- a card that contains a prompt-injection string inside `<untrusted>`;
- a note refused under `off`;
- a diff under `dry_run`;
- a note and a status change under `on`;
- an ambiguous name that is refused;
- the audit trail with `rol=crm_agent actor=hermes-demo`;
- `crm_salud`.

A captured run is in [media/demo-offline.txt](media/demo-offline.txt). The tool calls are scripted, so this shows the server's behaviour, not a model's. The database-level guarantees are **not** shown here, because the fake backend only imitates them.

## 2. With a real client and a local database (about 5 minutes)

1. Start a local database with the seed ([running-with-postgres.md](running-with-postgres.md)), then run `bash scripts/smoke.sh` and `python3 servers/crm/server.py --healthcheck`.
2. Copy `examples/claude-code/.mcp.example.json` to `.mcp.json` in this directory, export the `CRM_*` variables, and open Claude Code here. Then ask:
   1. "What is due today?" (`crm_hoy`)
   2. "Who should I call next?" (`crm_siguiente_llamada`)
   3. "Add a note to Asesoría Demo Norte: prefers afternoon calls". With `CRM_WRITE_MODE=off`, the write is refused.
   4. Repeat with `dry_run`. The diff is returned.
   5. Repeat with `on`. The note is written, and its author is set by the database.
   6. "Mark it as contactado" (`crm_actualizar_estado`)
   7. `crm_cambios`, or in SQL: `select rol, actor, operacion, campo from prospecto_cambios order by id desc`
3. In a terminal, run the manual `UPDATE ... set score = 1` as `crm_agent` from [running-with-postgres.md](running-with-postgres.md). Postgres answers with a permission error.

## 3. The same server under Hermes Agent

Merge `examples/hermes/config.example.yaml` into a Hermes profile that points at the demo database, and ask the same questions from the CLI.

Record this demo instead of running it live: in real use, one cron run with a low-cost model hit a 766-second inactivity timeout. Use a model that follows the tool-calling protocol reliably, and say which one you used.

For the cron example, use a bot and a chat created only for the demo.

## Recording a video (not done yet)

No video or screenshots have been recorded for this repository so far. To produce a 60–90 s clip of flows 1 and 2:

- terminal only: `asciinema rec demo.cast -c "python3 scripts/demo_offline.py"`, or a [vhs](https://github.com/charmbracelet/vhs) tape that runs the same command;
- with Claude Code: a screen recording of steps 2.1–2.7 against the local seed.

Before publishing a recording, check that it shows no real paths, hostnames, chat IDs or keys.
