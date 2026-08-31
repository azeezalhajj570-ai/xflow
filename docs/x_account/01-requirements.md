# Requirements: Native Odoo X Account & Session Platform

Traceable requirements. Each maps to model / service / API / test as applicable.

**ID convention:** `FR-` functional, `NFR-` non-functional, `SEC-` security,
`MIG-` migration, `TST-` testing.

---

## Functional Requirements

| ID | Requirement | Acceptance criterion | Maps to |
|---|---|---|---|
| FR-1 | X account is represented by `social.account` extended with X fields | `social.account` has `x_connection_status`, `last_connected`, `last_validated`, `last_error`, `x_provider`, `x_auth_method`, `x_session_store_id`, `x_migration_status`, `source_account_id`, `source_user_id`, `migration_batch_id`, `migration_timestamp` | `social.account` (model), view |
| FR-2 | Reuse the existing X media record, not a duplicate | Account `media_id` points to `social_media_twitter` (`media_type='twitter'`); no new X `social.media` created | `social.media` (reuse) |
| FR-3 | Authentication method is separate from provider | Account holds `x_auth_method` (`session_cookie`/`oauth1`) independent of `x_provider` (`session_web`/`official_publish`/`omnix`) | `social.account`, `XProvider` |
| FR-4 | Session string can be imported (cookie) | Wizard/controller accepts a raw X cookie string, parses canonical `auth_token`/`ct0` | wizard `import_session`, controller |
| FR-5 | Session is persistently stored encrypted | `create()`/`save()` writes an encrypted blob to `x.session.store`; roundtrip returns identical credentials | `x.session.store`, `XSessionManager` |
| FR-6 | Session survives restart | After simulated Odoo restart, `restore()` rebuilds the runtime client and validates; still `ACTIVE` | `XSessionManager.restore`, startup recovery |
| FR-7 | Session validates against X | `validate()` calls `SessionWebProvider.validate_session()`; success → `ACTIVE`, else classified failure | `SessionWebProvider`, lifecycle |
| FR-8 | Session can be invalidated/deleted | `invalidate()`/`delete()` remove runtime + (optionally) persistent state | `XSessionManager` |
| FR-9 | Account lifecycle states exist | `x_connection_status` in `NEW/AUTHENTICATING/ACTIVE/DISCONNECTED/INVALID/REAUTH_REQUIRED/ERROR/DISABLED` | `social.account`, state machine |
| FR-10 | Transient errors do not invalidate accounts | Network/rate-limit/temporary failures keep account `ACTIVE`; only real expiration/auth/challenge/disabled change state | lifecycle transitions (§10 spec) |
| FR-11 | Tasks are durable and queueable | `x.account.task` persists with `operation`/`status`/`priority`/`retry_count`/`max_attempts`/`claimed_at`/`next_retry_at`/`error`/`result` | `x.account.task`, `XTaskService` |
| FR-12 | Tasks claim with per-account single-flight concurrency | At most one `RUNNING` task per account; cross-account parallel; skip-locked claim | `XTaskService`, cron |
| FR-13 | Tasks retry with backoff | On failure, `retry_count` increments, `next_retry_at` set with backoff until `max_attempts` | `XTaskService`, cron |
| FR-14 | DM / group-DM conversations reuse Discuss | `discuss.channel` extended with `channel_type='x'`/`'x_group'`, `x_account_id`, `x_partner_id`, `x_conversation_id` | `discuss.channel` |
| FR-15 | External message identity is preserved | `x.message` maps external id ↔ `mail.message`; direction, external timestamps, author, ack/delivery, participant join/leave tracked | `x.message`, `discuss.channel` |
| FR-16 | X contacts reuse `res.partner` | Partner has `x_user_id`/`x_username`/`x_following`/`x_blocked` (only if required) | `res.partner` |
| FR-17 | Account lifecycle events logged via mail/activity | Account status change → `mail.message`/`mail.activity` record; no `x.account.event` | `mail.message`, `mail.activity` |
| FR-18 | Account groups exist | `x.account.group` with `name`/`description`/`account_ids`/`actions`/`auto_execute`/`cooldown_sec`/`paused` | `x.account.group` |
| FR-19 | Group automation reuses `base_automation` | Automation conditions/actions enqueue `x.account.task` (not direct X HTTP) | `base_automation`, `XTaskService` |
| FR-20 | Configuration is Odoo-native | Auth/provider toggles, key reference, URLs via `res.config.settings`/`ir.config_parameter` | settings, config |
| FR-21 | OmniX provider is available as an X provider option | Account with `x_provider='omnix'` resolves to `OmniXProvider` via `XService`, reads `auth_token` from `x.session.store`, and performs DM/tweet/follow ops through the OmniX REST API (mocked) | `OmniXProvider`, `XService` |
| FR-22 | OmniX API key is configurable | `x_account.omnix_api_key` settable via `res.config.settings`/`ir.config_parameter`; never stored in `x.session.store` | settings, config |

## Non-Functional Requirements

| ID | Requirement | Acceptance criterion | Maps to |
|---|---|---|---|
| NFR-1 | Scales to 10/25/50 accounts without redesign | Perf targets at 1/10/25/50: session restore time, memory, CPU, DB load, task/validation throughput, contention | services, cron, tests |
| NFR-2 | Runtime registry is not the durable source of truth | `x.session.store` is reloaded on startup; runtime mapping rebuilt | `XSessionManager` |
| NFR-3 | No 50-Chromium assumption | Provider is a native HTTP client (no Puppeteer) | `SessionWebProvider` |
| NFR-4 | Provider replaceable | All X calls behind `XService→XProvider`; swap is interface-only | `XProvider` interface |
| NFR-5 | MVP queue has a clean production path | `x.account.task` + cron replaceable by approved Odoo queue; no second queue system | `XTaskService`, plan |
| NFR-6 | OmniX is optional, not a dependency | Module works with `x_provider='session_web'` and no OmniX key configured | provider interface, tests |

## Security Requirements

| ID | Requirement | Acceptance criterion | Maps to |
|---|---|---|---|
| SEC-1 | Credentials encrypted at rest | `AES-256-GCM`; `encrypted_blob` on `x.session.store` | `XSessionManager`, store |
| SEC-2 | Key separated from DB | `X_SESSION_ENCRYPTION_KEY` in deployment config, never in PostgreSQL/logs/APIs | config |
| SEC-3 | No secrets in logs/chatter/display/API | Grep/assert: `auth_token`, `ct0`, OAuth secrets absent from logs, `mail.message`, `mail.activity`, display names, API responses | everywhere |
| SEC-4 | ACL restrictions on `x.session.store` | Only `group_x_account_manager` + `base.group_system` can read/write | `ir.model.access.csv` |
| SEC-5 | Ordinary user cannot read raw credentials | Test: user without manager role gets AccessError on `x.session.store.encrypted_blob` | security test |
| SEC-6 | Cross-account isolation | Account A task never resolves account B session | `XTaskService`, `XSessionManager`, test |

## Migration Requirements

| ID | Requirement | Acceptance criterion | Maps to |
|---|---|---|---|
| MIG-1 | Non-destructive | XAction records never deleted during migration | `migrations/` scripts |
| MIG-2 | Staged cutover | Shadow → Restore → Primary → Remove; XAction authoritative until Stage 3 | plan |
| MIG-3 | Session migrates with encryption | Cookie → canonical → encrypt with Odoo key → `x.session.store` → restore → validate | migration pipeline |
| MIG-4 | Success requires real ops, not just `verify_credentials==200` | Portability test: read + permitted write + restart + repeat (§14 spec) | portability test |
| MIG-5 | Audit + recoverable failures | `x_migration_status`, `source_account_id`, `source_user_id`, `migration_batch_id`, `migration_timestamp`; failures recoverable | `social.account` audit fields |
| MIG-6 | Rollback | Restore prior fields; key backup in env; re-point to XAction on failure | rollback procedure |

## Testing Requirements

| ID | Requirement | Acceptance criterion | Maps to |
|---|---|---|---|
| TST-1 | Use repo test conventions | `@tagged('post_install','-at_install')`, `MailCommon`, `@patch`, `./scripts/run-tests.sh x_account` | tests |
| TST-2 | Coverage per §17.1–17.3 spec | Encryption, persistence, restore, validation, classification, lifecycle, isolation, concurrency, retry, DM/group-DM/identity, migration, rollback, restart, scaling, security, performance all tested | tests dir |
