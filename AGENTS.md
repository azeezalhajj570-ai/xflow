# AGENTS.md — Odoo 19 AI WhatsApp Project

## Project Context

Custom Odoo 19 module `ai_whatsapp` that routes inbound WhatsApp messages
through AI agents, chatbots, or human operators. Built atop `whatsapp_evaluation`
(Evolution API integration).

## Dev Environment

- **Docker containers:**
  - `odoo19-dev-odoo` — Odoo 19 Enterprise (`kerbi/odoo19e-202604:latest`)
  - `odoo19-dev-db` — PostgreSQL 17 with pgvector (`pgvector/pgvector:pg17`)
- **DB connection:** configured in both `.env` and `config/odoo.conf`
  - host `db` (compose service name), port `5432`
  - user `odoo`, password `odoo18@2024!`
- **Addons:** code lives on host under `addons/custom/`, mapped to `/mnt/custom-addons/` in container
- **Config:** `config/odoo.conf` is mounted at `/etc/odoo/odoo.conf`
  - `dbfilter = .*` so the dev server can see any DB (including test DBs)
  - explicit `db_host/db_port/db_user/db_password` so `docker exec` commands work
    without relying on the entrypoint to inject credentials
- **Container memory:** defined in `.env` via `ODOO_MEMORY_LIMIT` and `DB_MEMORY_LIMIT`
  - The DB needs **≥1 GB**; module installation/tests will OOM-kill postgres at 256 MB
  - Odoo needs **≥3 GB** for module install + tests

## Quick Start

```bash
# Start the stack
docker compose up -d

# Or use the helper
docker compose up -d --remove-orphans
```

After changing `config/odoo.conf` or `.env`, restart:

```bash
docker compose restart
```

## Running Tests

Always use the helper script. It creates a fresh test DB, runs Odoo in
single-process mode with HTTP disabled, and saves the log.

```bash
./scripts/run-tests.sh ai_whatsapp
```

### Manual equivalent

Only use this if the helper script is unavailable. Note that Odoo 19 uses
sub-commands, so server options must follow `odoo server`.

```bash
TEST_DB=test_ai_whatsapp

# Create a fresh DB in the postgres container
docker compose exec db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS ${TEST_DB};"
docker compose exec db psql -U odoo -d postgres -c "CREATE DATABASE ${TEST_DB} OWNER odoo;"

# Run tests
docker compose exec -T odoo /entrypoint.sh odoo server \
  -d "${TEST_DB}" \
  -i ai_whatsapp \
  --test-tags /ai_whatsapp \
  --stop-after-init \
  --workers=0 \
  --http-port=18069 \
  --logfile=/var/log/odoo/test_ai_whatsapp.log
```

### Why the old command was broken

`docker exec odoo19-dev-odoo psql ...` was wrong because PostgreSQL runs in a
separate container (`odoo19-dev-db`). `--dbfilter` is not a valid option (use
`--db-filter` on the command line, or set it in `odoo.conf`). And `odoo -d ...`
bypassed the `server` subcommand, so the server tried to bind to the default
HTTP port and crashed.

## Reading Test Logs

The helper script writes logs to `logs/test_<module>.log`. You can also tail the
log live inside the container:

```bash
docker compose exec odoo tail -f /var/log/odoo/test_ai_whatsapp.log
```

## Troubleshooting

### `server closed the connection unexpectedly` / `database system is in recovery mode`

The postgres worker was OOM-killed. Increase the DB container memory in `.env`
(`DB_MEMORY_LIMIT`) and recreate the container:

```bash
docker compose up -d --force-recreate db
```

### `OSError: [Errno 98] Address already in use`

Tests tried to bind to port 8069 which the dev server already uses. Use the
helper script, or add `--http-port=18069 --workers=0` to the manual command.

### `FATAL: database "test_ai_whatsapp" does not exist`

The dev server was started with `dbfilter = ^odoo$`, which hides test DBs. The
config now uses `dbfilter = .*`; restart the Odoo container if you changed it.

### `psql: connection to server on socket ... failed`

`psql` was run inside the Odoo container. PostgreSQL lives in the `db`
container. Use `docker compose exec db psql ...`.

## Project Files

| File | Purpose |
|------|---------|
| `ai_whatsapp/models/ai_agent.py` | AI context overrides (chat history, system context) |
| `ai_whatsapp/models/discuss_channel.py` | Webhook routing, chatbot, human takeover |
| `ai_whatsapp/tests/test_ai_whatsapp.py` | 34 tests |
| `whatsapp_evaluation/models/whatsapp_account.py` | WhatsApp account model and `notify_user_ids` constraint |
| `whatsapp_evaluation/models/discuss_channel.py` | WhatsApp channel model and message-post logic |
| `whatsapp_evaluation/models/whatsapp_message.py` | Outbound/inbound WhatsApp message records |
| `config/odoo.conf` | Mounted Odoo configuration |
| `docker-compose.yml` | Compose stack definition |
| `scripts/run-tests.sh` | Fresh-DB test runner |

## Testing Conventions

- Classes tagged `@tagged('post_install', '-at_install')`
- Extend `MailCommon` (from `odoo.addons.mail.tests.common`)
- Use `@classmethod def setUpClass` for fixtures
- Use `@patch`/`@patch.object` for mocking API calls
- Fresh DB required because Odoo caches model metadata across tests in same DB
- WhatsApp outbound sends (`WhatsAppMessage._send_message`) should be mocked in
  tests to avoid real network calls and an Odoo 19 test-framework incompatibility
  with `requests` tuple timeouts

## Spec Location

`docs/ai_whatsapp_context.SPEC.md` — full spec with objective, commands,
structure, code style, testing, boundaries, and open questions.
