# Implementation Plan: x_account

## Overview

Build the native Odoo X Account & Session Platform in dependency order. Each phase is
independently verifiable. Do NOT implement until this SpecKit is approved.

**depends:** `['social', 'social_twitter', 'contacts', 'base_automation', 'mail']`
**module path:** `addons/custom/x_account/`

## Phase Dependency Graph

```
PHASE 1: social.account + session persistence
  └── No dependencies
  ↓
PHASE 2: Python session provider (XService/XProvider/SessionWebProvider)
  └── Depends on Phase 1
  ↓
PHASE 3: Restart/restore/validation (startup recovery + cron + portability test)
  └── Depends on Phase 2
  ↓
PHASE 4: XAction migration (shadow→restore→primary; audit + rollback)
  └── Depends on Phase 3
  ↓
PHASE 5: Task execution (x.account.task + XTaskService + cron worker + locking)
  └── Depends on Phase 3 (uses provider)
  ↓
PHASE 6: DM / Group DM (discuss.channel + x.message + res.partner + lifecycle events)
  └── Depends on Phase 3
  ↓
PHASE 7: Groups / automation (x.account.group + base_automation)
  └── Depends on Phase 5
  ↓
PHASE 8: OmniX REST provider (optional) + Official publish adapter (optional)
  └── Depends on Phase 2
```

## Project Structure

```
addons/custom/x_account/
├── __manifest__.py
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── social_account.py        # _inherit social.account → status, session relation, audit
│   ├── social_media.py          # _inherit social.media → _action_add_account auth branch
│   ├── discuss_channel.py       # _inherit discuss.channel → channel_type x / x_group
│   ├── x_message.py             # x.message mirror (NEW)
│   ├── res_partner.py           # _inherit res.partner → X contact fields
│   ├── session_store.py         # x.session.store (NEW)
│   ├── account_group.py         # x.account.group (NEW)
│   ├── account_task.py          # x.account.task (NEW)
│   └── res_config_settings.py   # _inherit res.config.settings
├── services/
│   ├── __init__.py
│   ├── x_service.py             # XService façade (models→provider; no X HTTP in models)
│   ├── session_manager.py       # XSessionManager
│   ├── task_service.py          # XTaskService (enqueue/claim/retry/lock)
│   ├── x_provider.py            # XProvider interface + dispatch
│   └── providers/
│       ├── __init__.py
│       ├── session_web.py       # SessionWebProvider (isolated, ported client)
│       ├── official_publish.py  # XOfficialPublishAdapter (optional)
│       └── omnix.py             # OmniXProvider (optional REST provider)
├── controllers/
│   ├── __init__.py
│   └── main.py                  # /x_account/import_session, callback, webhook
├── wizards/
│   ├── __init__.py
│   └── import_session.py        # paste session cookie wizard
├── data/
│   └── cron.xml                 # validate-sessions, process-task-queue crons
├── security/
│   └── ir.model.access.csv      # ACLs incl. group_x_account_manager
├── views/
│   ├── social_account_views.xml
│   ├── account_group_views.xml
│   ├── account_task_views.xml
│   └── res_config_settings_views.xml
├── migrations/
│   └── 19.0.1.0.1/              # XAction migration scripts (non-destructive)
└── tests/
    ├── __init__.py
    ├── test_session.py
    ├── test_account_lifecycle.py
    ├── test_task_queue.py
    ├── test_portability.py
    ├── test_security.py
    └── test_migration.py
```

## New Models

- `x.session.store` — `account_id`, `encrypted_blob`, `alg`, `created_at`, `last_access_at`.
- `x.account.group` — `name`, `description`, `account_ids` (M2M), `actions`, `auto_execute`,
  `cooldown_sec`, `paused`.
- `x.account.task` — `account_id`, `group_id`, `operation`, `status`, `priority`,
  `retry_count`, `max_attempts`, `claimed_at`, `next_retry_at`, `error`, `result`.
- `x.message` — `channel_id`, `account_id`, `direction`, `external_id`, `body_plain`,
  `external_created_at`, `author_partner_id`, `author_x_id`, `author_x_username`, `acked`,
  `delivered`, `participant_joined`, `participant_left`, `mail_message_id`.

## Field Additions to Existing Models

- `social.account`: `x_connection_status`, `last_connected`, `last_validated`, `last_error`,
  `x_provider`, `x_auth_method`, `x_session_store_id` (M2O), `x_migration_status`,
  `source_account_id`, `source_user_id`, `migration_batch_id`, `migration_timestamp`.
  **No** inverse `group_ids`/`task_ids` unless a view proves necessary.
- `social.media`: auth-branch in `_action_add_account()`; reuse `social_media_twitter`.
- `discuss.channel`: `channel_type` add `x`/`x_group`; `x_account_id`, `x_partner_id`,
  `x_conversation_id` (unique), `last_x_mail_message_id`.
- `res.partner`: `x_user_id`, `x_username`, `x_following`, `x_blocked` (only if required).

## Phase Details

### PHASE 1 — social.account + session persistence
- Module scaffold (`__manifest__`, `__init__`).
- `models/social_account.py`: status/relation/audit fields.
- `models/session_store.py`: `x.session.store`.
- `services/session_manager.py`: `XSessionManager` (create/save/load/restore/validate/
  invalidate/delete/disconnect/reconnect) with encryption helpers (`AES-256-GCM`,
  `X_SESSION_ENCRYPTION_KEY`).
- `security/ir.model.access.csv` (+ `group_x_account_manager`).
- `views/social_account_views.xml`, `res_config_settings_views.xml` (key/auth toggles).
- Verify: seed a session, encrypt/decrypt roundtrip, ACL test.

### PHASE 2 — Python session provider
- `services/x_provider.py`: `XProvider` interface (`validate_session`, `get_conversations`,
  `get_events`, `get_dms`, `send_dm`).
- `services/x_service.py`: `XService` façade + `XTaskService` stub.
- `services/providers/session_web.py`: `SessionWebProvider` (port TwitterAuth/HttpClient:
  cookie authenticate, guest token, ct0 CSRF, `verify_credentials`, DM ops).
- Verify: unit tests with mocked HTTP (`@patch`).

### PHASE 3 — Restart/restore/validation
- Startup recovery (restore sessions from `x.session.store` per worker).
- `data/cron.xml`: `_validate_sessions_cron()` per-account scheduled sweep.
- `tests/test_portability.py`: single-account portability acceptance test.
- Verify: simulated restart restores + validates; classification correct.

### PHASE 4 — XAction migration
- `migrations/19.0.1.0.1/`: discover→convert→encrypt→map→validate→flag; audit fields;
  rollback procedure; non-destructive.
- Manual/operational cutover (Stage 1 shadow → 2 restore → 3 primary → 4 remove).
- Verify: migration maps `Account`/`Group`/`GroupTask`/`XGroupMember`; rollback works;
  portability test against one real account.

### PHASE 5 — Task execution
- `models/account_task.py`: `x.account.task` + state machine.
- `services/task_service.py`: `XTaskService` (enqueue/claim/retry/backoff/per-account lock).
- `data/cron.xml`: `_process_task_queue()` skip-locked worker.
- Verify: claim/concurrency/retry/backoff tests; scale 10/25/50.

### PHASE 6 — DM / Group DM
- `models/discuss_channel.py`: channel types + identity fields + message routing
  (`message_post`/`_notify_thread`).
- `models/x_message.py`: `x.message` mirror.
- `models/res_partner.py`: X contact fields.
- `mail.message`/`mail.activity` lifecycle events on account.
- Verify: DM mapping, group-DM mapping, external identity mapping tests.

### PHASE 7 — Groups / automation
- `models/account_group.py`: `x.account.group` + M2M accounts.
- `base_automation` rules → enqueue `x.account.task`.
- Verify: group automation enqueues tasks (no direct X HTTP from rules).

### PHASE 8 — Official/OmniX (optional providers)
- `services/providers/omnix.py` — `OmniXProvider`: `validate_session` (GET `user/info`),
  `get_conversations` (GET DM inbox), `get_dms`, `send_dm` (POST), and
  `like`/`comment`/`repost`/`follow`/`post_tweet` mapped to OmniX endpoints
  (`https://api.omnixapi.com/api/v1/twitter`); reads `auth_token` from
  `XSessionManager.load(account)`; `Authorization: Bearer` from
  `ir.config_parameter 'x_account.omnix_api_key'`; `_needs_cookies = True`;
  classify `402` → transient (stays ACTIVE), `401` → ERROR.
- `models/social_account.py`: `x_provider` selection adds `('omnix', 'OmniX REST API')`.
- `models/res_config_settings.py`: add `x_omnix_api_key` config field (+ view).
- `services/providers/official_publish.py`: `XOfficialPublishAdapter` (optional, publish
  only; separate from auth and session) — unchanged.
- Verify: mocked tests (`tests/test_omnix.py`); `./scripts/run-tests.sh x_account`.

## Dependencies & Verify

```
./scripts/run-tests.sh x_account
```

## Risks Summary

See `00-specification.md` §18 (Risk Register) — session portability, undocumented X API,
Odoo worker lifecycle, multi-account concurrency, credential security, DM identity mapping,
migration failure, 50+ scalability, provider changes, **OmniX third-party dependency**
(pricing/API stability — mitigated by the `XProvider` interface, mocked tests, and
`session_web` remaining the no-dependency fallback).

## Boundaries

- Do NOT add a 5th model.
- Do NOT make OmniX a hard dependency (accounts may run on `session_web` alone).
- Do NOT reduce CREDENTIAL security.
- Do NOT skip the portability acceptance test.
- Do NOT treat network/rate-limit as session expiration.
- Do NOT create duplicate X account/media/contact/channel/event/config models.
