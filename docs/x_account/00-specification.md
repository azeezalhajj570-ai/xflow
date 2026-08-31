# Specification: Native Odoo X Account & Session Platform (XAction Replacement)

**Status:** AUTHORITATIVE — validated architecture; implementation NOT started.
**Module:** `x_account` (planned), under `addons/custom/`.
**Source input:** `docs/x_account_plan.md` (validated investigation).
**Goal:** Replace the external XAction runtime with a native Odoo subsystem that owns X
accounts, authentication/session persistence, session restoration and validation, account
lifecycle, provider integration, tasks, DM/group-DM integration, account grouping,
automation, and migration from XAction. XAction becomes disposable after a proven,
non-destructive, staged cutover.

**Final principle:** Do NOT "build another XAction using Odoo." Reuse Odoo's existing
architecture and create only the minimum genuinely necessary X-specific components.

---

## 1. Objective

Turn Odoo into the single system for X account & session management:

```
Today:        Odoo ──▶ XAction ──▶ X
Target:       Odoo ──▶ x_account ──▶ X / supported provider
```

The `x_account` module absorbs (and, after cutover, replaces) XAction's:

- X accounts
- X authentication / session persistence
- session restoration
- session validation
- account lifecycle
- X provider integration
- tasks
- DM / group-DM integration
- account grouping
- automation
- migration from XAction

**Who:** Businesses currently relying on XAction to manage multiple X
session-cookie accounts (target 50+ accounts) that want a native, auditable,
backed-up, Odoo-native replacement.

**Success looks like:** one real XAction account is migrated → Odoo restores and validates
the same session → a required read operation and a permitted harmless write operation
succeed → Odoo restarts → the same session is restored and the operations repeat, all
without XAction. Then it scales to 10 → 25 → 50 accounts.

---

## 2. Scope

### In scope
1. X account model (extend `social.account`).
2. Encrypted session persistence and a central session manager.
3. Session restoration surviving all Odoo/deployment restarts (unless the X session itself
   is invalid).
4. Session validation + account lifecycle state machine.
5. A replaceable, isolated `SessionWebProvider` ported from XAction's cookie-session client.
6. Durable task queue (`x.account.task`) + `ir.cron` MVP worker.
7. X DM / group-DM integration via `discuss.channel` + `x.message` + `res.partner`.
8. Account grouping (`x.account.group`) + automation via `base_automation`.
9. Non-destructive, staged migration from XAction with rollback and audit.
10. Comprehensive security, portability, and performance testing.
11. **OmniX provider option** — third-party X REST API (Bearer API key + the account's
    `auth_token` cookie) covering DM/tweet/like/retweet/follow; per-account **either/or**
    with `SessionWebProvider`. Optional; no dependency.

### Out of scope (do not build in MVP)
- **XAction Puppeteer/browser automation** — not required; the primary ported provider is a
  native HTTP client.
- **XOfficialPublishAdapter** — OPTIONAL publish/stats path via `social_twitter`; does not
  replace session-based functionality. Available later.
- Follower analytics snapshots (`AccountSnapshot`, `FollowerSnapshot`, `FollowerChange`,
  `UnfollowerSchedule`) — preserved as source data for reference; not recreated in MVP
  unless a confirmed XAction capability requires them.

---

## 3. Validation inputs (completed investigation — do not redo)

`docs/x_account_plan.md` records findings from XAction source, the live XAction PostgreSQL
DB, installed Odoo modules, Odoo source, the live Odoo DB, `social_twitter`,
`whatsapp_evaluation`, and existing custom modules. Key verified facts preserved:

### 3.1 XAction runtime (source of truth for requirements)
- Node.js app at `/root/XActions`; containers `xactions-api-1`, `xactions-worker-1`,
  `xactions-postgres-1`, `xactions-redis-1`.
- Prisma models (verified live columns): `User`, `Account` (`username`, `displayName`,
  `sessionCookie`, `authMethod`, `isActive`, `isBlocked`, `userId`, `profileUrl`, `avatar`,
  `lastUsedAt`), `Group` (`name`, `description`, `actions`, `autoExecute`, `cooldownSec`,
  `paused`, `userId`), `GroupAccount` (`groupId`, `accountId`), `GroupMember`
  (`username`, `displayName`, `groupId`), `GroupTask` (`action`, `targetId`, `memberId`,
  `status`, `priority`, `retryCount`, `nextRetryAt`, `claimedAt`, `error`, `result`,
  `groupId`, `accountId`), `XGroupMember` (`conversationId`, `xUserId`, `username`,
  `displayName`, `isAdmin`, `isCurrentMember`, `firstSeenAt`, `lastSeenAt`, `avatarUrl`),
  `JobQueue`, `Operation`, snapshots.
- Session = raw X cookie string `auth_token=...; ct0=...` (+ `kdt`, `twid`, ...).
- Session encryption in XAction: `aes-256-gcm`, key = `scryptSync(COOKIE_ENCRYPTION_KEY ||
  SESSION_SECRET || JWT_SECRET, salt, 32)`, blob `salt:iv:authTag:ciphertext`; stored on
  `User.sessionCookie`. **Defect:** `Account.sessionCookie` is stored plaintext (fixed in
  migration).
- Validation endpoint: `GET https://x.com/i/api/1.1/account/verify_credentials.json`
  (bearer + cookie + `x-csrf-token: ct0`). Bearer `AAAAAAAAAAAAA...ANRILgA...`.
- Provider is a native `fetch` HTTP client (`src/scrapers/twitter/http/`) — **no
  Puppeteer/Chromium required** for the ported path.
- Queue = Bull + Redis; retries (3, exp backoff); events are ephemeral (no durable bus).
- Live data: 5 `User`, 1 `Account` (session `wfaw0533`), light groups/tasks.

### 3.2 Odoo environment
- Odoo 19 Enterprise (`kerbi/odoo19e-202604`), container `odooo-odoo`; DB
  `odoo_2026-08-11_22-38-33` on `madarbot-postgres-1`.
- Installed & reusable: `social`, `social_media`, `social_twitter`, `social_reddit`,
  `social_tiktok`, `social_custom_relay`, `mail`, `mail_enterprise`, `contacts`,
  `base_automation`, `crm`, `im_livechat`, `ai_whatsapp`, `whatsapp_evaluation`.
- **Not installed:** OCA `queue_job`, standalone `discuss` module (Discussions models come
  from the `mail` bundle).
- `social_twitter` is **publish/stats-only** (verified source): OAuth 1.0a, tokens
  `twitter_oauth_token`/`_secret`, media upload, stats, streams. **No DM/group-DM/session
  capability.** It auto-installs `social_media_twitter` (media_type `twitter`, name "X",
  `noupdate=1`).
- **OmniX** (verified external docs) is a third-party X REST API at
  `https://api.omnixapi.com/api/v1/twitter` — flat `$0.001`/call, Bearer API key + the
  account's `auth_token` cookie (query param on GET, JSON body field on POST). Covers DM
  inbox/read/send, tweet create/like/retweet, follow, user info, webhooks — **the same
  `auth_token` already stored in `x.session.store`**, so it slots into the provider
  interface with no new credential model.
- `whatsapp_evaluation` is the canonical template: `discuss.channel` extended with a custom
  `channel_type` + a dedicated `whatsapp.message` mirror (direction, external identity) +
  `res.partner` mapping + `message_post`/`_notify_thread` overrides.
- Test conventions: `@tagged('post_install','-at_install')`, `MailCommon`, `@patch`,
  `scripts/run-tests.sh <module>`.

---

## 4. Architectural Constraints (binding)

C1. **Existing-first Odoo strategy.** Prefer extend/inherit an existing model over a new
duplicate. A new model is permitted ONLY when: (1) no existing model represents the
concept, (2) no close alternative exists, (3) inheritance cannot solve it, and (4) reuse
would create semantic/data problems.

C2. **Four new models, the maximum.** `x.session.store`, `x.account.group`,
`x.account.task`, `x.message`. No additional models without evidence.

C3. **Reuse, never re-create.** Reuse `social.account`, `social.media`,
`social_media_twitter`, `res.partner`, `discuss.channel`, `discuss.channel.member`,
`mail.message`, `mail.activity`, `res.config.settings`, `ir.config_parameter`,
`base_automation`, `ir.cron`. Do NOT create X Account / X Media / X Contact / X DM Channel /
X Event / X Configuration duplicate models.

C4. **Authentication ≠ provider.** These are independent dimensions. Do not couple
`cookie = provider` or `oauth = provider`.
- Authentication: `session_cookie` (`auth_token`, `ct0`, required extras) | `oauth1`
  (access token, access token secret).
- Provider: `SessionWebProvider` (core) | `XOfficialPublishAdapter` (optional, publish)
  | `OmniXProvider` (optional REST provider; per-account either/or with session).

C5. **All X HTTP behind XService → XProvider.** Models MUST NEVER perform X HTTP requests
directly. No `requests.get("https://x.com/...")` inside any Odoo model.

C6. **Session persistence is first-class.** Lifecycle: Authenticate → persist encrypted
session → Odoo restart → load → restore runtime client → validate → ACTIVE. Must survive
worker restart, process restart, server reboot, browser/client restart, deployment restart
unless the X session itself is invalid.

C7. **Encryption.** `AES-256-GCM` (or equivalent authenticated encryption), key separation
from the DB. Key via deployment config (`X_SESSION_ENCRYPTION_KEY` or impl-chosen name);
never stored in PostgreSQL, never logged, never returned through normal APIs.

C8. **Security at rest is key separation + ACL + masking** (in-DB, not out-of-band).
`x.session.store` lives in PostgreSQL and is therefore on backups/snapshots/replication;
its security rests on the separate key + tight ACLs + masking.

C9. **MVP queue = `x.account.task` + `ir.cron` + skip-locked claim.** Production must leave
a clean path to an approved Odoo queue mechanism; do not create a second unrelated queue
system.

C10. **Task isolation.** An account's task may use ONLY that account's session. Cross-account
credential use is prohibited.

C11. **Migration non-destructive and staged.** XAction stays authoritative until Stage 3 and
remains available until removed in Stage 4. Never delete source records during migration.

C12. **OmniX is an optional provider, not a dependency.** An account may use
`SessionWebProvider` without any OmniX key. The system must not depend on OmniX being
available, configured, or even installed.

C13. **Session portability acceptance test is mandatory** before multi-account scaling. Do
not pass on `verify_credentials == 200` alone; must perform real read + permitted harmless
write, then survive a restart and repeat.

C14. **Provider-independent business logic.** Business logic must not depend directly on
OmniX, `SessionWebProvider`, or raw X HTTP calls. Providers are swappable behind
`XProvider`.

---

## 5. Approved Reuse Matrix

| Requirement | Existing model/component | Decision | New? | Justification |
|---|---|---|---|---|
| X account | `social.account` | Extend | No | Already models external X accounts; has `name`, `social_account_handle`, `active`, `media_id`, `media_type`, `is_media_disconnected`, `twitter_user_id` |
| X identity (id/username) | `social.account.twitter_user_id` + `social_account_handle` | Reuse | No | Existing fields already carry X ID/username |
| X media record | `social_media_twitter` (media_type `twitter`) | **Reuse** | No | Auto-installed by `social_twitter`, `noupdate=1`; creating another X media would duplicate |
| Connection status | `social.account.is_media_disconnected` | Extend | No | Add `x_connection_status` Selection + timestamp/error fields |
| Session reference | — | New relation | No | Single M2O `x_session_store_id` on `social.account` (see §7; drop the redundant Char ref) |
| Secure session store | — | **`x.session.store`** | **Yes** | No existing Odoo secret vault; inheritance can't add a proper isolated tombstone |
| Session manager | — | `XSessionManager` (service) | Service | No existing Odoo session manager for X cookies |
| DM / group-DM conversation | `discuss.channel` + `discuss.channel.member` + `mail.message` | Extend | No | `whatsapp_evaluation`/`madarbot_bridge` precedent; add `channel_type='x'`/`'x_group'` + identity fields |
| External message identity | — | **`x.message`** | **Yes** | `mail.message`/`discuss.channel` alone cannot carry external id/direction/timestamps/author; `whatsapp.message` precedent |
| X contacts/participants | `res.partner` | Extend | No | `contacts` is the identity model; add X fields |
| Account lifecycle events | `mail.message` + `mail.activity` on account | **Reuse** | No | Chatter/activity; do NOT create `x.account.event` |
| Account grouping | — | **`x.account.group`** + M2M `social.account` | **Yes** | No existing business-grouping model; `mail.group`/`res.groups` are semantically wrong |
| Group rules/automation | `base_automation` | Reuse | No | Conditions + server action → enqueue `x.account.task` |
| Task queue | — | **`x.account.task`** + `ir.cron` | **Yes** | `queue_job` not installed; `ir.cron` is scheduler, not a queue; `mail.activity` is a todo |
| Worker | `ir.cron` | Reuse | No | MVP single cron + per-account skip-locked claim; prod path documented |
| Credential encryption | — | New helpers | — | Env key, `AES-256-GCM`, never in DB |
| Provider abstraction | `social_reddit` `services/*_client.py` pattern | New services | Service | `XService`/`XProvider` interface + implementations |
| Config | `res.config.settings`/`ir.config_parameter` | Reuse | No | Auth/provider toggles, key ref, base URLs |
| Publish (optional) | `social_twitter` | Extend (optional) | No | OAuth 1.0a; publish/stats only; NOT a session/DM replacement |
| OmniX provider (optional) | — | **New service** `OmniXProvider` behind `XProvider` | Service | Third-party REST API; reuses the `auth_token` already in `x.session.store`; DM/tweet/follow; per-account either/or with session; no new model |

---

## 6. Required New Models — Justification

### 6.1 `x.session.store` — encrypted credential vault (NEW)
- **Why:** No installed Odoo model provides key-separated, access-ruled, masked encrypted
  credential storage. Simple inheritance cannot add a secure isolated tombstone to
  `social.account` because the encrypted blob must be excluded from standard reads/logging
  and isolated by its own ACLs.
- **Caveat (C8):** It is an in-DB model (PostgreSQL), so it is on backups/snapshots/
  replication and reachable via ORM. Security rests ENTIRELY on key separation
  (`X_SESSION_ENCRYPTION_KEY` in deployment config, never in DB), strict ACLs, and masking.
  It is NOT described as an external/out-of-band vault.
- **Fields (minimum):** `account_id` M2O `social.account`, `encrypted_blob` Text, `alg`
  (`aes-256-gcm`), `created_at`, `last_access_at`.

### 6.2 `x.account.group` — X account grouping (NEW)
- **Why:** No installed model is "a named collection of external X accounts with automation
  actions." `mail.group` / `res.groups` are security/chat groups (wrong semantics); partner
  groupings are irrelevant. Inheritance cannot turn `res.groups` into a business grouping.
- **Fields (smallest schema necessary):** `name`, `description`, `account_ids` M2M
  `social.account`, `actions` (Selection/JSON like `{like, comment, repost, follow}`),
  `auto_execute`, `cooldown_sec`, `paused`. **No** additional group-member model unless a
  confirmed integration need proves it necessary.

### 6.3 `x.account.task` — durable task queue (NEW)
- **Why:** No OCA `queue_job` installed; `mail.activity` is a user-todo (not a durable,
  retryable, owned, prioritized job); `ir.cron` is a scheduler, not a queue.
- **Fields (minimum):** `account_id`, `group_id`, `operation`, `status`, `priority`,
  `retry_count`, `max_attempts`, `claimed_at`, `next_retry_at`, `error`, `result`.
- **States:** `PENDING`, `RUNNING`, `SUCCESS`, `FAILED`, `CANCELLED`.
- **MVP/explicitly defined (C9):** `x.account.task` + `ir.cron` + PostgreSQL skip-locked
  claim. Production path: keep `x.account.task`, hook executor into an installed/approved
  Odoo queue mechanism (e.g., OCA `queue_job`). Do not build a second queue.

### 6.4 `x.message` — external message identity mirror (NEW)
- **Why:** X external message identity (external id, direction, external timestamps,
  author/participant identity, ack/delivery, participant join/leave) cannot be represented
  safely by `mail.message` alone. `whatsapp_evaluation` established this with a dedicated
  `whatsapp.message` mirror. Provides a stable `X external message ↔ Odoo
  Discuss/mail message` mapping.
- **Fields (only those proven necessary):** `channel_id` M2O `discuss.channel`,
  `account_id` M2O `social.account`, `direction` (inbound/outbound), `external_id` Char
  (unique per channel), `body_plain` Text, `external_created_at` Datetime,
  `author_partner_id` M2O `res.partner`, `author_x_id`, `author_x_username`, `acked`,
  `delivered`, `participant_joined`, `participant_left`, `mail_message_id` M2O `mail.message`.

**Explicitly NOT created:** `x.account.event` (lifecycle events → `mail.message` +
`mail.activity`; conversation events → `x.message` + `discuss.channel` + `mail.message`).

---

## 7. Model Specifications

### 7.1 `social.account` (extend) — X account
Add only required XAction-replacement fields (C3; do NOT add inverse `group_ids`/`task_ids`
unless a UI requirement is proven):

| Field | Type | Purpose |
|---|---|---|
| `x_connection_status` | Selection | `NEW`, `AUTHENTICATING`, `ACTIVE`, `DISCONNECTED`, `INVALID`, `REAUTH_REQUIRED`, `ERROR`, `DISABLED` |
| `last_connected` | Datetime | Last successful connect |
| `last_validated` | Datetime | Last successful validation |
| `last_error` | Text | Last classified error (no secrets) |
| `x_provider` | Selection | `session_web` / `official_publish` / `omnix` (OmniX REST provider option) |
| `x_auth_method` | Selection | `session_cookie` / `oauth1` |
| `x_session_store_id` | M2O `x.session.store` | **Single canonical** secret relation (drop redundant Char ref) |
| `x_migration_status` | Selection | `pending` / `migrated` / `failed` |
| `source_account_id` | Char | XAction `Account.id` (audit) |
| `source_user_id` | Char | XAction `User.id` (audit) |
| `migration_batch_id` | Char | Batch identifier (audit) |
| `migration_timestamp` | Datetime | Migration time (audit) |

Reuse existing `twitter_user_id`, `social_account_handle`, `active`, `media_id`,
`is_media_disconnected` for X identity. `media_id` → `social_media_twitter`.

### 7.2 `social.media` (extend)
Branch `_action_add_account()` by configured auth method:
- `session_cookie` → import-session cookie wizard/controller (paste cookie string).
- `oauth1` (official publish) → existing `social_twitter` OAuth flow.
Do NOT add `media_type` value `'x'` (would duplicate `'twitter'`); reuse `social_media_twitter`
(`media_type='twitter'`).

### 7.3 `discuss.channel` (extend) — X DM / group DM
- `channel_type` `selection_add`: `'x'` (DM) and `'x_group'` (group DM), with `ondelete`.
- Fields: `x_account_id` M2O `social.account`; `x_partner_id` M2O `res.partner`;
  `x_conversation_id` Char (indexed) with uniqueness constraint for the X external
  conversation id; `last_x_mail_message_id` M2O `mail.message`.
- Message routing mirrors `whatsapp_evaluation`: override `message_post`/`_notify_thread`,
  distinguish inbound vs outbound, maintain `x.message` mirror. Do NOT treat Discuss as the
  complete external identity model (§6.4, §9).

### 7.4 `res.partner` (extend) — X contact
Add only required fields: `x_user_id`, `x_username`, `x_following`, `x_blocked` (only if the
actual application requires them). Do NOT create `x.contact` (C3).

### 7.5 `x.session.store` — see §6.1.
### 7.6 `x.account.group` — see §6.2.
### 7.7 `x.account.task` — see §6.3.
### 7.8 `x.message` — see §6.4.

---

## 8. Services

### 8.1 `XService` (façade)
Single entry point models call. Resolves the account's `XProvider` from
`x_provider`/`x_auth_method`, obtains the session from `XSessionManager`, and dispatches.
Models/tests NEVER call X HTTP directly (C5). Also hosts `XTaskService`.

### 8.2 `XProvider` (interface, minimal)
Only the operations actually required by the replacement (C: no speculative giant interface):
`validate_session`, `get_conversations`, `get_events`, `get_dms`, `send_dm`.
Additional operations are added only when a confirmed XAction capability requires them.

### 8.3 `SessionWebProvider` (isolated compatibility provider)
- Ports the required XAction web-session behavior into Python: `TwitterAuth` cookie-session
  authenticate/validate + `TwitterHttpClient` (bearer `ANRILgA...`, guest activation,
  `verify_credentials`, `ct0` CSRF, GraphQL/REST). Uses `requests` (established in repo).
- **Defined as:** "An isolated compatibility provider that reproduces the required XAction
  web-session behavior and is replaceable."
- Located behind `XService → XProvider → SessionWebProvider`. Never reachable directly from
  models.
- **Do not** let undocumented X HTTP calls spread through Odoo models.

### 8.4 `XOfficialPublishAdapter` (optional)
- Wraps `social_twitter` OAuth 1.0a for **publish/stats only**. It is NOT a
  DM/group-DM/session replacement (§3.2). Optional; separate from authentication and from
  `SessionWebProvider`.

### 8.5 `XSessionManager` (central session lifecycle service)
Operations: `create`, `save`, `load`, `restore`, `validate`, `invalidate`, `delete`,
`disconnect`, `reconnect`.
- Distinguish **persistent session state** (`x.session.store`) from **runtime session/client
  state** (in-memory registry `{account_id: XProvider}`).
- The runtime registry is NEVER the durable source of truth; on startup, restore from
  `x.session.store` and re-validate.

### 8.6 `OmniXProvider` (optional REST provider)
- Implements the `XProvider` interface: `validate_session` (GET `user/info`),
  `get_conversations` (GET DM inbox), `get_dms`, `send_dm` (POST), plus
  `like`/`comment`/`repost`/`follow`/`post_tweet` mapped to OmniX endpoints.
- Reads the account's `auth_token` from `x.session.store` (same cookie as the session
  provider) via `XSessionManager.load`; sends `Authorization: Bearer <api_key>` where the
  API key comes from `ir.config_parameter 'x_account.omnix_api_key'` (never stored in
  `x.session.store`).
- Error classification: `402` (insufficient credits) → transient, account stays `ACTIVE`
  (like rate-limit); `401` (bad API key) → `ERROR`; missing `auth_token` → `INVALID`.
- Per-account **either/or** with `SessionWebProvider`: an account's `x_provider` chooses
  which one runs its DMs/automation.

### 8.7 `XTaskService`
`enqueue`, `claim`, `then_run`, `retry`, `complete`, `fail`; per-account single-flight lock,
retry/backoff, priority ordering (§12).

---

## 9. X DMs / Group DMs

- Conversation layer: `discuss.channel` (`channel_type='x'`/`'x_group'`) +
  `discuss.channel.member` + `mail.message` (reuse).
- External identity: `x_conversation_id`, `x_account_id`, `x_partner_id` on the channel +
  the `x.message` mirror (§6.4) carrying the full X external message identity.
- Participants/contacts: `res.partner` (§7.4).
- Group-DM participants map from XAction `XGroupMember` (`conversationId`, `xUserId`,
  `username`, `displayName`, `isAdmin`, `isCurrentMember`, `firstSeenAt`, `lastSeenAt`,
  `avatarUrl`).

---

## 10. Account Lifecycle State Machine

**States:** `NEW`, `AUTHENTICATING`, `ACTIVE`, `DISCONNECTED`, `INVALID`,
`REAUTH_REQUIRED`, `ERROR`, `DISABLED`.

**Transition rules:**
- `NEW → AUTHENTICATING → ACTIVE` (on successful authenticate+validate).
- Invalid/expired session → `INVALID`.
- Challenge/login-wall/re-auth required → `REAUTH_REQUIRED`.
- Account challenge/disabled by X → `DISABLED`.
- Unrecoverable operational failure → `ERROR`.
- User/operator disconnect → `DISCONNECTED`.

**Error classification** MUST distinguish (they are NOT all session expiration):
- `network_error` — transient; account stays `ACTIVE` (retry).
- `rate_limit` — transient; account stays `ACTIVE` (backoff/retry).
- `temporary_x_failure` — transient; account stays `ACTIVE` (retry).
- `session_expiration` — `INVALID`.
- `authentication_failure` — `INVALID` / `REAUTH_REQUIRED`.
- `account_challenge` — `REAUTH_REQUIRED`.
- `account_disabled` — `DISABLED`.

A temporary network/rate-limit error MUST NOT automatically mark an account `INVALID`.

---

## 11. Multi-Account Support

Designed for 10 / 25 / 50+ accounts WITHOUT architecture redesign. Each account has an
isolated: session, runtime client, tasks, state, provider context. No account may
accidentally execute using another account's credentials (C10). Sessions do NOT require 50
Chromium processes — the ported provider is a native HTTP client (C: performance). The
`OmniXProvider` option further removes per-account web-session clients for DM ops (one
shared REST API key + per-account `auth_token`); per-account isolation (C10) still applies.

---

## 12. Task Queue Behavior

- States: `PENDING → RUNNING → SUCCESS` | `FAILED` / `CANCELLED`.
- Claiming: single `ir.cron` worker scans `PENDING` where `next_retry_at <= now` and claims
  via `SELECT ... FOR UPDATE SKIP LOCKED` on `x.account.task`, keyed by `account_id` for
  per-account single-flight (one RUNNING task per account at a time).
- Retry/backoff: on `FAILED`, increment `retry_count`; if `retry_count < max_attempts`,
  set `next_retry_at` with exponential backoff and return to `PENDING`; else stay `FAILED`.
- Isolation: task executes with its `account_id`'s session ONLY (C10). Concurrency defined
  (per-account serial, cross-account parallel).
- Timeout/stale `RUNNING`: reclaim after threshold (operational parameter).

---

## 13. Security Requirements (binding)

**Sensitive data:** `auth_token`, `ct0`, cookies, OAuth secrets, session state, API
credentials. MUST NEVER appear in: logs, `mail.message`, `mail.activity`, `_compute_display_name`,
API responses, normal UI fields.

**Encryption:** `AES-256-GCM` (or equivalent authenticated encryption); key separation from
the DB. Key `X_SESSION_ENCRYPTION_KEY` (or impl-final name) supplied via secure deployment
configuration; never stored in PostgreSQL; never logged; never returned through normal APIs.

**`x.session.store` access:** read/write only for `group_x_account_manager` +
`base.group_system` (via `ir.model.access.csv`); invisible to other groups. Credentials
masked in all outputs (like XAction's `maskCookie`).

**Account isolation:** enforced at the service layer; an ordinary Odoo user cannot retrieve
raw session credentials (must be proven by test, §17).

---

## 14. Migration Plan (XAction → Odoo)

**Source:** `xactions-postgres-1`, DB `xactions`. Non-destructive; XAction stays until
Stage 4; XAction records are never deleted during migration.

Pipeline:
1. **Discover** — read `Account` (`username`, `displayName`, `sessionCookie`, `authMethod`,
   `isActive`, `isBlocked`, `userId`, `profileUrl`) + `User` session.
2. **Convert** — parse cookie strings to canonical `auth_token`/`ct0` (+ helper cookies).
3. **Encrypt** — re-encrypt with the Odoo-managed key into `x.session.store` (fixes the
   XAction plaintext `Account.sessionCookie` defect).
4. **Map** — upsert `social.account` keyed by `twitter_user_id`/`social_account_handle`;
   `media_id` = `social_media_twitter`; set audit fields (§7.1).
5. **Validate** — `SessionWebProvider.validate_session()` per migrated account; mark `ACTIVE`
   vs `REAUTH_REQUIRED`.
6. **Group / Task / DM** — `Group`+`GroupAccount` → `x.account.group` + M2M; `GroupTask` →
   `x.account.task`; `XGroupMember` → `discuss.channel` + `res.partner` + `x.message`.
7. **Audit + recoverable failures** — `x_migration_status` (pending/migrated/failed);
   failures are recoverable and retried; no source deletion.

**Session migration (do NOT pass on `verify_credentials == 200` alone):**
```
XAction session → extract canonical cookies → encrypt with Odoo key → store in
x.session.store → restore through SessionWebProvider → validate
```

**Cutover stages:**
- **Stage 1 — Shadow:** Odoo can inspect/import but XAction remains authoritative.
- **Stage 2 — Restore:** Odoo restores and validates migrated sessions.
- **Stage 3 — Primary:** Odoo becomes the active runtime.
- **Stage 4 — Removal:** XAction removed ONLY after successful production verification.

**Rollback:** restore prior `social.account` fields; `X_SESSION_ENCRYPTION_KEY` backup kept
in env; re-point to XAction if validation fails.

**Mandatory portability acceptance test** (before scaling):
```
One real XAction account → migrate session → shutdown XAction → start Odoo → restore
session → validate → perform required read operation → perform permitted harmless test
operation → restart again → restore again → repeat operation
```
Then scale: 1 → 10 → 25 → 50.

---

## 15. Automation (Groups)

Reuse `base_automation` for group rules where applicable; do NOT build an independent rules
engine. Automation conditions/actions enqueue `x.account.task` (C: automation queues a task
rather than directly executing X HTTP from the rule). Execution of queued actions happens
through `XTaskService` + the account's provider.

---

## 16. Implementation Plan (phased, each independently verifiable)

- **PHASE 0 — Validate architecture assumptions** (COMPLETE; documented in §3).
- **PHASE 1 — `social.account` + session persistence** — module scaffold (`__manifest__`,
  `__init__`), `social.media` branch, `x.session.store` + relation + `x_connection_status`
  fields, `XSessionManager`, `ir.model.access.csv` ACLs.
- **PHASE 2 — Python session provider** — `XService`/`XProvider` interface + isolated
  `SessionWebProvider` (ported client: authenticate, validate, guest token, ct0, DMs).
- **PHASE 3 — Restart/restore/validation** — startup recovery + cron sweep
  (`_validate_sessions_cron`) + single-account portability acceptance test.
- **PHASE 4 — XAction migration** — discover→convert→encrypt→map→validate→flag; shadow→
  restore→primary→remove; rollback; audit fields.
- **PHASE 5 — Task execution** — `x.account.task` + `XTaskService` + cron worker +
  skip-locked claiming + retry/backoff; scale 10→25→50.
- **PHASE 6 — DM / Group DM** — `discuss.channel` x/x_group + `x.message` mirror +
  `res.partner` contacts + `mail.message` lifecycle events.
- **PHASE 7 — Groups / automation** — `x.account.group` + `base_automation` rules.
- **PHASE 8 — Official/OmniX integrations** — `OmniXProvider` (optional REST provider;
  per-account either/or with session); `XOfficialPublishAdapter` (optional publish/stats).

---

## 17. Testing Plan

Follow `ai_whatsapp` / `whatsapp_evaluation` conventions (`@tagged('post_install',
'-at_install')`, `MailCommon`, `@patch`, fresh DB via `./scripts/run-tests.sh x_account`).

### 17.1 Functional tests
- Session encryption/decryption roundtrip.
- Session persistence + restoration (survives simulated restart).
- Session validation.
- Invalid-session classification (network vs rate-limit vs invalid vs challenge).
- Account lifecycle state machine transitions.
- Account isolation (task on account A cannot use account B session; ordinary user cannot
  retrieve raw credentials).
- Task claiming, task concurrency (per-account single-flight), retry/backoff.
- DM mapping, group-DM mapping, X external message identity (`x.message` ↔ `mail.message`).
- Migration + migration rollback.
- Restart recovery.
- Session portability acceptance test (real read + permitted write; §14).
- **OmniX provider** (mocked `requests` to `api.omnixapi.com`): `validate_session` via
  `user/info`; DM send/get mapping; error classification (`402` transient → stays ACTIVE,
  `401` → ERROR); per-account isolation; `XService.get_provider` dispatch for
  `x_provider='omnix'`; optionality (no API key → session provider still works).

### 17.2 Security tests
Credential encryption; credential masking; ACL restrictions; unauthorized session access;
API response masking; log redaction; cross-account isolation; encryption-key separation. A
test MUST prove an ordinary Odoo user cannot retrieve raw session credentials.

### 17.3 Performance tests
Measurable targets at 1 / 10 / 25 / 50 accounts: session restore time, memory, CPU, DB
load, task throughput, validation throughput, worker contention. No assumption of 50
Chromium processes (native HTTP client).

---

## 18. Risk Register

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Session portability (cookie won't carry to Python client) | High | Medium | Mandatory portability test (§14) before scaling; real read + permitted write; restart-and-repeat |
| Undocumented X API changes (endpoints, GraphQL, bearer, guest token, challenges) | High | Medium | Isolate `SessionWebProvider` behind interface; version/signature headers; challenge→`REAUTH_REQUIRED`; replaceable provider |
| Odoo worker lifecycle (multi-worker loses in-memory session) | High | High | Durable `x.session.store` as source of truth; startup restore per worker; runtime registry never the authority |
| Multi-account concurrency (cross-account credential leakage; contention) | Medium | Medium | Per-account single-flight (skip-locked); tasks keyed by account; isolation tests |
| Credential security (plaintext leak previously in XAction) | High | High | `AES-256-GCM` + key separation; ACLs; masking; log redaction; no secrets in chatter/API; ordinary-user-cannot-read test |
| DM identity mapping (external id ↔ Discuss/mail) | Medium | Medium | `x.message` mirror + uniqueness on `x_conversation_id`/`external_id`; `whatsapp_evaluation` precedent |
| Migration failure (schema drift, cookie conversion) | Medium | Medium | Non-destructive, staged, recoverable; audit fields; rollback; XAction kept |
| 50+ account scalability | Medium | Medium | No Chromium; native HTTP provider; per-account scheduling (not full-table scan); perf tests 10/25/50; prod queue path |
| Provider changes (OAuth, OmniX future) | Medium | Low | Provider interface + auth-vs-provider separation; OmniX optional, no dependency |
| OmniX third-party dependency (pricing, API stability, availability) | Medium | Low | `OmniXProvider` behind `XProvider`; mocked tests; API key in config; `402` treated as transient; session provider remains the fallback |

---

## 19. Configuration

- `res.config.settings` / `ir.config_parameter` (reuse): auth/provider toggles,
  `X_SESSION_ENCRYPTION_KEY` reference, base URLs, encryption key indicator,
  `x_account.omnix_api_key` (OmniX API key — never stored in `x.session.store`).
- `.env` / `odoo.conf` additions: `X_SESSION_ENCRYPTION_KEY` (exported, never logged).

---

## 20. Testing / Verification Commands

```bash
# Run tests (fresh DB) per repo convention
./scripts/run-tests.sh x_account
```

---

## 21. Boundaries

**Always:** reuse existing models; isolate all X HTTP behind `XService`→`XProvider`; store
credentials encrypted with key separation + ACLs + masking; stage+non-destructively migrate;
run the portability test before scaling; retain the 4 new models; keep OmniX an **optional
provider** (no hard dependency — an account may use `SessionWebProvider` without it).

**Ask first:** adding a 5th model; adding new dependencies beyond
`['social','social_twitter','contacts','base_automation','mail']`; changing
`social.account` base semantics; changing the provider interface set;
adding DM operations beyond the confirmed set.

**Never:** store tokens/secrets in logs, chatter, display names, or API responses; call X
HTTP from a model; run cross-account sessions; delete XAction source during migration; skip
the portability test; treat network/rate-limit as session expiration; create duplicate
X account/media/contact/channel/event/config models; create dozens of Chromium processes.

---

## 22. Deliverable (this SpecKit)

This specification is the authoritative source for the next implementation phase. It is
disposable-independent of XAction: Odoo becomes the single X account / session / task /
event system.
