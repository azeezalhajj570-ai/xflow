# Spec: x_account — Native Odoo X Account & Session Platform (XAction Replacement)

## Objective

Replace the external XAction runtime with a native Odoo subsystem (`x_account`) that owns
X accounts, authentication/session persistence and restoration/validation, account
lifecycle, provider integration, tasks, DM/group-DM, account grouping, automation, and
migration from XAction. Odoo becomes the single system; XAction becomes disposable after a
proven, non-destructive, staged cutover.

**Who:** Businesses managing multiple X session-cookie accounts (target 50+) currently via
XAction.

**Success looks like:** migrate one real XAction account → Odoo restores/validates the same
session → required read op + permitted harmless write op succeed → Odoo restarts → session
restored and ops repeat (no XAction). Then scale 10 → 25 → 50.

**Authoritative spec:** `docs/x_account/00-specification.md` (+ `01-requirements.md`,
`02-acceptance.md`, `tasks/plan-x_account.md`, `tasks/todo-x_account.md`).

## Tech Stack

- Odoo 19 Enterprise, container `odooo-odoo`.
- depends: `['social','social_twitter','contacts','base_automation','mail']`.
- Provider: native Python HTTP client (no Puppeteer) ported from XAction's cookie-session
  client (`SessionWebProvider`).
- Queue: `x.account.task` + `ir.cron` (MVP); production path to approved Odoo queue.

## Key Architectural Decisions

1. **Reuse-first:** extend `social.account`, reuse `social_media_twitter`, `res.partner`,
   `discuss.channel`/`discuss.channel.member`, `mail.message`/`mail.activity`,
   `base_automation`, `res.config.settings`, `ir.config_parameter`, `ir.cron`.
2. **Exactly four new models:** `x.session.store`, `x.account.group`, `x.account.task`,
   `x.message` (justifications in spec §6). **No** `x.account.event` (use mail/message).
3. **Auth ≠ provider:** `x_auth_method` (session_cookie/oauth1) independent of `x_provider`
   (session_web/official_publish). No `cookie=provider` coupling.
4. **All X HTTP behind `XService → XProvider`** — models never call X directly.
5. **Session persistence is first-class:** durable encrypted `x.session.store` (key
   separation + ACLs + masking), restored per worker on restart; runtime registry is never
   the source of truth.
6. **Lifecycle with error classification:** network/rate-limit/temporary ≠ session
   expiration; those keep the account ACTIVE.
7. **Migration non-destructive + staged** (shadow → restore → primary → remove) with audit
   and rollback; portability test (real read + write + restart) before scaling.
8. **OmniX = OPTIONAL provider** — third-party X REST API (Bearer key + account's
   `auth_token`) covering DM/tweet/like/retweet/follow; per-account **either/or** with
   `SessionWebProvider`; no hard dependency.

## Project Structure

```
addons/custom/x_account/
├── __manifest__.py, __init__.py
├── models/  social_account, social_media, discuss_channel, x_message, res_partner,
│            session_store, account_group, account_task, res_config_settings
├── services/  x_service, session_manager, task_service, x_provider, providers/{session_web, official_publish, omnix}
├── controllers/ main.py              # /x_account/import_session, callback, webhook
├── wizards/ import_session.py
├── data/ cron.xml
├── security/ ir.model.access.csv     # incl. group_x_account_manager
├── views/                    # social_account, account_group, account_task, res_config_settings
├── migrations/ 19.0.1.0.1/   # non-destructive XAction migration
└── tests/     test_session, test_account_lifecycle, test_task_queue, test_portability,
               test_security, test_migration
```

## Testing Strategy

- Repo conventions: `@tagged('post_install','-at_install')`, `MailCommon`, `@patch`, fresh DB.
- Verify: `./scripts/run-tests.sh x_account`
- Coverage: encryption/persistence/restore/validation/classification, lifecycle, isolation,
  task claim/concurrency/retry/backoff, DM/group-DM/identity mapping, migration + rollback,
  restart recovery, 10/25/50 scaling, security (ordinary user cannot read raw credentials).

## Boundaries

**Always:** reuse existing models; isolate all X HTTP; encrypt with key separation + ACL +
masking; non-destructive staged migration; run portability test before scaling.

**Ask first:** a 5th model; new dependencies beyond the listed set; changing
provider interface set or `social.account` base semantics.

**Never:** tokens/secrets in logs/chatter/display/API; X HTTP from a model; cross-account
sessions; delete XAction source during migration; skip portability test; treat
network/rate-limit as session expiration; create duplicate X account/media/contact/channel/
event/config models; many Chromium processes.
