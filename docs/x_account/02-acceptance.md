# Acceptance Criteria: Native Odoo X Account & Session Platform

Objective, measurable criteria grouped by capability. All must be demonstrable via the
repo test harness (`./scripts/run-tests.sh x_account`) unless noted as manual/operational.

---

## A1. Module install & structure
- [ ] `x_account` installs on a fresh DB without errors alongside `social`, `social_twitter`,
      `contacts`, `base_automation`, `mail`.
- [ ] No duplicate X `social.media` is created (verifies reuse of `social_media_twitter`).
- [ ] Exactly the four new models exist (`x.session.store`, `x.account.group`,
      `x.account.task`, `x.message`) and no other new models required by the module.

## A2. X account model
- [ ] `social.account` exposes `x_connection_status`, `last_connected`, `last_validated`,
      `last_error`, `x_provider`, `x_auth_method`, `x_session_store_id`, `x_migration_status`,
      `source_account_id`, `source_user_id`, `migration_batch_id`, `migration_timestamp`.
- [ ] `media_id` on an X account resolves to `social_media_twitter` (media_type `twitter`).

## A3. Session persistence
- [ ] Import a session cookie → `x.session.store` contains an encrypted blob (not plaintext).
- [ ] Decrypt roundtrip returns byte-identical credentials.
- [ ] Simulated worker/process restart → `restore()` rebuilds the runtime client → `validate()`
      → status `ACTIVE` (given a valid session).
- [ ] Deleting/invalidating the session removes runtime state and (optionally) persistent
      state.

## A4. Lifecycle & error classification
- [ ] State transitions per §10 of the spec are enforced (NEW→AUTHENTICATING→ACTIVE; →INVALID;
      →REAUTH_REQUIRED; →DISABLED; →ERROR; →DISCONNECTED).
- [ ] `network_error`, `rate_limit`, `temporary_x_failure` keep the account `ACTIVE` (retry)
      and do NOT set `INVALID`/`REAUTH_REQUIRED`.
- [ ] True expiration/auth failure → `INVALID`; challenge → `REAUTH_REQUIRED`; disabled →
      `DISABLED`.

## A5. Task queue
- [ ] `x.account.task` persists `account_id`, `group_id`, `operation`, `status`, `priority`,
      `retry_count`, `max_attempts`, `claimed_at`, `next_retry_at`, `error`, `result`.
- [ ] States `PENDING/RUNNING/SUCCESS/FAILED/CANCELLED` enforce the workflow.
- [ ] At most one `RUNNING` task per account at a time (per-account single-flight); different
      accounts run in parallel.
- [ ] Failed task retries with exponential backoff up to `max_attempts`, then stays `FAILED`.
- [ ] Stale `RUNNING` tasks are reclaimed after the operational timeout.

## A6. DM / group-DM / contacts
- [ ] `discuss.channel` supports `channel_type='x'` and `'x_group'` with `x_account_id`,
      `x_partner_id`, `x_conversation_id` (unique per external conversation).
- [ ] `x.message` maps X external id ↔ `mail.message` with `direction`, `external_id`,
      `external_created_at`, `author_*`, `acked`, `delivered`, `participant_joined/left`.
- [ ] X contacts resolve to `res.partner` with `x_user_id`/`x_username`.

## A7. Account groups & automation
- [ ] `x.account.group` holds named accounts + `actions`/`auto_execute`/`cooldown_sec`/`paused`.
- [ ] `base_automation` rules enqueue `x.account.task` (and do not execute X HTTP directly).

## A8. Security
- [ ] No credentials (`auth_token`, `ct0`, OAuth secrets) in logs, `mail.message`,
      `mail.activity`, display names, or API responses (asserted by test/scan).
- [ ] An ordinary, non-manager user receives `AccessError` when attempting to read
      `x.session.store.encrypted_blob` (must be proven by test).
- [ ] Cross-account: a task on account A cannot resolve/use account B's session.

## A9. Migration
- [ ] Migration runs non-destructively (XAction DB untouched).
- [ ] Migrated account carries audit fields and `x_migration_status`.
- [ ] Session migration encrypts with the Odoo key and validates via `SessionWebProvider`.
- [ ] Rollback restores prior state and re-points to XAction on failure.
- [ ] **Portability acceptance test passes:** migrate one real account → shutdown XAction →
      start Odoo → restore → validate → perform a required read op → perform a permitted
      harmless write op → restart → restore → repeat. (Manual/operational; proves more than
      `verify_credentials==200`.)

## A10. Scaling
- [ ] Performance measured at 1/10/25/50 accounts (session restore time, memory, CPU, DB load,
      task throughput, validation throughput, contention) with no Chromium processes required.

## A11. Configuration
- [ ] Auth/provider toggles, base URLs, and encryption-key reference are set through
      `res.config.settings`/`ir.config_parameter`.
- [ ] `X_SESSION_ENCRYPTION_KEY` is supplied via deployment config, never stored/logged/returned.

## A12. OmniX provider option
- [ ] An account with `x_provider='omnix'` dispatches through `XService` to `OmniXProvider` (test).
- [ ] `OmniXProvider.validate_session` calls `user/info` with `Authorization: Bearer` API key +
      `auth_token` from `x.session.store` (mocked).
- [ ] DM/tweet/follow operations map to OmniX endpoints (mocked).
- [ ] `402` (insufficient credits) → transient error, account stays `ACTIVE` (not `INVALID`).
- [ ] `401` (bad API key) → account `ERROR`.
- [ ] OmniX is optional: no API key configured → accounts using `session_web` still work.
