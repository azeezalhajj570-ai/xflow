# Reddit API Research

## Overview

Reddit provides a modern OAuth2-based API (`https://oauth.reddit.com`).
The API is RESTful, returns JSON, and uses Bearer token authentication.

## API Base URLs

| Purpose | URL |
|---------|-----|
| OAuth2 Authorization | `https://www.reddit.com/api/v1/authorize` |
| OAuth2 Token | `https://www.reddit.com/api/v1/access_token` |
| API Endpoint | `https://oauth.reddit.com` |
| OAuth2 Revoke | `https://www.reddit.com/api/v1/revoke_token` |

## OAuth2 Flow

Reddit uses standard OAuth2 Authorization Code grant:

1. **Authorization URL:**
   ```
   GET https://www.reddit.com/api/v1/authorize
     ?client_id=<CLIENT_ID>
     &response_type=code
     &state=<CSRF_STATE>
     &redirect_uri=<REDIRECT_URI>
     &duration=permanent
     &scope=<SCOPES>
   ```

2. **Token exchange:**
   ```
   POST https://www.reddit.com/api/v1/access_token
   Authorization: Basic <base64(client_id:client_secret)>
   Content-Type: application/x-www-form-urlencoded
   
   grant_type=authorization_code
   &code=<CODE>
   &redirect_uri=<REDIRECT_URI>
   ```

3. **Token refresh:**
   ```
   POST https://www.reddit.com/api/v1/access_token
   Authorization: Basic <base64(client_id:client_secret)>
   
   grant_type=refresh_token
   &refresh_token=<REFRESH_TOKEN>
   ```

4. **Revoke:**
   ```
   POST https://www.reddit.com/api/v1/revoke_token
   Authorization: Basic <base64(client_id:client_secret)>
   
   token=<TOKEN>
   &token_type_hint=access_token
   ```

## OAuth2 Scopes

| Scope | Permission | Required For |
|-------|-----------|--------------|
| `identity` | Access basic account info | Account sync |
| `read` | Read posts/comments | Stream fetching |
| `submit` | Submit links and comments | Publishing |
| `edit` | Edit/delete posts/comments | Post management |
| `history` | Access voting history | Analytics |
| `mysubreddits` | List subreddits user moderates | Stream setup |
| `privatemessages` | Access inbox | (optional) |
| `report` | Report content | (optional) |
| `save` | Save/unsave posts | (optional) |
| `vote` | Vote on content | (optional) |
| `wikiedit` | Edit wiki pages | (optional) |
| `wikiread` | Read wiki pages | (optional) |
| `modcontributors` | Manage contributors | (optional) |
| `modconfig` | Manage configuration | (optional) |
| `modflair` | Manage flairs | Publishing with flair |
| `modlog` | Access moderation log | (optional) |
| `modmail` | Access modmail | (optional) |
| `modothers` | Invite/remove mods | (optional) |
| `modposts` | Approve/remove posts | Moderation |
| `modtraffic` | Access traffic (analytics) | (optional) |
| `modwiki` | Manage wiki | (optional) |
| `structuredstyles` | Manage subreddit styles | (optional) |
| `subscribe` | Manage subscriptions | Subreddit selection |
| `account` | Account settings | (optional) |
| `creddits` | Purchase creddits | (optional) |
| `flair` | Manage user flair | (optional) |
| `livemanage` | Manage live threads | (optional) |
| `modcontributors` | Manage contributor list | (optional) |
| `modtraffic` | View subreddit traffic | Analytics |

### Required Scopes for social_reddit

```
identity  read  submit  edit  mysubreddits  subscribe  history modflair
```

## Token Response

```json
{
  "access_token": "xxxx",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "yyyy",
  "scope": "identity,read,submit"
}
```

- Access tokens expire in **1 hour** (3600 seconds)
- Refresh tokens are valid indefinitely (until revoked)
- `duration=permanent` is required to get a refresh_token

## Rate Limits

Reddit rate limits are based on the OAuth2 client ID:
- **60 requests per minute** per OAuth2 client (authenticated)
- Rate limit headers returned: `x-ratelimit-remaining`, `x-ratelimit-used`, `x-ratelimit-reset`
- Exceeding the limit returns HTTP 429 with a retry-after header
- Recommended: use exponential backoff on 429

## API Endpoints

### Authentication & Identity

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/me` | GET | Get current user info |
| `/api/v1/me/karma` | GET | Get user karma breakdown |

**User info response:**
```json
{
  "id": "2abc3",
  "name": "reddit_user",
  "is_employee": false,
  "icon_img": "https://...",
  "subreddit": {
    "display_name": "u_reddit_user",
    "name": "t5_xxx"
  },
  "total_karma": 10000,
  "link_karma": 5000,
  "comment_karma": 5000,
  "created_utc": 1500000000,
  "verified": true
}
```

### Subreddits

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/me/karma` | GET | Subreddit karma breakdown |
| `/subreddits/mine/subscriber` | GET | Subscribed subreddits |
| `/subreddits/mine/contributor` | GET | Subreddits user contributes to |
| `/subreddits/mine/moderator` | GET | Subreddits user moderates |
| `/api/v1/subreddit/search` | GET | Search subreddits (query: q) |
| `/subreddits/search` | GET | Search subreddits |
| `/api/subscribe` | POST | Subscribe/unsubscribe (sr, action: sub/unsub) |
| `/r/{subreddit}/about` | GET | Get subreddit info |
| `/api/v1/{subreddit}/post_flair` | GET | Get available flair |

**Subreddit info response:**
```json
{
  "data": {
    "display_name": "python",
    "title": "/r/Python",
    "public_description": "...",
    "subscribers": 750000,
    "created_utc": 1200000000,
    "over18": false,
    "user_is_subscriber": true,
    "subreddit_type": "public",
    "submit_text": "",
    "submit_text_label": "",
    "submission_type": "any",
    "link_flair_enabled": true,
    "link_flair_position": "left",
    "link_flair_text": ""
  }
}
```

### Posting

Reddit uses OAuth2 for posting. No separate media upload endpoint —
images are uploaded via direct POST to Reddit's media upload endpoint.

#### Text Post (Self Post)
```
POST /api/submit
kind=self
sr=<subreddit_fullname>
title=<title>
text=<markdown body>
```

#### Link Post
```
POST /api/submit
kind=link
sr=<subreddit_fullname>
title=<title>
url=<url>
```

#### Image Post (via media upload)
```
POST /api/submit
kind=image
sr=<subreddit_fullname>
title=<title>
```

Image must be uploaded first:
```
POST /api/media/asset.json
filepath=<filename>
mimetype=<mime>
```

Then submit with `video_poster_url` or use the newer `richtext_json` approach.

**Alternative (simpler):** Use the newer `/api/submit/media` endpoint.

For Reddit, the simplest image approach is to use the **Richtext JSON** format
or the `image` submit kind.

**Updated approach (Reddit API):**
```
POST /api/submit
kind=image
sr=t5_subreddit_id
title=My Title
image_asset=<image_asset_id>

# Where image_asset comes from:
POST /api/media/asset.json
{
  "filepath": "image.jpg",
  "mimetype": "image/jpeg"
}
```

#### Video Post
```
POST /api/submit
kind=video
sr=<subreddit_fullname>
title=<title>
videovideoposter_url=<thumbnail URL>
```

#### Gallery Post
```
POST /api/submit
kind=gallery
sr=<subreddit_fullname>
title=<title>
items=[{"media_id": "...", "caption": "..."}, ...]
```

### Post Actions

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/del` | POST | Delete a post/comment |
| `/api/editusertext` | POST | Edit a post/comment |
| `/api/hide` | POST | Hide a post |
| `/api/unhide` | POST | Unhide a post |
| `/api/lock` | POST | Lock a post (requires mod) |
| `/api/unlock` | POST | Unlock a post (requires mod) |
| `/api/marknsfw` | POST | Mark as NSFW |
| `/api/unmarknsfw` | POST | Unmark NSFW |
| `/api/set_contest_mode` | POST | Toggle contest mode |
| `/api/set_subreddit_sticky` | POST | Sticky/unsticky a post |
| `/api/set_suggested_sort` | POST | Set suggested sort |

### Comments

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/comment` | POST | Reply to post/comment |
| `/api/del` | POST | Delete comment |
| `/api/editusertext` | POST | Edit comment |

### User Content

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/user/{username}/submitted` | GET | User's posts |
| `/user/{username}/overview` | GET | User's posts and comments |
| `/api/v1/me/submitted` | GET | Current user's posts |

### Listings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/r/{subreddit}/hot` | GET | Hot posts |
| `/r/{subreddit}/new` | GET | New posts |
| `/r/{subreddit}/top` | GET | Top posts |
| `/r/{subreddit}/rising` | GET | Rising posts |
| `/r/{subreddit}/hot?limit=100` | GET | With limit (max 100) |
| `?after=t3_xxx&count=25` | GET | Pagination (fullname based) |

### Statistics & Analytics

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/r/{subreddit}/about/traffic` | GET | Subreddit traffic (mod only) |
| `/api/v1/me` | GET | User karma |
| `/api/v1/me/karma` | GET | Per-subreddit karma |
| `/api/info` | GET | Get info by fullname (ids=t3_xxx,t3_yyy) |

Reddit does NOT provide per-post analytics (no views, no reach, no impressions).
What's available per post:
- `score` (upvotes - downvotes)
- `ups` (upvotes)
- `downs` (downvotes)
- `num_comments`
- `upvote_ratio`

There are no awards data in the standard API (only via GraphQL/Pushshift).

## Post Listing Response

```json
{
  "kind": "Listing",
  "data": {
    "after": "t3_xxx",
    "dist": 25,
    "children": [
      {
        "kind": "t3",
        "data": {
          "id": "abc123",
          "name": "t3_abc123",
          "title": "Post Title",
          "selftext": "Post body...",
          "score": 150,
          "ups": 160,
          "downs": 10,
          "upvote_ratio": 0.94,
          "num_comments": 25,
          "permalink": "/r/subreddit/comments/abc123/post_title/",
          "url": "https://...",
          "created_utc": 1600000000,
          "author": "username",
          "author_fullname": "t2_xxx",
          "subreddit": "subreddit",
          "subreddit_name_prefixed": "r/subreddit",
          "subreddit_id": "t5_xxx",
          "thumbnail": "https://...",
          "thumbnail_height": 140,
          "thumbnail_width": 140,
          "link_flair_text": "Discussion",
          "link_flair_css_class": "discussion",
          "over_18": false,
          "spoiler": false,
          "stickied": false,
          "saved": false,
          "locked": false,
          "domain": "self.subreddit",
          "is_video": false,
          "is_gallery": false,
          "gallery_data": null,
          "media_metadata": null,
          "post_hint": "link",
          "preview": { "images": [...] }
        }
      }
    ]
  }
}
```

## Reddit API Limitations

1. **No per-post analytics/views/impressions** — only score, upvote ratio, and comment count
2. **No awards via standard API** — awards data not available via OAuth2
3. **Media upload is not direct** — requires `/api/media/asset.json` first
4. **Gallery posts are complex** — require multiple image uploads
5. **No scheduled posting** — must be handled client-side
6. **Rate limits** — 60 req/min per client, 600 req/min per app
7. **API versioning** — versionless (uses Accept header or URL path)
8. **Fullname (t3_, t2_, t5_)** — all IDs use type prefixes in "fullname" format
9. **No live video** — only submitted content
10. **No user search** by name — only known usernames
11. **Content restrictions** — some subreddits require minimum karma to post
12. **Subreddit-specific rules** — posting validation is server-side

## Key Differences from Other Social APIs

| Feature | Reddit | Facebook | Twitter |
|---------|--------|----------|---------|
| Token expiry | 1 hour (refreshable) | 60 days (or permanent page token) | Does not expire |
| Rate limit | 60 req/min | 200 calls/hour | 300 req/15min |
| Image upload | `/api/media/asset.json` | Direct to graph endpoint | 3-step media upload |
| Post ID format | `t3_xxx` (fullname) | Numeric string | Numeric string |
| Content type | Fullnames (`t3_`, `t2_`, `t5_`) | Pure IDs | Pure IDs |
| Permalink | `/r/sub/comments/id/title/` | `/pg_id/posts/perm_id` | `/user/status/id` |
| Account type | User only (no pages) | Pages + User | User only |

## Required Permissions / Scopes (Minimum)

For a functional social_reddit module:

```
identity   - Get user info (username, avatar, karma)
read       - Read posts for streams
submit     - Submit new posts
edit       - Edit/delete own posts
mysubreddits - List subscribed subreddits
history    - Access post history (for import)
modflair   - Set post flair (if subreddit requires it)
```

## Reddit App Types

Reddit has two app types relevant to OAuth:
1. **Web App** — uses Authorization Code grant with refresh tokens
2. **Installed App** — uses implicit grant (no refresh token)

For `social_reddit`, we need **Web App** type for refresh token support.

## References

- [Reddit API Documentation](https://www.reddit.com/dev/api/)
- [OAuth2 Wiki](https://github.com/reddit-archive/reddit/wiki/OAuth2)
- [Reddit App Preferences](https://www.reddit.com/prefs/apps)
