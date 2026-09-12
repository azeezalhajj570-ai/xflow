# AGENTS.md — Odoo 19 AI WhatsApp Project

## Project Context

Custom Odoo 19 module `ai_whatsapp` that routes inbound WhatsApp messages
through AI agents, chatbots, or human operators. Built atop `whatsapp_evaluation`
(Evolution API integration).

## Dev Environment

- **Docker containers:**
  - `odooo-odoo` — Odoo 19 Enterprise (compose service `odoo`)
  - PostgreSQL is **not** part of this compose project; it runs in the external
    madarbot stack (container `madarbot-postgres-1`)
- **DB connection:** configured in both `.env` and `config/odoo.conf`
  - host `madarbot-postgres-1`, port `5432`
  - user `odoo`, password `odoo` (note: NOT `odoo18@2024!`)
  - **Running dev DB name: `odoo_2026-08-11_22-38-33`** (set via `db_name` in
    config and `ODOO_DATABASE` in `.env`)
- **Addons:** code lives on host under `addons/custom/`, mapped to `/mnt/custom-addons/` in container
- **Config:** `config/odoo.conf` is mounted at `/etc/odoo/odoo.conf`
  - `dbfilter = ^odoo_2026-08-11_22-38-33$` — the dev server is pinned to the
    running DB name above (not `.*`)
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

# Create a fresh DB on the external postgres host (madarbot-postgres-1)
docker compose exec -T odoo sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS test_ai_whatsapp;"' sh test_ai_whatsapp
docker compose exec -T odoo sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE test_ai_whatsapp OWNER odoo;"' sh test_ai_whatsapp

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

`docker exec odoo19-dev-odoo psql ...` was wrong because PostgreSQL runs in the
external madarbot stack (`madarbot-postgres-1`), not in an Odoo compose service.
`--dbfilter` is not a valid option (use
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

The `dbfilter` in `config/odoo.conf` is pinned to the running dev DB
(`^odoo_2026-08-11_22-38-33$`). Tests pass the DB explicitly with `-d`, so
compose-provided test runs are unaffected, but the GUI database list hides test
DBs by design.

### `psql: connection to server on socket ... failed`

`psql` was run inside the Odoo container. PostgreSQL lives on the external host
`madarbot-postgres-1`. Use `docker compose exec -T odoo sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres ...'` from the repo root, or `docker exec madarbot-postgres-1 psql ...`.

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

## UI Conventions

- **Follow Members composer** (`x.follow.composer`, opened from the X group chat
  form via the "Follow Members" button): members are selected on a
  `Many2many('res.partner')` field (`member_ids`) rendered with
  `widget="many2many_tags"`. The tag field's domain is restricted to the
  conversation's group members by referencing a computed `member_pool_ids`
  field (`[('id', 'in', member_pool_ids)]`). Do NOT regress this to a
  One2many/checkbox line list.
- Do NOT use traversal domains like `[('id', 'in', channel_id.x_group_member_ids)]`
  in form views — the web client cannot resolve `m2o.<related m2m>` and throws
  `InvalidDomainError: Invalid domain representation`. Resolve the pool on the
  record itself (computed field) and reference it directly.
- In general, prefer `Many2many` + `widget="many2many_tags"` over One2many line
  lists for member/tag selection in wizards and forms.

## Spec Location

`docs/ai_whatsapp_context.SPEC.md` — full spec with objective, commands,
structure, code style, testing, boundaries, and open questions.

## Git Workflow

**No direct commits to `main` branch.** All changes must go through feature branches and pull requests.

### Branch Naming

- `feat/<description>` — New features
- `fix/<description>` — Bug fixes
- `refactor/<description>` — Code refactoring
- `docs/<description>` — Documentation updates
- `test/<description>` — Test additions

Examples:
- `feat/subscription-events-config`
- `fix/webhook-decryption`
- `refactor/task-queue`

### Workflow

1. **Create feature branch from main:**
   ```bash
   git checkout main
   git pull origin main
   git checkout -b feat/your-feature-name
   ```

2. **Make changes and commit:**
   ```bash
   git add .
   git commit -m "feat: add subscription event configuration"
   ```

3. **Push and create PR:**
   ```bash
   git push -u origin feat/your-feature-name
   gh pr create --title "feat: add subscription event configuration" --body "Description of changes"
   ```

4. **After PR approval and merge:**
   ```bash
   git checkout main
   git pull origin main
   git branch -d feat/your-feature-name
   ```

### Commit Message Format

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <description>

[optional body]

[optional footer(s)]
```

Types:
- `feat:` — New feature
- `fix:` — Bug fix
- `refactor:` — Code refactoring
- `docs:` — Documentation
- `test:` — Tests
- `chore:` — Maintenance tasks

Examples:
```
feat: add configurable subscription events
fix: resolve webhook decryption error
refactor: simplify task queue processing
```

### Protected Branch

The `main` branch is protected via pre-commit and pre-push hooks. Direct commits and pushes are blocked. All changes must go through pull requests.
