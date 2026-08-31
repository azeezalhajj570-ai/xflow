# Implementation Plan: social_reddit

## Overview

Build the Reddit provider in dependency order. Each phase builds on the previous.
The implementation is divided into **8 phases** that can be verified independently.

## Phase Dependency Graph

```
Phase 1: Module Scaffold + Data
  └── No dependencies
  ↓
Phase 2: Reddit Client (API Communication Layer)
  └── Depends on Phase 1
  ↓
Phase 3: Social Media Model + OAuth
  └── Depends on Phase 2
  ↓
Phase 4: Account Model + Synchronization
  └── Depends on Phase 3
  ↓
Phase 5: Publishing (Posts + Live Posts)
  └── Depends on Phase 4
  ↓
Phase 6: Streams + Feed
  └── Depends on Phase 4
  ↓
Phase 7: Statistics + Background Jobs
  └── Depends on Phases 5, 6
  ↓
Phase 8: Views, Settings, UI, Tests
  └── Depends on all previous phases
```

## Phase Details

### Phase 1: Module Scaffold + Data Records
- Create `__manifest__.py` with module metadata
- Create `__init__.py` files for module, models, controllers, services
- Create `data/social_media_data.xml` with:
  - `social.media` record for Reddit
  - `social.stream.type` records ("My Posts", "Subreddit Hot", "Subreddit New")
- Create `security/ir.model.access.csv` following base social patterns
- Create `static/src/img/reddit.svg` — Reddit logo icon

### Phase 2: Reddit Client (`services/reddit_client.py`)
- Singleton-style API client class `RedditClient`
- Methods:
  - `__init__(self, access_token, refresh_token, client_id, client_secret)`
  - `_request(method, endpoint, **kwargs)` — base request with auth, headers, retry
  - `_ensure_token()` — auto-refresh if token expired
  - `refresh_token()` — exchange refresh token for new access token
  - `get_me()` — fetch authenticated user info
  - `get_karma()` — fetch karma breakdown
  - `get_subscribed_subreddits()` — list subscribed subreddits
  - `search_subreddits(query)` — search subreddits
  - `get_subreddit_info(subreddit)` — get subreddit details
  - `submit_post(kind, sr, title, **kwargs)` — submit text/link/image post
  - `upload_media(filepath, mimetype, data)` — upload image asset
  - `get_user_posts(username, limit=100)` — get user's posts
  - `get_subreddit_posts(subreddit, listing='hot', limit=100)` — get subreddit posts
  - `get_post_info(post_fullname)` — get single post details
  - `get_post_info_batch(post_fullnames)` — batch post info via `/api/info`
  - `delete_post(post_fullname)` — delete a post
  - `edit_post(post_fullname, text)` — edit a post
  - `comment(parent_fullname, text)` — add comment
  - `delete_comment(comment_fullname)` — delete comment
- Handle 401 → auto refresh → retry
- Handle 429 → exponential backoff
- Set `User-Agent` header on all requests

### Phase 3: Social Media + OAuth
- `models/social_media.py`:
  - Override `media_type` selection_add
  - Set class constants `_REDDIT_API_ENDPOINT`, `_REDDIT_AUTH_ENDPOINT`, `_REDDIT_OAUTH_SCOPE`
  - Override `_action_add_account()`:
    - Build OAuth2 authorize URL with `response_type=code`, `duration=permanent`
    - Generate and store CSRF state token
    - Return `ir.actions.act_url` redirect
- `controllers/main.py`:
  - `SocialRedditController(SocialController)`
  - Route `/social_reddit/callback`:
    - Validate state (CSRF)
    - Exchange code for access + refresh tokens
    - Fetch user info from Reddit API
    - Create or update `social.account`
    - Redirect to Stream Post dashboard
  - Route `/social_reddit/get_comments` — stub (Reddit has nested comments)
  - Route `/social_reddit/comment` — add comment to post
  - Route `/social_reddit/delete_comment` — delete comment
  - Route `/social_reddit/get_comments` — fetch comments thread

### Phase 4: Account Model + Sync
- `models/social_account.py`:
  - Fields: `reddit_user_id`, `reddit_username`, `reddit_access_token`, `reddit_refresh_token`, `reddit_token_expiry`
  - Override `_compute_stats_link()` → `https://www.reddit.com/user/{username}/`
  - Override `_compute_statistics()`:
    - Fetch user info (karma)
    - Set audience = total_karma, engagement = comment_karma
  - Override `create()` → create default "My Posts" stream
  - Method `_refresh_reddit_token()` → auto-refresh token
  - Method `_reddit_request()` → convenient wrapper using `RedditClient`
- `models/res_config_settings.py`:
  - Fields: `reddit_client_id`, `reddit_client_secret`
  - Settings form integration

### Phase 5: Publishing
- `models/social_post.py`:
  - Override `_message_fields()` → `{'reddit': 'reddit_message'}`
  - Override `_images_fields()` → `{'reddit': 'reddit_image_ids'}`
  - Override `_get_post_message_modifying_fields()` → `['reddit_title', 'reddit_subreddit', 'reddit_flair_text']`
- `models/social_post_template.py`:
  - Fields: `reddit_title` (char), `reddit_subreddit` (char), `reddit_flair_text` (char), `reddit_message` (text), `reddit_image_ids` (many2many ir.attachment)
  - Override `compute_message_by_media` and `compute_images_by_media`
- `models/social_live_post.py`:
  - Fields: `reddit_post_fullname` (char = t3_xxx)
  - Override `_post()`:
    - Build API request based on type (text/link/image)
    - Call `RedditClient.submit_post()`
    - Handle errors, token refresh, rate limits
  - Override `_compute_live_post_link()` → `https://www.reddit.com{permalink}`
  - Override `_refresh_statistics()`:
    - Call `/api/info` with post fullnames
    - Update engagement with score + num_comments

### Phase 6: Streams + Feed
- `models/social_stream.py`:
  - Override `_fetch_stream_data()`:
    - For "My Posts" → `get_user_posts()`
    - For "Subreddit Hot/New" → `get_subreddit_posts()`
    - Create `social.stream.post` records
- `models/social_stream_post.py`:
  - Fields: `reddit_post_fullname`
  - Override `_compute_author_link()` → `https://www.reddit.com/user/{author}/`
  - Override `_compute_post_link()` → `https://www.reddit.com{permalink}`
  - Override `_fetch_matching_post()` → match by `reddit_post_fullname`

### Phase 7: Statistics + Background Jobs
- Scheduled cron in data:
  - `ir_cron_reddit_refresh_token` — daily token refresh
  - Refresh statistics uses existing base social crons
  - Stream refresh uses existing `refresh_all` cron
- Token refresh before every API request:
  - Check `reddit_token_expiry`
  - If expired or within 5 minutes, auto-refresh
  - Update tokens and expiry on account

### Phase 8: Views, Settings, UI, Tests
- `views/res_config_settings_views.xml`:
  - Inherit `social.res_config_settings_view_form`
  - Add Reddit credentials block in "Developer Accounts" section
- `views/social_account_views.xml`:
  - Inherit `social.social_account_view_form`
  - Add Reddit-specific fields in a page/group
- `views/social_post_views.xml`:
  - Inherit `social.social_post_view_form`
  - Add Reddit-specific message, title, subreddit, flair, image fields
- `views/social_reddit_templates.xml`:
  - Post preview template
  - Stream post template
- Tests (comprehensive mock-based)

## Risks

1. **Reddit API changes** — mitigate by using User-Agent versioning
2. **Rate limiting** — mitigate by exponential backoff + caching
3. **Image upload complexity** — Reddit has a non-trivial media upload flow
4. **Subreddit restrictions** — karma requirements, posting cooldowns, etc.
