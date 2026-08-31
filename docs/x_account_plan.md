# Odoo + X Integration Module — Investigation & Implementation Plan

**Goal:** New native Odoo module (`x_account`) that absorbs XAction's X account/session
management, tasks, and events into Odoo, then removes the XAction runtime. Reuse-first,
minimal new models. XAction is disposable — Odoo becomes the single system.

**Status:** Architecture validated against the live Odoo DB and installed-source, and
against the XAction source/DB. Research complete; implementation phased below.

---

## Part 1 — XAction reverse-engineering report

XAction = Node.js app at `/root/XActions` (running: `xactions-api`, `xactions-worker`,
`xactions-postgres`, `xactions-redis`). It wraps the **undocumented X web/API** (the
browser session-cookie API) to manage authenticated sessions and automate X.

**Data model (Prisma `schema.prisma`):**

- `User` — platform user; optional OAuth tokens (`twitterAccessToken/RefreshToken/Expiry`)
  or `sessionCookie` (encrypted), `authMethod`.
- `Account` — linked X session-cookie account: `username`, `displayName`, `sessionCookie`,
  `authMethod` (default `session`), `isActive`, `isBlocked`, `lastUsedAt`.
  *(plaintext cookie here — a defect the migration fixes)*
- `Group` / `GroupAccount` (join) — account groups + automation `actions` JSON,
  `autoExecute`, `cooldownSec`, `paused`.
- `GroupMember` — targeted X users per group.
- `GroupTask` — per-account queued action: `status` (PENDING), `priority`, `claimedAt`,
  `retryCount`, `nextRetryAt`, `error`, `result`.
- `XGroupMember` — X group-DM conversation participants.
- `JobQueue` / `Operation` — job queue + operation log.
- `AccountSnapshot` / `FollowerSnapshot` / `FollowerChange` / `UnfollowerSchedule` —
  follower analytics.

**Session format & security:**

- Session = raw X cookie string `auth_token=...; ct0=...` (+ `kdt`, `twid`, ...), as copied
  from browser DevTools.
- Encrypted at rest: `aes-256-gcm`, key = `scryptSync(COOKIE_ENCRYPTION_KEY ||
  SESSION_SECRET || JWT_SECRET, salt, 32)`, blob = `salt:iv:authTag:ciphertext`
  (route `api/routes/session-auth.js`).
- Validation: `GET https://x.com/i/api/1.1/account/verify_credentials.json` with
  bearer + cookie + `x-csrf-token: ct0` headers.
- Cookie masked in API responses (`auth_token=***; ct0=***`), only `hasCookie` exposed.

**Runtime/provider:**

- Native `fetch` client at `src/scrapers/twitter/http/` (`TwitterAuth`, `TwitterHttpClient`;
  bearer `AAAAAAAAAAAA...ANRILgA...`, guest-token activation, GraphQL + REST 1.1).
  **No Puppeteer/Chromium needed for this provider** (browserAutomation.js is a separate
  extension-only path we do not port).
- Group automation: `api/services/groups/{automationWorker,taskGenerator,executor,
  claimStore,rateLimiter}.js` (GroupTask execution + per-account locking).
- X group-DM sync: `api/services/xGroups/{sync,extractor,lock,urlParser}.js`.
- **Worker/queue:** Bull + Redis (`api/services/jobQueue.js`); retries (3, exp backoff),
  progress, Socket.IO job events, optional `callbackUrl` webhook. Events are ephemeral —
  there is no durable event bus to migrate.

**Live data:** 5 `User`, 1 `Account`, light groups/tasks → migration surface is small.

---

## Part 2 — Odoo architecture report (installed & verified against source)

Confirmed against live DB `odoo_2026-08-11_22-38-33` and the container's Odoo source.

### Existing X media record (verified)

`social_twitter` auto-installs **`social_media_twitter`** (`social.media`,
`media_type='twitter'`, name "X", `noupdate="1"`) plus `social.stream.type` records
regardless of auth method. **We reuse this record** — do NOT create a new `social.media`
for X. Our module branches `_action_add_account()` by chosen auth method and adds X
session fields to `social.account`.

### `social_twitter` = publish/stats only (verified — this is important)

Inspected `social_twitter/models/{social_media,social_account,res_config_settings}.py`
and `controllers/`. Findings:

- **No real OAuth flow server-side.** It reads consumer key/secret from
  `ir.config_parameter` (`social.twitter_consumer_key/secret`) or from **Odoo IAP**, then
  does OAuth **1.0a** request-token → redirect → callback. Connect requires either
  self-hosted app keys or an IAP subscription.
- Account holds **`twitter_user_id`, `twitter_oauth_token`, `twitter_oauth_token_secret`**
  (OAuth tokens) — **not** session cookies. The token model does not represent an XAction
  session-cookie account.
- Its API surface is **publishing + stats + streams**: `_format_tweet`, media upload,
  `_get_last_tweets_stats`, `twitter_get_user_by_username`, stream posts. **It does not
  implement DM / group-DM / the automation actions XAction performs.**
- It also overrides `_compute_statistics` / `_compute_stats_link` / `create` for default
  streams.

**Conclusion:** "XOfficialProvider wrapping social_twitter" works **only for publishing /
tweet-side ops**, and only with a separate OAuth token set. For the XAction operations we
are replacing (sessions, DMs, group automation), the **undocumented web-session path is
the actual carrier**. We must therefore **separate authentication from provider** (see
Part 5) and keep the OAuth/publish path as an optional secondary, not the core.

### Reusable modules

| Module | Reuse for |
|---|---|
| `social`, `social_media`, `social_twitter` | `social.account` (X account home; existing `twitter_user_id`, `twitter_oauth_token[_secret]`, `active`, `media_id`, `is_media_disconnected`); reuse `social_media_twitter`; custom-provider layout pattern from `social_reddit` |
| `social_reddit`, `social_tiktok`, `social_custom_relay` | Custom-provider layout pattern (inherit `social.account`/`social.media`, `res.config.settings`, `services/*_client.py`) |
| `discuss` (bundled inside `mail`) | **X DMs / group-DMs** via `discuss.channel` + `discuss.channel.member` + `mail.message` — proven pattern in `whatsapp_evaluation`/`madarbot_bridge` (`channel_type='whatsapp'`/`'telegram'`). **But** requires an `x.message` mirror (see Part 5) to carry external identity |
| `contacts` | **X contacts/participants** via `res.partner` (extend with `x_user_id`, `x_username`, ...) |
| `mail`, `mail_enterprise` | Account **lifecycle events** via `mail.message` chatter + `mail.activity` on `social.account` |
| `base_automation` | Group automation **rules** (conditions + server action → enqueue `x.account.task`) |
| `ir.cron` (base) | MVC-periodic tasks (session validation sweep, task queue processing). **No OCA `queue_job` installed** |
| `res.config.settings` + `ir.config_parameter` | Provider/auth toggles, encryption-key ref, base URLs |
| `whatsapp_evaluation` / `ai_whatsapp` | Testing conventions (`@tagged('post_install','-at_install')`, `MailCommon`, `@patch`, `scripts/run-tests.sh`) |

**Not installed / not relied on:** `queue_job` (OCA), `madarbot_bridge` (message-routing
reference only; Discussion models come from the `mail` bundle).

---

## Part 3 — Corrections applied from architecture review (7 items)

1. **`x.session.store` is in-the-DB, not out-of-band.** It stays an Odoo model (PostgreSQL),
   so it IS on backups/snapshots/replication and can be reached via ORM. Security therefore
   rests **entirely on key separation** (`X_SESSION_ENCRYPTION_KEY`, never in DB) + access
   rules + masking. We adopt this and stop claiming otherwise. No existing alternative
   secret vault is installed, so `x.session.store` is the minimal dedicated vault record.

2. **Drop redundant `x_session_ref`.** Keep a single canonical relation
   `social.account.x_session_store_id = Many2one('x.session.store')`. The store record may
   carry its own internal opaque id; no second Char ref on the account.

3. **The cookie-session client is the highest-risk, isolated compatibility layer.**
   Rename the second provider to **`SessionWebProvider`** and mark it: *compatibility
   provider, isolated behind one interface, replaceable*. ALL X HTTP calls go
   model → `XService` → `XProvider` → implementation. Never `requests.get("https://x.com")`
   from a model.

4. **Do NOT promise `social_twitter` OAuth reuse without verification.** Verified above: it
   is publish/stats-only with an OAuth *1.0a* token model, no DM/automation, and its auth
   needs IAP or self-hosted keys. The core carrier is the session-web provider. `social_twitter`
   is reused only as an **optional publish extension** (`XOfficialPublishAdapter`), separate
   from authentication and from the session provider.

5. **`discuss.channel` needs an external-identity mirror; do not use it as a thin wrapper.**
   We add `x.message` (direction, external id, external timestamps, author/participant,
   `discuss.channel_id`, `mail_message_id`) to fully carry X's identity model — mirroring the
   proven `whatsapp_evaluation` pattern (which keeps `whatsapp.message` with an
   `('inbound','Inbound')` direction + `whatsapp_number` external identity on the channel).
   `discuss.channel` gets explicit X identity fields + uniqueness constraints.

6. **`ir.cron` is the MVP worker, not the production-scale queue.** The MVP uses
   `x.account.task` + single cron + `claimed_at`/skip-locked. The plan documents a
   **production-scale path**: keep `x.account.task` but hook into an approved Preferable
   queue mechanism (e.g. OCA `queue_job`) once the install/base is chosen, so we don't hand-roll
   a confining queue. We also schedule per-account validation, not a full-table scan.

7. **Trim the `social.account` inverse fields.** Only
   `x.account.group.account_ids` (M2M) and `x.account.task.account_id` (M2O) are needed.
   Inverse `group_ids`/`task_ids` on `social.account` are added **only if** a view genuinely
   needs them — deferred.

---

## Part 4 — Reuse matrix (final)

| Requirement | Existing model/component | Decision | New? | Reason |
|---|---|---|---|---|
| X account | `social.account` | Extend | No | Already models external X accounts + OAuth fields |
| X identity | `social.account.twitter_user_id` + `social_account_handle` | Reuse | No | Existing fields cover X ID/username |
| X media record | `social_media_twitter` (`media_type='twitter'`) | **Reuse** | No | Auto-installed by `social_twitter`; don't duplicate |
| Connection status | `social.account.is_media_disconnected` | Extend | No | Add `x_connection_status` Selection + `last_connected`/`last_validated`/`last_error` |
| Session reference | — | New relation | No | `x_session_store_id` (single M2O) |
| Secure session store | — | **New `x.session.store`** | **Yes** | Key-separation + access-controlled vault; no existing equivalent |
| Session manager | — | New Python service | Service | `XSessionManager` |
| X DM / group-DM | `discuss.channel` + `discuss.channel.member` + `mail.message` | **Extend** | No | `channel_type='x'`/`'x_group'` + account/partner links + `x_conversation_id` (whatsapp pattern) |
| X message identity | — | **New `x.message`** | **Yes** | External/direction/author identity mirror (like `whatsapp.message`) |
| X contacts | `res.partner` | **Extend** | No | `x_user_id`, `x_username`, etc. (`contacts`) |
| Account lifecycle events | `mail.message` + `mail.activity` on account | **Reuse** | No | Chatter logging; no new event model |
| Group / membership | — | **New `x.account.group`** + M2M `social.account` | **Yes** | No existing business-grouping model |
| Group rules | `base_automation` | Reuse | No | Conditions + server action |
| Task queue | — | **New `x.account.task`** + `ir.cron` (MVP) | **Yes** | No queue_job installed; `ir.cron` is scheduler, not a queue |
| Worker | `ir.cron` | Reuse | No | MVP single cron + per-account lock; prod path documented |
| Credential encryption | — | New helpers | — | Env key `X_SESSION_ENCRYPTION_KEY`, never in DB |
| Provider abstraction | `services/*_client.py` pattern | New services | Service | `XProvider` interface; `SessionWebProvider` (ported), `XOfficialPublishAdapter` (optional) |
| Config | `res.config.settings` / `ir.config_parameter` | Reuse | No | Auth/provider toggles, key, URLs |
| Publish (optional) | `social_twitter` | Extend (optional) | No | OAuth 1.0a token; publish/stats only | 
| Auth UI | `social.media._action_add_account` + controller | Extend | No | Branch by auth method; "import session cookie" wizard/callback |

**New models (4, the minimum):**
1. `x.session.store` — encrypted credential vault
2. `x.account.group` — X account groups + automation actions
3. `x.account.task` — durable retryable/owned/prioritized job queue
4. `x.message` — external conversation/message identity mirror for discussion

(`x.account.event` dropped — lifecycle via `mail.message` chatter, conversations via
`discuss.channel` + `x.message`.)

### New-model justifications

- **`x.session.store`**: no installed Odoo secret vault gives key-separated, access-ruled,
  masked credential storage; inheritance cannot add a proper tombstone to `social.account`
  because the blob must be excluded from standard reads/logging and isolated by access rules.
  Security = key separation (env) + ACL + masking; it is **in-DB**, so we rely on key
  separation, not "out-of-band" claims.
- **`x.account.group`**: no installed model is "a named collection of external X accounts with
  automation actions"; `mail.group`/`res.groups` are semantically wrong.
- **`x.account.task`**: no OCA `queue_job` installed; `mail.activity` is a user-todo;
  `ir.cron` is a scheduler not a queue.
- **`x.message`**: `discuss.channel`/`mail.message` alone cannot carry external identity
  (external id, direction, external timestamps, author/participant join/leave). The
  `whatsapp_evaluation` precedent (dedicated `whatsapp.message`) proves this mirror is needed.

---

## Part 5 — Architecture

### Separation of concerns (per review item on "provider vs session")

We separate two orthogonal dimensions:

```
Authentication
├── Session Cookie  (auth_token, ct0)  → SessionWebProvider
└── OAuth 1.0a      (tokens)           → XOfficialPublishAdapter (optional, publish only)

Provider (X interface)  →  XProvider  (dispatch by auth)
├── SessionWebProvider  (core carrier: sessions, DMs, group automation; ported)
├── XOfficialPublishAdapter (optional: publish/stats via social_twitter tokens)
└── OmniXProvider         (optional REST provider; per-account either/or with session)
```

All model → X traffic goes through `XService` → `XProvider`. A model never calls X HTTP
directly.

```
                        ODOO (existing-first)
        │
   social.account (X)   ─── x_session_store_id ───▶  x.session.store (encrypted)
   + x_connection_status                              + access rules
   + x_provider, x_auth_method                        + key in env
        │  XSessionManager (svc)
        │
   XService  ──▶  XProvider (interface)
        │          ├─ SessionWebProvider (ported, isolated, replaceable)
        │          └─ XOfficialPublishAdapter (optional, social_twitter OAuth1 tokens)
        │
        ├─────▶  res.partner (X contacts: x_user_id, x_username, ...)
        │
        └─────▶  discuss.channel (channel_type='x' / 'x_group')
                        │  x_conversation_id / x_account_id / x_partner_id
                        └── x.message (mirror: direction, ext id, author, ...)
                            └── mail.message (+ discuss.channel.member)
        │
   x.account.task (ir.cron, per-account lock)   ← MVP queue; prod path documented
   x.account.group + base_automation (rules)
   mail.message / mail.activity (lifecycle on account)
        │
     X API (undocumented web / session)  |  X API (OAuth1 publish)  |  OmniX REST API (optional)
```

### Session + task services

- `XSessionManager`: `create / save / load / restore / validate / invalidate / delete /
  disconnect / reconnect`. In-memory runtime registry `{account_id: XProvider}` separated
  from the persistent `x.session.store`. Startup recovery restores all valid sessions.
- `XTaskService` (XService sub-service): `enqueue / claim / then_run / retry / complete /
  fail` with per-account single-flight lock, retry/backoff, priorities, results.

---

## Part 6 — Migration plan (XAction → Odoo)

Source: `xactions-postgres-1`, db `xactions`. **Non-destructive; XAction kept running until
Stage 4.**

1. **Discover** — `Account` rows (`username`, `displayName`, `sessionCookie`, `isActive`,
   `isBlocked`, `authMethod`) + `User.twitterUsername` / `sessionCookie`.
2. **Convert** — parse cookie strings to canonical `auth_token`/`ct0` (+ helper cookies).
3. **Encrypt** — re-encrypt with the Odoo-managed key into `x.session.store`
   (fixes XAction's plaintext `Account.sessionCookie` defect).
4. **Map** — upsert `social.account` keyed by `twitter_user_id`/handle; `media_id` =
   `social_media_twitter`.
5. **Validate** — `SessionWebProvider.validate_session()` per migrated account;
   mark ACTIVE vs REAUTH_REQUIRED.
6. **Group / Task / DM** — `Group`+`GroupAccount` → `x.account.group` + M2M;
   `GroupTask` → `x.account.task`; `XGroupMember` → `discuss.channel` + `res.partner` +
   `x.message`.
7. **Mode flags** — `x_migration_status` (pending/migrated/failed) + retained
   `source_account_id` / `source_user_id` / `migration_batch_id` / `migration_timestamp`
   for audit + rollback. Staged cutover (shadow → restore → primary → remove).
8. **Rollback** — restore prior `social.account` fields; key backup in env;
   re-point to XAction if validation fails.

### Session portability acceptance test (mandatory before declaring migration success)

Do **not** stop at `verify_credentials == 200`. For one real XAction session:

```
XAction session
  → extract canonical cookies
  → new Python SessionWebProvider
  → authenticate
  → verify_credentials
  → perform a real read operation (get_conversations / DM fetch)
  → perform an allowed test operation (send a harmless DM to self/test account)
  → restore after simulated Odoo restart → re-perform the same operations
```

Then scale: 10 → 25 → 50 concurrent accounts before moving remaining XAction features.

---

## Part 7 — Implementation plan

Module **`x_account`** under `addons/custom/`.
depends = `['social', 'social_twitter', 'contacts', 'base_automation', 'mail']`.

### Files

```
x_account/
  __manifest__.py
  __init__.py
  models/
    __init__.py
    social_account.py        # _inherit social.account → status + session relation
    social_media.py          # _inherit social.media → _action_add_account branch by auth
    discuss_channel.py       # _inherit discuss.channel → channel_type x / x_group
    x_message.py             # x.message mirror (NEW)
    res_partner.py           # _inherit res.partner → X contact fields
    session_store.py         # x.session.store (NEW)
    account_group.py         # x.account.group (NEW)
    account_task.py          # x.account.task (NEW)
    res_config_settings.py   # encryption-key, auth/provider toggle, URLs
  services/
    __init__.py
    x_service.py             # XService façade (models → provider; never X from a model)
    session_manager.py       # XSessionManager
    task_service.py          # XTaskService (enqueue/claim/retry/lock)
    x_provider.py            # XProvider interface + dispatch
    providers/
      __init__.py
      session_web.py         # SessionWebProvider — ported cookie-session client (isolated)
      official_publish.py    # XOfficialPublishAdapter (optional; social_twitter OAuth1)
  controllers/
    __init__.py
    main.py                  # /x_account/import_session, callback, webhook
  wizards/
    __init__.py
    import_session.py        # paste session cookie wizard
  data/
    cron.xml                 # validate-sessions, process-task-queue crons
  security/
    ir.model.access.csv
  views/
    social_account_views.xml
    account_group_views.xml
    account_task_views.xml
    res_config_settings_views.xml
  migrations/
    19.0.1.0.1/              # XAction migration scripts (non-destructive)
  tests/
    __init__.py
    test_session.py
    test_account_lifecycle.py
    test_task_queue.py
    test_portability.py      # session restore across "restart" + real read/write ops
```

### Models & fields

- `social.account` (+): `x_connection_status` Selection (NEW / AUTHENTICATING / ACTIVE /
  DISCONNECTED / INVALID / REAUTH_REQUIRED / ERROR / DISABLED); `last_connected`,
  `last_validated`, `last_error`; `x_provider` Selection (session_web / official_publish);
  `x_auth_method` Selection (session_cookie / oauth1); `x_session_store_id` Many2one
  `x.session.store` (single canonical relation); `x_migration_status` Selection
  (pending/migrated/failed) + `source_account_id`/`source_user_id`/`migration_batch_id`/
  `migration_timestamp`. **No** `group_ids`/`task_ids` inverse unless a view needs them
  (deferred).
- `social.media` (+): branch `_action_add_account()` by configured auth method; reuse
  existing `social_media_twitter` record — do not create a new X media record.
- `discuss.channel` (+): `channel_type` `selection_add` `'x'` and `'x_group'`;
  `x_account_id` M2O `social.account`; `x_partner_id` M2O `res.partner`;
  `x_conversation_id` (Char, indexed) with uniqueness constraint for the X external id;
  message routing mirroring `whatsapp_evaluation` (`message_post`/`_notify_thread`,
  `last_x_mail_message_id`, etc.).
- `x.message` (NEW): `channel_id` M2O `discuss.channel`; `account_id` M2O `social.account`;
  `direction` Selection (inbound/outbound); `external_id` Char (unique per channel);
  `body_plain` Text; `external_created_at` Datetime; `author_partner_id` M2O `res.partner`;
  `author_x_id`/`author_x_username`; `acked`/`delivered`; optional `participant_joined` /
  `participant_left` markers; `mail_message_id` M2O `mail.message`.
- `res.partner` (+): `x_user_id`, `x_username`, `x_following`, `x_blocked`.
- `x.session.store`: `account_id` M2O `social.account`; `encrypted_blob` Text; `alg`
  (`aes-256-gcm`); `created_at`, `last_access_at`; access via `group_x_account_manager` +
  `base.group_system`.
- `x.account.group`: `name`, `description`, `account_ids` M2M `social.account`, `actions`
  (Selection/JSON like `{like, comment, repost, follow}`), `auto_execute`, `cooldown_sec`,
  `paused`.
- `x.account.task`: `account_id` M2O; `group_id` M2O; `operation`; `status` (PENDING /
  RUNNING / SUCCESS / FAILED / CANCELLED); `priority`; `retry_count`; `max_attempts`;
  `claimed_at`; `next_retry_at`; `error`; `result`.

### Methods

- `XService` façade: single entry point; resolves the account's `XProvider` via
  `x_provider`/`x_auth_method`, passes session from `XSessionManager`. Models/tests never
  call X HTTP directly.
- `XSessionManager`: `create/save/load/restore/validate/invalidate/delete/disconnect/
  reconnect`; in-memory registry `{account_id: XProvider}`; startup recovery.
- `XTaskService`: `enqueue/claim/then_run/retry/complete/fail`; per-account single-flight
  lock (skip-locked claim), retry/backoff, priority ordering.
- `SessionWebProvider`: port `TwitterAuth` (loginWithCookies/validate) + `TwitterHttpClient`
  (requests; bearer `ANRILgA...`, guest activation, `verify_credentials`, `ct0` CSRF) and the
  ops the module needs: `get_conversations`, `get_events`, `get_dms`, `send_dm`, group-DM sync,
  and the group automation actions. **Isolated + replaceable.**
- `XOfficialPublishAdapter` (optional): delegate to `social_twitter` OAuth 1.0a token methods
  for publishing/stats only.
- Lifecycle state machine transitions (documented): NEW → AUTHENTICATING → ACTIVE;
  invalid → INVALID; → REAUTH_REQUIRED (challenge); → DISABLED; → ERROR. Network /
  rate-limit errors keep the account ACTIVE (not invalid).
- Cron: `_validate_sessions_cron()` (per-account scheduled sweep, not full-table scan, with
  one-account failure isolation) and `_process_task_queue()` (claim + run + reschedule).

### Queue/MVP vs production

- **MVP:** `x.account.task` + `ir.cron` + skip-locked per-account claim.
- **Production scale (50+ accounts):** keep `x.account.task`, switch the executor to an
  **approved/installed queue mechanism** (e.g. OCA `queue_job`) rather than hand-rolling a
  full queue. Documented as a migration path in `tasks/`.

### Security rules

- `x.session.store` (encrypted blob, session refs, task payload) invisible to non-manager
  groups; `ir.model.access.csv` grants read/write only to `group_x_account_manager` +
  `base.group_system`; no secrets in `_compute_display_name`/chatter/logs/API (masked like
  XAction's `maskCookie`). Security depends on key separation + ACL + masking (in-DB).

### Observability

- Status fields + chatter `mail.message` + optional `mail.activity` warnings when status
  → REAUTH_REQUIRED / INVALID. No credential exposure.

### Tests (follow `ai_whatsapp` conventions)

- `@tagged('post_install', '-at_install')`, extend `MailCommon`, mock
  `verify_credentials`/`requests`.
- Session encrypt → save → restore → validate roundtrip (survives simulated restart).
- Invalid-session classification (network vs rate-limit vs invalid).
- Account lifecycle state machine transitions.
- Task queue claim/concurrency/retry/backoff on one account.
- Account isolation (task on account A cannot use account B session).
- Migration mapping (given XAction rows) incl. failure + rollback.
- Session portability acceptance test (Part 6) with real read + allowed write ops.
- Run via `./scripts/run-tests.sh x_account`.

### Migration route

`migrations/` — documented scripts reading the XActions DB; **non-destructive** (XAction
kept until Stage 4). `.env`/`odoo.conf` additions: `X_SESSION_ENCRYPTION_KEY` (exported,
never logged).

### Build order (each verifiable)

- **PHASE 0 — Validate architecture assumptions** (done: XAction + Odoo source + DB
  inspected; `social_twitter` publish-only confirmed; media record reuse confirmed).
- **PHASE 1 — `social.account` + session persistence** (`x.session.store` + relation +
  `XSessionManager` + `x_provider`/`x_auth_method`/status fields + ACL).
- **PHASE 2 — Python session provider** (`SessionWebProvider`, ported client; isolated).
- **PHASE 3 — Restart/restore/validation** (startup recovery + cron sweep + portability
  acceptance test single-account).
- **PHASE 4 — XAction migration** (discover→convert→encrypt→map→validate→flag→shadow→
  restore→primary→remove; rollback).
- **PHASE 5 — Task execution** (`x.account.task` + `XTaskService` + cron + locking;
  scale 10→25→50).
- **PHASE 6 — DM / Group DM** (`discuss.channel` x/x_group + `x.message` mirror +
  `res.partner` contacts + mail.message lifecycle events).
- **PHASE 7 — Groups / automation** (`x.account.group` + `base_automation` rules).
- **PHASE 8 — Official/OmniX integrations** (`OmniXProvider` optional REST provider;
  `XOfficialPublishAdapter` optional publish).

---

## Deliverable conclusion

Reuse installed **`social` / `social_twitter` / `social_media`** (extend `social.account`,
reuse the auto-installed `social_media_twitter` record, branch add-account by auth),
**`discuss.channel`** (via `mail`) for X DMs / group-DMs with an **`x.message`** identity
mirror (the `whatsapp_evaluation` precedent), **`contacts` (`res.partner`)** for X contacts,
**`res.config.settings` / `ir.config_parameter`** for config, **`base_automation`** for group
rules, **`mail` / `mail.activity`** for lifecycle event logging, **`ir.cron`** for the MVP
worker, and **`social_reddit` layout + `ai_whatsapp` test conventions**.

Absorb XAction's **undocumented cookie-session HTTP client** as an isolated, replaceable
Python `SessionWebProvider` behind a single `XService`/`XProvider` interface — no Puppeteer,
no XAction runtime. Separate **authentication** (session-cookie vs OAuth 1.0a) from
**provider** so session, official-publish, and future OmniX do not get tangled.

Add **four unavoidable new models** (`x.session.store`, `x.account.group`, `x.account.task`,
`x.message`) plus the `XService` / `XSessionManager` / `XTaskService` / `XProvider` Python
layers. The core proof is: **one real XAction account → migrate session → shutdown XAction →
restart Odoo → restore same session → authenticate → perform the required X operation**,
then scale to 25 → 50 concurrent accounts. Odoo becomes the single X account / session /
task / event system.
