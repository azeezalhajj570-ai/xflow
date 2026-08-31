# Existing Provider Comparison

## Feature Matrix

| Feature | Facebook | LinkedIn | X (Twitter) | YouTube | TikTok |
|---------|----------|----------|-------------|---------|--------|
| **OAuth Grant** | Token (fragment) | Code | Request Token (OAuth1) | Code | Code |
| **API Version** | v17.0 (Graph) | REST 202511 | v2 | v3 | v2 |
| **Auth Mode** | Own app / IAP | Own app / IAP | Own app / IAP | Own app / IAP | Own credentials |
| **Token Type** | Page Access Token (long-lived) | Bearer token | OAuth1 tokens | Access + Refresh | Access + Refresh |
| **Token Refresh** | Extended token (60 days) | Manual re-auth | None needed | Auto via refresh_token | Auto via refresh_token |
| **Post Types** | Text, Image, Link, Video, GIF | Text, Single Image, Multi Image, Link | Text, Image, GIF | Video | Video |
| **Message Field** | `message` | `message` | `message` | `message` | `message` |
| **Image Field** | `image_ids` | `image_ids` | `image_ids` | N/A | `tiktok_video_ids` |
| **Account Stats** | Audience, Engagement | Audience, Engagement, Stories | Audience, Engagement, Stories | Engagement | Audience, Engagement |
| **Has Trends** | Yes | Yes | Yes | No | No |
| **Has Account Stats** | Yes | Yes | Yes | No | Yes |
| **Default Stream** | Page Posts | Company Posts | User Tweets | Channel Videos | User Videos |
| **Comment Mgmt** | Yes | Yes | Yes | Yes | No (stub) |
| **Like / React** | Yes | Yes | Yes (like/retweet) | Yes | No |
| **Post Deletion** | No | Yes | Yes | No | No |
| **Max Post Length** | 63,206 | 3,000 | 280 | 5,000 | 150 (title) |
| **Scheduling** | Yes | Yes | Yes | Yes | Yes |

## Models — Extension Points Used

| Provider | social.account fields | social.live.post fields | social.media |
|----------|----------------------|------------------------|--------------|
| Facebook | `facebook_account_id`, `facebook_access_token` | `facebook_post_id` | `media_type=facebook` |
| LinkedIn | `linkedin_account_urn`, `linkedin_access_token` | `linkedin_post_id` | `media_type=linkedin` |
| X | `twitter_user_id`, `twitter_oauth_token`, `twitter_oauth_token_secret` | `twitter_tweet_id` | `media_type=twitter` |
| YouTube | `youtube_channel_id`, `youtube_access_token`, `youtube_refresh_token` | (uses post_id.youtube_video_id) | `media_type=youtube` |
| TikTok | `tiktok_account_id`, `tiktok_access_token`, `tiktok_refresh_token` | `tiktok_video_id`, `tiktok_publish_id` | `media_type=tiktok` |

## OAuth Flow Comparison

| Step | Facebook | LinkedIn | X | TikTok |
|------|----------|----------|---|--------|
| 1. URL | dialog/oauth | oauth/v2/authorization | oauth/request_token | v2/auth/authorize |
| 2. Auth | User logs in | User logs in | User logs in | User logs in |
| 3. Callback | Hash fragment → code | Code | OAuth verifier | Code |
| 4. Token | Fragment → extended token | Code → access token | Verifier → access token | Code → access+refresh |
| 5. Accounts | /me/accounts | /organizationAcls | /2/users/me | /user/info/ |
| 6. Create | Create or update | Create or update | Create or update | Create or update |

## Key Implementation Patterns

### Facebook (most complete reference)
- `_FACEBOOK_ENDPOINT` / `_FACEBOOK_ENDPOINT_VERSIONED` as class constants
- `@fragment_to_query_string` decorator for hash fragment OAuth
- IAP support for account addition
- Post with single image → `/photos` endpoint
- Post with multiple images → `attached_media` params
- Post with link → `link` param
- Post with GIF → `/videos` endpoint
- Comment controller: add, delete, edit, fetch, like

### LinkedIn
- `_linkedin_request()` utility for all API calls (standardized method)
- URN format for IDs (`urn:li:organization:123`)
- Bearer token auth with `X-Restli-Protocol-Version` header
- Image upload pipeline: initializeUpload → upload binary
- `_format_to_linkedin_little_text()` — escapes special characters
- `/socialMetadata` endpoint for batch statistics

### X (Twitter) — OAuth 1.0a (different from all others)
- OAuth 1.0a with HMAC-SHA1 signature
- Request token → authorize → access token flow
- 3-step media upload: initialize → append → finalize
- `/2/tweets` endpoint for posting and statistics

### TikTok
- Clean separation of concerns
- CSRF state token stored and validated
- `/post/publish/video/init/` → upload URL → PUT upload
- Expired token auto-retry with refresh
- Unaudited client fallback to SELF_ONLY privacy

## What Reddit Must Implement (In Order)

1. OAuth2 with code grant (like LinkedIn/TikTok — Reddit uses standard OAuth2)
2. Token management with refresh_token (like TikTok/YouTube)
3. Social media record + stream types
4. Account creation with token storage
5. Post publishing (text, link, image)
6. Account statistics
7. Post statistics (engagement refresh)
8. Stream fetching (user posts, subreddit posts)
9. Scheduled jobs for token refresh and stats
10. Views and settings integration
