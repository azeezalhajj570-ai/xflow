# TikTok API Data Persistence Audit

## Answer for TikTok Audit Question

**"Please list the API response data fields that your API client will save in its database."**

Below is the complete list of API response data fields that our Odoo Social Marketing TikTok integration persists in the database. Fields not listed are fetched but never written to any database table.

---

## Stored API Response Fields

### 1. OAuth Token Exchange — `POST /v2/oauth/token/`

| Response Field | DB Model | DB Column | Purpose | Retention | Encrypted? |
|---|---|---|---|---|---|
| `access_token` | `social.account` | `tiktok_access_token` | Authenticate all subsequent API requests on behalf of the user | Until user disconnects account or re-authorizes | **No** — stored as plain `fields.Char`, no `password=True` attribute |
| `refresh_token` | `social.account` | `tiktok_refresh_token` | Renew expired access tokens without user re-authorization | Until user disconnects account or re-authorizes | **No** — stored as plain `fields.Char` |
| `open_id` | `social.account` | `tiktok_account_id` | Unique identifier to match future OAuth callbacks to the existing account record | Permanent (life of the account record) | **No** |
| `scope` | — | — | **NOT stored.** Logged for debugging only. | — | N/A |

### 2. User Info — `GET /v2/user/info/` (at account creation)

| Response Field | DB Model | DB Column | Purpose | Retention | Encrypted? |
|---|---|---|---|---|---|
| `open_id` | `social.account` | `tiktok_account_id` | Same as above — set during account creation | Permanent | **No** |
| `display_name` | `social.account` | `name` | Human-readable account name shown in Odoo UI | Permanent | **No** |
| `username` | `social.account` | `social_account_handle` | TikTok @handle used to construct profile links | Permanent | **No** |
| `avatar_url` | `social.account` | `image` | Profile photo — URL content downloaded and stored as binary | Permanent | **No** (binary image data) |
| `follower_count` | `social.account` | `audience` | Initial follower count shown in account dashboard stats | Permanent (overwritten on periodic refresh) | **No** |
| `union_id` | — | — | **NOT stored.** Fetched in API request but never written to DB. | — | N/A |
| `following_count` | — | — | **NOT stored.** Fetched but never written to DB. | — | N/A |
| `likes_count` | — | — | **NOT stored at creation time.** (see refresh below) | — | N/A |
| `video_count` | — | — | **NOT stored.** Fetched but never written to DB. | — | N/A |

### 3. User Info — `GET /v2/user/info/` (periodic stats refresh)

| Response Field | DB Model | DB Column | Purpose | Retention | Encrypted? |
|---|---|---|---|---|---|
| `follower_count` | `social.account` | `audience` | Overwrites the previous follower count on every refresh cycle | Overwritten each refresh | **No** |
| `likes_count` | `social.account` | `engagement` | Total likes count shown in account dashboard stats | Overwritten each refresh | **No** |
| All other fields | — | — | **NOT stored** during periodic refresh. Only `follower_count` and `likes_count` are extracted from the response. | — | N/A |

### 4. Video List — `POST /v2/video/list/` (stream fetch)

| Response Field | DB Model | DB Column | Purpose | Retention | Encrypted? |
|---|---|---|---|---|---|
| `id` | `social.stream.post` | `tiktok_video_id` | TikTok video ID used to match live posts and construct video links | Permanent (upserted on each stream fetch) | **No** |
| `title` | `social.stream.post` | `message` | Video caption displayed in the stream kanban view | Permanent (upserted) | **No** |
| `cover_image_url` | `social.stream.post` | `link_image_url` | Video thumbnail URL (only stored if URL contains "tiktokcdn") | Permanent (upserted) | **No** (URL only, not binary) |
| `like_count` | `social.stream.post` | `tiktok_likes_count` | Engagement metric displayed in stream post list | Permanent (upserted) | **No** |
| `comment_count` | `social.stream.post` | `tiktok_comments_count` | Engagement metric displayed in stream post list | Permanent (upserted) | **No** |
| `share_count` | `social.stream.post` | `tiktok_shares_count` | Engagement metric displayed in stream post list | Permanent (upserted) | **No** |
| `view_count` | `social.stream.post` | `tiktok_views_count` | Engagement metric displayed in stream post list | Permanent (upserted) | **No** |
| `create_time` | `social.stream.post` | `published_date` | Video publication date (converted from Unix timestamp → Odoo datetime) | Permanent (upserted) | **No** |
| `embed_link` | — | — | **NOT stored.** Fetched from API but not persisted. | — | N/A |

### 5. Content Posting — `POST /v2/post/publish/video/init/`

| Response Field | DB Model | DB Column | Purpose | Retention | Encrypted? |
|---|---|---|---|---|---|
| `publish_id` | `social.live.post` | `tiktok_publish_id` | Reference identifier for the async publishing operation | Life of the live post record | **No** |
| `upload_url` | — | — | **NOT stored.** Temporary one-time URL used only in memory during upload. | — | N/A |
| `error.code` | — | — | **NOT stored.** Used only for error display/logging. | — | N/A |
| `error.message` | — | — | **NOT stored.** Used only for error display/logging. | — | N/A |

### 6. Temporary: Video Upload — `PUT <upload_url>` (Content Posting API)

| Response Field | DB Model | DB Column | Purpose | Retention | Encrypted? |
|---|---|---|---|---|---|
| HTTP status code | — | — | **NOT stored.** Only validated (200/201/206 = success). | — | N/A |

### 7. System Configuration — `ir.config_parameter` table

| Key | DB Column | Purpose | Retention | Encrypted? |
|---|---|---|---|---|
| `social.tiktok_client_key` | `value` | TikTok app client key for OAuth flow | Permanent until changed in Settings | **No** |
| `social.tiktok_client_secret` | `value` | TikTok app client secret for OAuth token exchange | Permanent until changed in Settings | **No** |
| `social.tiktok_oauth_state` | `value` | One-time CSRF state token for OAuth authorization | Cleared immediately after callback | **No** — ephemeral |

---

## Summary

**Total unique TikTok API response fields persisted: 17**

**Fields returned by API but NOT stored in our database:**
- `token_type`, `expires_in` (OAuth token exchange)
- `scope` (OAuth — logged only)
- `union_id` (user info)
- `following_count` (user info)
- `video_count` (user info)
- `embed_link` (video list)
- `upload_url` (publish init — used in memory then discarded)
- `upload_response` HTTP body (video upload)
- All error detail fields (`error.code`, `error.message`, `error.log_id`)

**Encryption status:** No TikTok API response fields are encrypted at rest. The fields `tiktok_access_token`, `tiktok_refresh_token`, `tiktok_client_key`, and `tiktok_client_secret` are stored as plain-text `fields.Char` columns.
