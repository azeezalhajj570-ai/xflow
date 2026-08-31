# Task Breakdown: social_reddit

Task list in order of implementation. Each task is verifiable independently.

---

- [ ] **T1: Module scaffold and data records** (Phase 1)
  - Acceptance: Module installs without errors; Reddit appears in social.media list
  - Verify: `odoo -d test_db -i social_reddit --stop-after-init`
  - Files: `__manifest__.py`, `__init__.py`, `data/social_media_data.xml`, `security/ir.model.access.csv`

- [ ] **T2: RedditClient service layer** (Phase 2)
  - Acceptance: `RedditClient` class handles auth, requests, refresh, retry
  - Verify: Unit tests pass with mocked HTTP
  - Files: `services/__init__.py`, `services/reddit_client.py`, `tests/`

- [ ] **T3: Social media model + OAuth2 flow** (Phase 3)
  - Acceptance: OAuth redirect URL is generated; callback creates accounts
  - Verify: Manual OAuth flow or mocked callback test
  - Files: `models/social_media.py`, `controllers/main.py`, `models/__init__.py`

- [ ] **T4: Social account model with token management** (Phase 4)
  - Acceptance: Accounts store user info, tokens, auto-refresh on expiry
  - Verify: `_compute_statistics` fetches karma; `_refresh_reddit_token` works
  - Files: `models/social_account.py`, `models/res_config_settings.py`

- [ ] **T5: Post template fields for Reddit-specific content** (Phase 4)
  - Acceptance: Post form has Reddit title, subreddit selection, flair, message fields
  - Verify: Fields appear in form view; compute methods work
  - Files: `models/social_post_template.py`, `models/social_post.py`

- [ ] **T6: Publishing (post and live post)** (Phase 5)
  - Acceptance: Text, link, and image posts are published via Reddit API
  - Verify: `_post()` creates post on Reddit (mocked); `_compute_live_post_link` works
  - Files: `models/social_live_post.py`

- [ ] **T7: Stream fetching** (Phase 6)
  - Acceptance: User posts and subreddit posts appear in Stream Feed
  - Verify: `_fetch_stream_data` creates stream posts from mocked data
  - Files: `models/social_stream.py`, `models/social_stream_post.py`

- [ ] **T8: Statistics refresh** (Phase 7)
  - Acceptance: Account stats (karma) and post stats (score, comments) are refreshed
  - Verify: `_refresh_statistics` updates engagement on live posts
  - Files: `models/social_account.py`, `models/social_live_post.py`

- [ ] **T9: Views and settings UI** (Phase 8)
  - Acceptance: Settings form has Reddit credentials; post form has Reddit fields
  - Verify: Views load without errors
  - Files: All view XML files

- [ ] **T10: Tests** (Phase 8)
  - Acceptance: All critical paths are tested with mocks; tests pass
  - Verify: `--test-tags /social_reddit` passes
  - Files: `tests/test_social_reddit.py`

- [ ] **T11: Final integration verification**
  - Acceptance: Module is installable alongside other social providers
  - Verify: Full test suite + manual verification
  - Files: (all files reviewed)

## Task Dependencies

```
T1 ─→ T2 ─→ T3 ─→ T4 ─→ T5 ─→ T6 ─→ T7 ─→ T8 ─→ T9 ─→ T10 ─→ T11
            │      │      │
            │      └──────┴────┐
            │                  │
            ▼                  ▼
           T3                 T9
```

T1–T4 are sequential (each builds on the previous).
T5 can be done in parallel with T4 (both are model-only).
T6 depends on T4+T5.
T7 depends on T4.
T8 depends on T6.
T9 can start after T3.
T10 depends on everything except T9.
T11 is final integration.
