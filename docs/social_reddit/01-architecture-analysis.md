# Architecture Analysis: Odoo Social Marketing

## Overview

Odoo Social Marketing (`social`) is the base module that provides a unified framework
for managing social media accounts, publishing posts, and monitoring engagement across
multiple social networks. Each social network is implemented as a separate provider
module (`social_facebook`, `social_linkedin`, `social_twitter`, etc.) that inherits from
and extends the base models.

## Core Models

### `social.media`
- Represents a social network platform (Facebook, X, LinkedIn, etc.)
- Identified by `media_type` (selection field)
- Stores API configuration (for self-hosted OAuth)
- Has `_action_add_account()` — the OAuth entry point
- Has `has_streams`, `can_link_accounts`, `max_post_length` flags
- Has `stream_type_ids` for available stream types
- Has `csrf_token` computed field for CSRF validation

**Extension points:**
- `media_type` — providers add their value via `selection_add`
- `_action_add_account()` — providers override to build OAuth URL
- `max_post_length` — set per media type

### `social.account`
- Represents an actual account on a social network (a Facebook Page, a Twitter account)
- Has `media_id` (link to social.media), `image`, `audience`, `engagement`, `stories` fields
- Statistical fields (`audience`, `audience_trend`, `engagement`, `engagement_trend`, `stories`, `stories_trend`) are computed manually via `_compute_statistics()`
- Related to `utm.medium` (one per account, for tracking)
- Multi-company aware via `company_id`

**Extension points:**
- `_compute_statistics()` — override to fetch account-level stats
- `_compute_stats_link()` — override to provide external analytics link
- `create()` — override to add default streams
- Add provider-specific fields (tokens, IDs)

### `social.post` (inherits `social.post.template`)
- Represents a post to be published to multiple accounts
- Has `state` (draft/scheduled/posting/posted), `post_method` (now/scheduled), `scheduled_date`
- Created with default accounts (up to 3)
- `_action_post()` creates `social.live.post` records and calls `_post()` on each
- Has `live_post_ids` (one2many to social.live.post)
- Related to `utm.source`

**Extension points:**
- `_message_fields()` — return dict of media_type → message field name
- `_images_fields()` — return dict of media_type → image field name
- `_get_post_message_modifying_fields()` — fields needed for message post-processing
- `_prepare_post_content()` — customize message per media type
- `_prepare_live_post_values()` — customize live post creation

### `social.live.post`
- Represents a post actually published on a specific account (one per account per post)
- Has `state` (ready/posting/posted/failed), `account_id`, `post_id`
- `_post()` — the actual API call to publish (providers override this)
- `_refresh_statistics()` — fetch engagement data from the API
- Has `engagement` (integer)
- Provider-specific fields for the external post ID

**Extension points:**
- `_post()` — override to implement publishing via provider API
- `_refresh_statistics()` — override to fetch post-level engagement
- `_compute_live_post_link()` — override to provide link to external post
- Add provider-specific ID fields (e.g., `facebook_post_id`, `twitter_tweet_id`)

### `social.stream`
- Represents a feed of posts from a social network (Page Posts, User Tweets, etc.)
- Has `media_id`, `account_id`, `stream_type_id`, `stream_post_ids`
- `_fetch_stream_data()` — override to fetch posts from API

### `social.stream.post`
- Represents a post from a social network feed
- Has `message`, `author_name`, `author_link`, `post_link`, `published_date`, `stream_id`
- `_compute_author_link()` — override
- `_compute_post_link()` — override
- `_fetch_matching_post()` — match stream post to a social.post

### `social.stream.type`
- Defines types of streams available per media (e.g., "Page Posts", "Mentions")
- Has `name`, `stream_type`, `media_id`

## OAuth Flow Pattern

All providers follow the same OAuth2 pattern:

1. **User clicks "Add Account"** on the social.media form
2. **`_action_add_account()`** builds the OAuth authorization URL and returns an `ir.actions.act_url` to redirect the user
3. **User authorizes** on the third-party site
4. **Provider callback** route receives the authorization code/token
5. **Callback controller** exchanges the code for tokens, fetches user info, creates/updates `social.account`
6. **User is redirected** to the Stream Post dashboard

Two authentication modes:
- **Own app**: OAuth credentials configured in res.config.settings
- **IAP (Odoo cloud)**: Uses Odoo's IAP relay service

## Publishing Flow

1. User creates a `social.post` with a message, images, and selected accounts
2. User clicks "Send Now" (or schedules via cron)
3. `_action_post()` creates one `social.live.post` per account
4. For each live post, `_post()` is called
5. Provider's `_post()` implementation makes the API call
6. Live post state is updated to "posted" or "failed"
7. Parent post completion is checked

## Cron Jobs

| Cron | Purpose |
|------|---------|
| `ir_cron_post_scheduled` | Publishes scheduled posts (runs hourly) |
| `refresh_statistics` (account) | Refreshes account-level stats (audience, engagement) |
| `refresh_statistics` (live post) | Refreshes post-level engagement stats |
| `refresh_all` (stream) | Fetches new stream posts |

All providers must implement:
- `_refresh_statistics()` on `social.live.post`
- `_compute_statistics()` on `social.account`

## Stream / Feed Pattern

1. Each provider defines a `social.stream.type` record (e.g., "Page Posts")
2. Account creation creates a default stream
3. `_fetch_stream_data()` is called on stream creation and periodically
4. Stream posts are displayed in the Feed kanban view

## Security Model

- `group_social_user` — read/create/write/unlock own
- `group_social_manager` — full access
- Multi-company rules on accounts, posts, streams
- `@fragment_to_query_string` decorator for OAuth callback
- CSRF token validation on OAuth state

## Module Structure (Official Providers)

```
social_facebook/
  __init__.py
  __manifest__.py        # depends: ['social']
  controllers/
    __init__.py
    main.py              # OAuth callback + comment management
  models/
    __init__.py
    res_config_settings.py
    social_account.py    # _compute_statistics, tokens, create default streams
    social_live_post.py  # _post, _refresh_statistics, _compute_live_post_link
    social_media.py      # media_type, _action_add_account, endpoints
    social_post.py       # _message_fields, _images_fields
    social_post_template.py
    social_stream.py     # _fetch_stream_data
    social_stream_post.py
  views/
    ...
  data/                  # social_media_data.xml (media + stream types)
  static/
  tests/
```

## Key Conventions

- Provider modules depend only on `['social']` (or `['social', 'iap']`)
- `media_type` values are lowercase single words: `'facebook'`, `'twitter'`, `'linkedin'`, `'youtube'`, `'tiktok'`
- API endpoints stored as class constants on the `social.media` model
- OAuth controller inherits from `social.controllers.main.SocialController`
- Error handling uses `SocialValidationException` for user-facing errors
- Account disconnection calls `_action_disconnect_accounts()` on `social.account`
- Token refresh is implemented on the `social.account` model
- Default streams created in `create()` override on `social.account`
