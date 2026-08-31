# Task Breakdown: x_account

Task list in order of implementation. Each task is verifiable independently.
Follows the `tasks/todo.md` convention. Do NOT implement until the SpecKit is approved.

---

- [ ] **T0: SpecKit approval** (Phase 0)
  - Acceptance: `docs/x_account/*.md`, `tasks/plan-x_account.md`, `tasks/todo-x_account.md`
    reviewed and approved; no implementation yet.
  - Files: (spec docs)

- [ ] **T1: Module scaffold + `x.session.store` + account fields** (Phase 1)
  - Acceptance: `x_account` installs on fresh DB; `social.account` has X fields;
    `x.session.store` created with ACLs; encrypted roundtrip works.
  - Verify: `./scripts/run-tests.sh x_account` (session test); ACL test.
  - Files: `__manifest__.py`, `__init__.py`, `models/{__init__,social_account,session_store}.py`,
    `security/ir.model.access.csv`, `views/social_account_views.xml`

- [ ] **T2: `XSessionManager` + encryption** (Phase 1)
  - Acceptance: create/save/load/restore/validate/invalidate/delete/disconnect/reconnect;
    `AES-256-GCM` with `X_SESSION_ENCRYPTION_KEY`; runtime registry distinct from durable store.
  - Verify: encryption/persistence tests.
  - Files: `services/__init__.py`, `services/session_manager.py`

- [ ] **T3: `XProvider` interface + `XService` façade** (Phase 2)
  - Acceptance: `XProvider` exposes only `validate_session`, `get_conversations`,
    `get_events`, `get_dms`, `send_dm`; `XService` dispatches by auth/provider; no X HTTP in models.
  - Verify: unit tests (mocked).
  - Files: `services/x_provider.py`, `services/x_service.py`

- [ ] **T4: `SessionWebProvider` (ported client)** (Phase 2)
  - Acceptance: cookie authenticate + validate; guest token; ct0 CSRF; `verify_credentials`;
    DM ops; isolated behind `XService`.
  - Verify: mocked HTTP tests.
  - Files: `services/providers/{__init__,session_web}.py`

- [ ] **T5: Startup recovery + validation cron** (Phase 3)
  - Acceptance: sessions restored from `x.session.store` per worker on start; `_validate_sessions_cron`
    sweeps per-account (not full-table); one-account failure doesn't block others.
  - Verify: restart-recovery test; classification test.
  - Files: `data/cron.xml`, lifecycle methods, startup hook

- [ ] **T6: Single-account portability test** (Phase 3)
  - Acceptance: one real XAction account → migrate → shutdown XAction → start Odoo → restore →
    validate → real read op → permitted harmless write op → restart → restore → repeat.
  - Verify: `tests/test_portability.py` (manual/operational).
  - Files: `tests/test_portability.py`

- [ ] **T7: XAction migration scripts (non-destructive)** (Phase 4)
  - Acceptance: discover→convert→encrypt→map→validate→flag; audit fields set; rollback works;
    XAction DB untouched.
  - Verify: `tests/test_migration.py` (mocked source rows) + rollback.
  - Files: `migrations/19.0.1.0.1/`, `tests/test_migration.py`

- [ ] **T8: Cutover procedure + audit** (Phase 4)
  - Acceptance: Stage 1 shadow → 2 restore → 3 primary → 4 remove documented; failure →
    rollback to XAction; `x_migration_status`/`source_*`/`migration_batch_id`/
    `migration_timestamp` retained in MySQL audit.
  - Verify: migration rollback test; operational review.
  - Files: `migrations/`, docs

- [ ] **T9: `x.account.task` + `XTaskService` + worker cron** (Phase 5)
  - Acceptance: states PENDING/RUNNING/SUCCESS/FAILED/CANCELLED; per-account single-flight;
    skip-locked claim; retry/backoff up to `max_attempts`; stale-RUNNING reclaim.
  - Verify: task queue, concurrency, retry/backoff tests; scale 10/25/50.
  - Files: `models/account_task.py`, `services/task_service.py`, `data/cron.xml`,
    `views/account_task_views.xml`, `tests/test_task_queue.py`

- [ ] **T10: DM / Group-DM / contacts** (Phase 6)
  - Acceptance: `discuss.channel` `channel_type='x'`/`'x_group'` + identity fields; `x.message`
    mirror; `res.partner` X fields; inbound/outbound routing (whatsapp pattern).
  - Verify: DM mapping, group-DM mapping, external identity tests.
  - Files: `models/{discuss_channel,x_message,res_partner}.py`

- [ ] **T11: Account lifecycle events via mail/activity** (Phase 6)
  - Acceptance: status changes logged as `mail.message`/`mail.activity`; no `x.account.event`.
  - Verify: lifecycle test.
  - Files: `models/social_account.py`, `models/x_message.py`

- [ ] **T12: `x.account.group` + `base_automation`** (Phase 7)
  - Acceptance: groups hold accounts + actions/auto_execute/cooldown/paused; automation enqueues
    `x.account.task` (no direct X HTTP from rules).
  - Verify: group/automation tests.
  - Files: `models/account_group.py`, `views/account_group_views.xml`, `base_automation` data

- [ ] **T13: Views + settings UI** (Phase 8)
  - Acceptance: account form has X fields; task and group forms render; settings have
    auth/provider toggles + key reference + URLs.
  - Verify: views load without errors.
  - Files: all view XML + `res_config_settings_views.xml`

- [ ] **T14: Security test suite** (Phase 8)
  - Acceptance: encryption; masking; ACL (ordinary user cannot read raw credentials); API
    response masking; log redaction; cross-account isolation; key separation.
  - Verify: `tests/test_security.py`.
  - Files: `tests/test_security.py`

- [ ] **T15: Performance tests 1/10/25/50** (Phase 8)
  - Acceptance: measured session-restore/memory/CPU/DB/task-throughput/validation-throughput/
    contention; no Chromium.
  - Verify: perf harness.
  - Files: `tests/` perf tests

- [x] **T16: Optional `XOfficialPublishAdapter` + OmniX provider option** (Phase 8)
  - Acceptance: official publish adapter (publish-only, separate from session) optional; OmniX
    available as a provider option (per-account either/or with session); no hard dependency.
  - Verify: `./scripts/run-tests.sh x_account` (test_official_publish, test_omnix).
  - Files: `services/providers/official_publish.py`, `services/providers/omnix.py`,
    `services/x_provider.py` (extension point), `tests/test_official_publish.py`,
    `tests/test_omnix.py`

- [ ] **T17: Final integration verification**
  - Acceptance: full suite passes on fresh DB; module installs alongside other providers;
    cutover to Stage 3 verified; no credentials leak.
  - Verify: `./scripts/run-tests.sh x_account` + manual.
  - Files: (all reviewed)

- [x] **T18: OmniX provider implementation** (Phase 8)
  - Acceptance: `x_provider='omnix'` → `OmniXProvider` via `XService`; validate via
    `user/info`; DM/tweet/follow ops; `402` → transient (stays ACTIVE), `401` → ERROR;
    `x_account.omnix_api_key` configurable; optional (session_web works without it).
  - Verify: `./scripts/run-tests.sh x_account` (test_omnix, mocked) — 73 tests green.
  - Files: `services/providers/omnix.py`, `models/social_account.py` (selection),
    `models/res_config_settings.py` (+ view), `tests/test_omnix.py`

## Task Dependencies

```
T0 ─→ T1 ─→ T2 ─→ T3 ─→ T4 ─→ T5 ─→ T6 ─→ T7 ─→ T8
            │             │      │            │
            │             └──────┴────┐       │
            │                        │       │
            ▼                        ▼       ▼
          T9 ──────────────────▶  T10 ─→ T11 ─→ T12
            │                              │
            ▼                              ▼
          T13 ◀────────────────────────  T14 ─→ T15 ─→ T16 ─→ T17 ─→ T18
```

- T1–T8 sequential (each builds on the previous); T6/T7/T8 (portability + migration) cluster.
- T9 depends on T3+T5 (provider + restore).
- T10 depends on T3 (provider for DM ops).
- T11 depends on T10.
- T12 depends on T9 (enqueues tasks).
- T13 can start after T1; UI for later models after their models exist.
- T14 depends on T1+T10 (session + DM); T15 depends on T9.
- T16 depends on T2+T3 (provider + auth separation).
- T17 is final integration.
- T18 (OmniX provider) depends on T16 (registry/extension point); may run after T17.
