# Implementation Plan: GetXAPI Odoo Integration Module

**Module:** `x_account_getxapi`
**Status:** PLAN
**Date:** 2026-09-07

---

## 1. Objective

Build `x_account_getxapi` — an optional X provider module that integrates with the
GetXAPI REST API (`https://api.getxapi.com`). Follows the exact same architectural
pattern as `x_account_omnix`: register as a provider with `XProviderRegistry`, implement
the `XProvider` contract, layer transport → parsing → error → composition, and remain
a per-account either/or alternative to SessionWebProvider and other providers.

Key differentiators from OmniX:
- **Pay-per-call pricing** → centralized cost table, `getxapi.api.usage` tracking model,
  call minimization, caching
- **Cursor pagination** → centralized `paginate()` helper on the client
- **Bounded retries** → different behavior for reads (retry safe) vs writes (do NOT blindly
  retry mutations)
- **No webhooks in v1** → data API only

---

## 2. Module Structure

```text
addons/custom/x_account_getxapi/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── getxapi_usage.py          # getxapi.api.usage — cost tracking model
│   ├── res_config_settings.py    # GetXAPI key + provider selection
│   ├── social_account.py         # extend social.account with getxapi fields
│   └── social_media.py           # branch add-account flow for getxapi
├── services/
│   ├── __init__.py
│   ├── getxapi_client.py         # Core HTTP client + pagination + retry + cost
│   ├── getxapi_cost.py           # Centralized ENDPOINT_COSTS table
│   ├── getxapi_envelope.py       # Response normalization → DTOs
│   ├── getxapi_errors.py         # HTTP status → classified error taxonomy
│   ├── getxapi_provider.py       # Composition root — XProvider contract
│   ├── getxapi_tweet_service.py  # Tweet reads + writes
│   ├── getxapi_user_service.py   # User operations
│   ├── getxapi_dm_service.py     # DM send/list
│   └── getxapi_media_service.py  # Media upload
├── security/
│   ├── getxapi_security.xml
│   └── ir.model.access.csv
├── views/
│   ├── res_config_settings_views.xml
│   ├── getxapi_usage_views.xml
│   └── social_account_views.xml
├── data/
│   └── cron.xml
└── tests/
    ├── __init__.py
    ├── common.py
    ├── test_client.py
    ├── test_cost.py
    ├── test_envelope.py
    ├── test_provider.py
    ├── test_tweet_service.py
    ├── test_user_service.py
    ├── test_dm_service.py
    └── test_errors.py
```

---

## 3. Implementation Order (5 Phases)

### Phase 1: Foundation (files: 10)
1. `__manifest__.py`, root `__init__.py`
2. `services/__init__.py`, `models/__init__.py`
3. `services/getxapi_cost.py` — pricing table
4. `services/getxapi_errors.py` — error taxonomy
5. `services/getxapi_envelope.py` — response parser
6. `services/getxapi_client.py` — HTTP client + pagination + retry + cost tracking
7. `models/getxapi_usage.py` — usage tracking model
8. `security/getxapi_security.xml`, `security/ir.model.access.csv`

### Phase 2: Provider + Account Integration (files: 6)
9. `services/getxapi_provider.py` — composition root
10. `models/social_account.py` — extend x_provider selection
11. `models/social_media.py` — branch add-account flow
12. `models/res_config_settings.py` — API key config
13. `views/res_config_settings_views.xml`
14. `views/social_account_views.xml`

### Phase 3: Services (files: 4)
15. `services/getxapi_tweet_service.py`
16. `services/getxapi_user_service.py`
17. `services/getxapi_dm_service.py`
18. `services/getxapi_media_service.py`

### Phase 4: Usage UI (files: 1)
19. `views/getxapi_usage_views.xml`
20. `data/cron.xml`

### Phase 5: Tests (files: 9)
21. `tests/__init__.py`, `tests/common.py`
22. `tests/test_client.py`
23. `tests/test_cost.py`
24. `tests/test_envelope.py`
25. `tests/test_errors.py`
26. `tests/test_provider.py`
27. `tests/test_tweet_service.py`
28. `tests/test_user_service.py`
29. `tests/test_dm_service.py`

---

## 4. Key Design Decisions

### 4.1 Follow OmniX Pattern Exactly
- Self-registration via `XProviderRegistry.register()` at import time
- `_needs_cookies = False` (GetXAPI uses API key, not session cookies)
- `__init__.py` imports: `models`, `services`
- Tests extend `XAccountTestBase` from `x_account.tests.common`

### 4.2 Cost Tracking as First-Class Feature
- Centralized `ENDPOINT_COSTS` dict (single source of truth)
- Every `client.request()` call logs to `getxapi.api.usage`
- Admin UI for monitoring spend

### 4.3 Pagination Is Client-Level
- `GetXAPIClient.paginate()` handles cursor pagination generically
- Services call `client.paginate(path, params)` instead of implementing their own loop
- Guards: max_pages=50, max_items=1000, repeated cursor detection

### 4.4 Write Retry Safety
- On timeout: do NOT retry (state unknown)
- On 502/503/504: retry up to 2 times (server explicitly rejected)
- On 429: classify as rate_limit, let caller decide
- For idempotent writes (like, retweet): retry is safer
- For non-idempotent writes (create tweet): skip retry on timeout

### 4.5 Call Minimization
- `validate_session()` checks if user info is already cached locally
- Provider methods accept pre-fetched data where possible
- No automatic "fetch everything" on webhook receipt
- Cache-first pattern documented in provider docstrings

### 4.6 No Webhooks in v1
GetXAPI webhook contracts are not yet documented. Added in follow-up.

---

## 5. Detailed File Specifications

### 5.1 `__manifest__.py`
```python
{
    'name': 'X Account GetXAPI Provider',
    'category': 'Marketing/Social Marketing',
    'summary': 'Optional GetXAPI REST provider for X Account & Session Platform',
    'version': '19.0.1.0.0',
    'depends': ['x_account', 'mail'],
    'data': [
        'security/getxapi_security.xml',
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/getxapi_usage_views.xml',
        'views/social_account_views.xml',
        'data/cron.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'OEEL-1',
}
```

### 5.2 `services/getxapi_cost.py`
Centralized pricing table:
```python
ENDPOINT_COSTS = {
    "twitter/tweet/advanced_search": 0.001,
    "twitter/tweet/detail": 0.001,
    "twitter/tweet/thread": 0.005,
    "twitter/tweet/replies": 0.001,
    "twitter/tweet/retweeters": 0.001,
    "twitter/tweet/create": 0.002,
    "twitter/tweet/edit": 0.002,
    "twitter/tweet/favorite": 0.001,
    "twitter/tweet/retweet": 0.001,
    "twitter/user/search": 0.001,
    "twitter/user/status": 0.001,
    "twitter/user/info": 0.001,
    "twitter/user/info_by_id": 0.001,
    "twitter/user/user_about": 0.001,
    "twitter/user/tweets": 0.001,
    "twitter/user/tweets_and_replies": 0.001,
    "twitter/user/tweets/complete": 0.003,
    "twitter/user/media": 0.001,
    "twitter/user/likes": 0.001,
    "twitter/user/followers": 0.001,
    "twitter/user/followers_v2": 0.001,
    "twitter/user/following": 0.001,
    "twitter/user/following_v2": 0.001,
    "twitter/user/verified_followers": 0.001,
    "twitter/user/followers_you_know": 0.001,
    "twitter/user/check_follow_relationship": 0.001,
    "twitter/user/follow": 0.001,
    "twitter/user/unfollow": 0.001,
    "twitter/user/home_timeline": 0.001,
    "twitter/user/bookmark_search": 0.001,
    "twitter/user/affiliates": 0.001,
    "twitter/dm/send": 0.002,
    "twitter/dm/list": 0.002,
    "twitter/media/upload": 0.001,
    "twitter/article/get": 0.001,
    "twitter/article/create": 0.010,
    "twitter/article/update": 0.005,
    "twitter/article/list": 0.005,
    "twitter/article/publish": 0.005,
    "twitter/article/unpublish": 0.005,
    "twitter/article/delete": 0.005,
}

def estimate_cost(path):
    for prefix, cost in ENDPOINT_COSTS.items():
        if prefix in path:
            return cost
    return 0.0
```

### 5.3 `services/getxapi_client.py`
Core HTTP client with:
- `BASE_URL = "https://api.getxapi.com"`
- Bearer auth header from API key
- GET/POST with configurable timeout (20s)
- JSON response parsing
- Bounded retry (2 retries) for network errors, timeouts, 502/503/504
- Write retry safety (no retry on timeout for writes)
- Automatic cost logging to `getxapi.api.usage`
- `paginate(path, params, max_pages=50, max_items=1000)` for cursor pagination
- Never logs API keys

### 5.4 `services/getxapi_errors.py`
```python
class GetXAPIError(Exception):
    def __init__(self, status_code, endpoint, message=''):
        self.status_code = status_code
        self.endpoint = endpoint
        self.message = message or str(status_code)
        self.retryable = status_code in (429, 500, 502, 503, 504) or status_code >= 500
        super().__init__(self.message)
```

HTTP status → error code mapping:
- 400 → bad_request
- 401 → authentication_failure
- 404 → not_found
- 429 → rate_limit (retryable)
- 500 → temporary_error (retryable)
- 502 → upstream_rejection (retryable for reads)
- 503 → temporary_error (retryable)
- 504 → timeout (retryable)

### 5.5 `services/getxapi_envelope.py`
Stateless parser methods: `user()`, `tweet()`, `search_results()`, `dm_conversations()`, `dm_messages()`, `followers()`, `follow_result()`, `retweet_result()`, `like_result()`, `media_upload_result()`

### 5.6 `services/getxapi_provider.py`
Composition root implementing `XProvider` contract:
- `validate_session()` → `GET /twitter/user/info`
- `like(post)` → `_tweets.like()`
- `comment(post, text)` → `_tweets.create()`
- `repost(post)` → `_tweets.retweet()`
- `follow(screen_name)` → `_users.follow()`
- `post_tweet(text)` → `_tweets.create()`
- `get_dms(conversation_id)` → `_dms.list()`
- `send_dm(recipient_id, text)` → `_dms.send()`
- `fetch_groups(account)` → group sync
- `fetch_group_messages(account)` → group message sync
- Self-registers at import time with `XProviderRegistry`

### 5.7 `services/getxapi_tweet_service.py`
- `search(query)` → `GET /twitter/tweet/advanced_search` ($0.001)
- `detail(tweet_id)` → `GET /twitter/tweet/detail` ($0.001)
- `thread(tweet_id)` → `GET /twitter/tweet/thread` ($0.005)
- `replies(tweet_id)` → `GET /twitter/tweet/replies` ($0.001)
- `retweeters(tweet_id)` → `GET /twitter/tweet/retweeters` ($0.001)
- `create(text)` → `POST /twitter/tweet/create` ($0.002)
- `edit(tweet_id, text)` → `POST /twitter/tweet/edit` ($0.002)
- `like(tweet_id)` → `POST /twitter/tweet/favorite` ($0.001)
- `retweet(tweet_id)` → `POST /twitter/tweet/retweet` ($0.001)

### 5.8 `services/getxapi_user_service.py`
- `info(username)` → `GET /twitter/user/info` ($0.001)
- `info_by_id(user_id)` → `GET /twitter/user/info_by_id` ($0.001)
- `search(query)` → `GET /twitter/user/search` ($0.001)
- `tweets(username)` → `GET /twitter/user/tweets` ($0.001)
- `followers(username)` → `GET /twitter/user/followers` ($0.001)
- `following(username)` → `GET /twitter/user/following` ($0.001)
- `follow(username)` → `POST /twitter/user/follow` ($0.001)
- `unfollow(username)` → `POST /twitter/user/unfollow` ($0.001)

### 5.9 `services/getxapi_dm_service.py`
- `send(recipient_id, text)` → `POST /twitter/dm/send` ($0.002)
- `list(conversation_id)` → `POST /twitter/dm/list` ($0.002)

### 5.10 `services/getxapi_media_service.py`
- `upload(file_data, media_type)` → `POST /twitter/media/upload` ($0.001)

### 5.11 `models/getxapi_usage.py`
New model `getxapi.api.usage`:
- `account_id` (Many2one → social.account, indexed)
- `endpoint` (Char, indexed)
- `method` (Selection: GET/POST)
- `timestamp` (Datetime)
- `status_code` (Integer)
- `success` (Boolean, indexed)
- `estimated_cost` (Float, digits=(10,4))
- `request_duration` (Integer, ms)
- `error_type` (Char)

### 5.12 `models/res_config_settings.py`
Extends `res.config.settings`:
- `x_getxapi_api_key` (Char, config_parameter='x_account.getxapi_api_key')
- Extends `x_provider` selection with `('getxapi', 'GetXAPI REST API')`

### 5.13 `models/social_account.py`
Extends `social.account`:
- `x_provider` selection_add: `('getxapi', 'GetXAPI REST API')` with `ondelete={'getxapi': 'cascade'}`

### 5.14 `models/social_media.py`
Extends `social.media`: branches `_action_add_account()` when provider is `getxapi`

### 5.15 Views
- `res_config_settings_views.xml`: inherit x_account settings, add API key field
- `getxapi_usage_views.xml`: list/form/search views + action + menu
- `social_account_views.xml`: inherit account form

### 5.16 Security
- `getxapi_security.xml`: no new groups (reuse `group_x_account_manager`)
- `ir.model.access.csv`: read for users, full CRUD for system

### 5.17 Cron
- `data/cron.xml`: archive old usage records (keep 90 days)

---

## 6. Testing Strategy

All tests follow existing conventions:
- `@tagged('post_install', '-at_install', 'x_account_getxapi')`
- Extend `XAccountTestBase` from `x_account.tests.common`
- Mock `requests.request` to prevent real HTTP

### Test Coverage

| File | Class | Tests |
|------|-------|-------|
| `test_client.py` | `TestGetXAPIClient` | GET/POST, auth header, timeout, retry on 5xx, retry on network error, no retry on 4xx, cost logging, pagination (first page, multi-page, has_more=false, repeated cursor, max_pages) |
| `test_cost.py` | `TestGetXAPICost` | Every endpoint cost matches spec, unknown endpoint returns 0 |
| `test_envelope.py` | `TestGetXAPIEnvelope` | Parse user, tweet, search, DMs, followers, retweet/like results |
| `test_errors.py` | `TestGetXAPIErrors` | 400/401/404/429/502/503/504 classification, retryable flag |
| `test_provider.py` | `TestGetXAPIProvider` | Registry resolution, validate_session, like/comment/repost/follow dispatch |
| `test_tweet_service.py` | `TestGetXAPITweetService` | Search, detail, thread, replies, create, edit, like, retweet |
| `test_user_service.py` | `TestGetXAPIUserService` | Info, search, tweets, followers, following, follow, unfollow |
| `test_dm_service.py` | `TestGetXAPIDMService` | Send DM, list DMs |

### Cost Verification Tests
```python
def test_retweet_cost_is_001(self):
    self.assertAlmostEqual(estimate_cost('twitter/tweet/retweet'), 0.001)

def test_like_cost_is_001(self):
    self.assertAlmostEqual(estimate_cost('twitter/tweet/favorite'), 0.001)

def test_create_tweet_cost_is_002(self):
    self.assertAlmostEqual(estimate_cost('twitter/tweet/create'), 0.002)

def test_dm_cost_is_002(self):
    self.assertAlmostEqual(estimate_cost('twitter/dm/send'), 0.002)
    self.assertAlmostEqual(estimate_cost('twitter/dm/list'), 0.002)

def test_thread_cost_is_005(self):
    self.assertAlmostEqual(estimate_cost('twitter/tweet/thread'), 0.005)

def test_tweets_complete_cost_is_003(self):
    self.assertAlmostEqual(estimate_cost('twitter/user/tweets/complete'), 0.003)
```

---

## 7. Boundaries — What This Module Does NOT Do

- Does NOT create a new X account model (extends `social.account`)
- Does NOT duplicate encryption, session persistence, or webhook handling
- Does NOT implement webhooks (v1 is data-API only)
- Does NOT implement articles (core subset; follow-up)
- Does NOT implement all 70+ user endpoints (core subset; follow-up)
- Does NOT replace existing providers (SessionWeb, Twitter, OmniX remain)
- Does NOT modify `x_account` core (only extends via inheritance)

---

## 8. Acceptance Criteria

- [ ] Module installs cleanly with `x_account` dependency
- [ ] `getxapi` appears as a provider option in account settings
- [ ] GetXAPI API key is configurable in settings (password-masked)
- [ ] `GetXAPIProvider` registers with `XProviderRegistry` at import
- [ ] `validate_session()` works with valid/invalid/missing API key
- [ ] Tweet reads work: search, detail, thread, replies, retweeters
- [ ] Tweet writes work: create, edit, like, retweet
- [ ] Retweet costs $0.001/call (verified by test)
- [ ] User operations work: info, search, tweets, followers, following
- [ ] Follow/unfollow works
- [ ] DM send/list works
- [ ] Media upload works
- [ ] Cursor pagination is centralized in `GetXAPIClient.paginate()`
- [ ] API errors are normalized to `GetXAPIError`
- [ ] Retry: bounded (2 retries), reads retry on 5xx, writes skip retry on timeout
- [ ] API usage/cost is tracked in `getxapi.api.usage`
- [ ] Usage tracking has admin UI (list + form views)
- [ ] Credentials never appear in logs
- [ ] All tests pass (`./scripts/run-tests.sh x_account_getxapi`)
- [ ] Existing X provider behavior unaffected
