# Spec: AI WhatsApp Context

## Objective

When an AI agent processes WhatsApp messages, it should receive clean plain-text
chat history (no HTML) and know who the customer is (name, phone). This makes
the AI usable on WhatsApp where HTML rendering doesn't exist and the agent needs
customer identity to give contextual support.

**Success criteria:**
- `_retrieve_chat_history` returns plain text (no `<b>`, `<i>`, `<p>`) for WhatsApp channels
- `_build_extra_system_context` includes customer name + phone for WhatsApp channels
- Non-WhatsApp channels are unaffected (HTML preserved, no customer info injected)
- Empty channels return `[]` history
- All 6 new tests pass on a fresh DB
- The `_check_notify_user_ids` constraint does not block test setup

## Assumptions (validated)

1. Odoo 19.0 Enterprise (`kerbi/odoo19e-202604` Docker image)
2. Dev environment: Docker (`odoo19-dev-odoo` container), PostgreSQL 17
3. Fresh test DB required each run (existing `odoo` DB cannot be reused by a second server)
4. DB password `odoo18@2024!` — shell escape `\!` or use config file
5. Constraint blocker is in scope — tests can't run without fixing it
6. Deliverable: working code + passing tests, committed

## Tech Stack

- **Odoo 19.0** Enterprise Edition
- **PostgreSQL 17** via `pgvector/pgvector:pg17`
- **Python 3.x** (Odoo 19 default)
- **Docker Compose** dev environment
- **unittest** with Odoo's `MailCommon` test base

## Commands

```bash
# Full test suite (fresh DB)
docker exec odoo19-dev-odoo bash -c "psql -U odoo -d postgres -c 'DROP DATABASE IF EXISTS test_ai_whatsapp;' && psql -U odoo -d postgres -c 'CREATE DATABASE test_ai_whatsapp OWNER odoo;' && odoo -d test_ai_whatsapp -i ai_whatsapp --test-tags /ai_whatsapp --stop-after-init --db_password 'odoo18@2024!'"

# Production DB update (no tests)
make update m=ai_whatsapp

# Update all custom modules
make update m=all

# Start dev server (hot reload)
make dev

# Open shell
make shell
```

## Project Structure

```
addons/custom/
├── ai_whatsapp/                  ← Feature module
│   ├── __manifest__.py
│   ├── models/
│   │   ├── ai_agent.py          ← _retrieve_chat_history, _build_extra_system_context overrides
│   │   ├── discuss_channel.py    ← AI routing, chatbot, human takeover logic
│   │   └── whatsapp_account.py   ← routing_mode, ai_agent_id fields
│   └── tests/
│       └── test_ai_whatsapp.py   ← 34 test methods
├── whatsapp_evaluation/          ← Base WhatsApp integration (dependency)
│   └── models/
│       └── whatsapp_account.py   ← notify_user_ids constraint (blocker location)
```

## Code Style

```python
class AIAgent(models.Model):
    _inherit = 'ai.agent'

    def _retrieve_chat_history(self, discuss_channel, no_messages=20):
        if discuss_channel.channel_type != 'whatsapp':
            return super()._retrieve_chat_history(discuss_channel, no_messages)
        from odoo.tools import html2plaintext
        chat_history = [
            {
                'content': html2plaintext(message.body) if message.body else '',
                'role': 'assistant' if message.sudo().author_id.agent_ids else 'user',
            }
            for message in discuss_channel.message_ids[1 : no_messages + 1]
        ]
        chat_history.reverse()
        return chat_history

    def _build_extra_system_context(self, discuss_channel):
        extra = super()._build_extra_system_context(discuss_channel)
        if discuss_channel.channel_type != 'whatsapp':
            return extra
        wa_context = []
        if discuss_channel.whatsapp_partner_id:
            partner = discuss_channel.whatsapp_partner_id
            wa_context.append(f"Customer name: {partner.name}")
            wa_context.append(f"Customer phone: {partner.phone or discuss_channel.whatsapp_number or ''}")
        if discuss_channel.wa_account_id:
            wa_context.append(f"WhatsApp account: {discuss_channel.wa_account_id.name}")
        if wa_context:
            extra += "\n\n" + "\n".join(wa_context) if extra else "\n".join(wa_context)
        return extra
```

**Key conventions:**
- Override `channel_type != 'whatsapp'` guard at top of method, fallthrough to `super()`
- `from odoo.tools import html2plaintext` inside method (lazy import, avoid circular deps)
- List comprehensions for chat history, `.reverse()` to get chronological order

## Testing Strategy

**Framework:** `unittest` via Odoo's `MailCommon` base class, tagged `post_install -at_install`.

| Test | Coverage |
|------|----------|
| 29 | WhatsApp history is plaintext (no HTML) |
| 30 | HTML stripped from formatted messages |
| 31 | Non-WhatsApp channels preserve HTML |
| 32 | Empty WhatsApp channel returns `[]` |
| 33 | Extra context includes customer name/phone |
| 34 | Non-WhatsApp channel gets empty extra context |

## Boundaries

- **Always:** Run tests on fresh DB before committing; lazy-import `html2plaintext`; guard on `channel_type`
- **Ask first:** Changing the constraint logic; adding new dependencies; modifying test infrastructure
- **Never:** Store `notify_user_ids` inline in create vals to bypass constraint; commit with failing tests

## Open Questions

1. **Constraint blocker (P0):** `_check_notify_user_ids` fires with empty cache during `create`/`write`. Why does `field.create()` → `write_real` → `_update_cache` not persist? Needs deeper `_update_cache` tracing or `invalidate_recordset` workaround.
2. Should `_retrieve_chat_history` handle `message.body is None` explicitly (currently `html2plaintext(None)` behavior)?

## Files Touched

| File | Change |
|------|--------|
| `addons/custom/ai_whatsapp/models/ai_agent.py` | `_retrieve_chat_history` + `_build_extra_system_context` |
| `addons/custom/ai_whatsapp/tests/test_ai_whatsapp.py` | tests 29–34 |
| `addons/custom/whatsapp_evaluation/models/whatsapp_account.py` | Constraint fix (TBD) |
