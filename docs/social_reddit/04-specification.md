# Specification: Reddit Social Marketing Provider for Odoo

## Objective

Build `social_reddit`, an Odoo Social Marketing provider module that integrates
Reddit as a native social media platform within the Odoo Social Marketing framework.
The module must be indistinguishable from official Odoo Social providers.

## Tech Stack

- **Odoo Version:** 18+ (compatible with 17, 19 where feasible)
- **Language:** Python 3
- **API:** Reddit OAuth2 API (`https://oauth.reddit.com`)
- **Dependencies:** `['social']`
- **License:** `OEEL-1`

## Assumptions

1. This is an Odoo Enterprise module (OEEL-1 license like the official providers)
2. Reddit OAuth2 uses standard Authorization Code grant with Basic auth for token exchange
3. Reddit API is accessed at `https://oauth.reddit.com`
4. Users configure their own Reddit Web App credentials in Settings
5. No IAP relay service (Reddit is not an Odoo IAP partner)
6. Reddit accounts are user-type only (no pages/business accounts like LinkedIn)
7. All API requests use the `User-Agent` header per Reddit API requirements

## Commands

```
Build:   (no build step — pure Python module)
Test:    docker exec odoo19-dev-odoo bash -c "psql -U odoo -d postgres -c 'DROP DATABASE IF EXISTS test_social_reddit;' && psql -U odoo -d postgres -c 'CREATE DATABASE test_social_reddit OWNER odoo;' && odoo -d test_social_reddit -i social_reddit --test-tags /social_reddit --stop-after-init --db_password 'odoo18@2024!'"
Lint:    Not specified (follow existing code style)
Dev:     Module is auto-loaded if placed in addons path
```

## Project Structure

```
social_reddit/
├── __init__.py
├── __manifest__.py
├── controllers/
│   ├── __init__.py
│   └── main.py              # OAuth callback + comment management
├── data/
│   └── social_media_data.xml # Social media record + stream types + cron
├── models/
│   ├── __init__.py
│   ├── res_config_settings.py  # Reddit app credentials in settings
│   ├── social_account.py       # Tokens, stats, default streams
│   ├── social_live_post.py     # _post, _refresh_statistics
│   ├── social_media.py         # media_type, _action_add_account
│   ├── social_post.py          # _message_fields, _images_fields
│   ├── social_post_template.py # Reddit-specific post fields
│   ├── social_stream.py        # _fetch_stream_data
│   └── social_stream_post.py   # _compute_author_link, _compute_post_link
├── security/
│   └── ir.model.access.csv     # ACLs
├── static/
│   └── src/
│       └── img/
│           └── reddit.svg      # Reddit logo
├── tests/
│   ├── __init__.py
│   └── test_social_reddit.py   # Tests
├── views/
│   ├── res_config_settings_views.xml  # Settings form
│   ├── social_account_views.xml       # Account form extension
│   ├── social_post_views.xml          # Post form extension
│   └── social_reddit_templates.xml    # QWeb templates
└── services/
    └── reddit_client.py        # Reusable Reddit API client
```

## Code Style

Follow Odoo conventions exactly as in official providers:

```python
# Odoo file header
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SocialMedia(models.Model):
    _inherit = 'social.media'

    _REDDIT_API_ENDPOINT = 'https://oauth.reddit.com'
    _REDDIT_AUTH_ENDPOINT = 'https://www.reddit.com/api/v1'
    _REDDIT_OAUTH_SCOPE = 'identity read submit edit mysubreddits history subscribe modflair'

    media_type = fields.Selection(selection_add=[('reddit', 'Reddit')])
```

Key conventions:
- Class constants for API endpoints
- `_filter_by_media_types()` for filtering recordsets
- `super()` calls for non-matching media types in overrides
- `SocialValidationException` for user-facing OAuth errors
- No hardcoded URLs (use class constants)
- Small focused methods
- `@api.model_create_multi` for create overrides
- `self.env.ref()` for referencing XML IDs
- No comments in code (per project convention)

## Testing Strategy

- **Framework:** Odoo TransactionCase (`odoo.tests.common.TransactionCase`)
- **Location:** `social_reddit/tests/test_social_reddit.py`
- **Tag:** `@tagged('post_install', '-at_install')`
- **Mocks:** `unittest.mock.patch` for all HTTP requests to Reddit API
- **Coverage:** All CRUD operations, OAuth flow, publishing, statistics, error handling

### Test Cases

1. **OAuth:** callback with valid code, invalid code, missing state, CSRF mismatch
2. **Account creation:** creates social.media record, links tokens, creates default stream
3. **Publishing:** text post, link post, image post, scheduled post
4. **Statistics:** account stats computation, live post stats refresh
5. **Streams:** fetch stream data for user posts, subreddit posts
6. **Token refresh:** expired token auto-refresh, failed refresh
7. **Error handling:** API errors, rate limiting, disconnected accounts
8. **Multiple accounts:** multiple users, multiple companies

## Boundaries

### Always
- Follow Odoo Social architecture patterns
- Use `_filter_by_media_types()` for recordset filtering
- Implement `_post()`, `_refresh_statistics()`, `_fetch_stream_data()`
- Store tokens as Char fields on social.account
- Validate CSRF state in OAuth callback
- Set `User-Agent` header on all Reddit API requests
- Handle HTTP 429 (rate limit) with retry
- Handle 401 (expired token) with auto-refresh

### Ask First
- Adding new dependencies beyond `['social']`
- Changing the `social.account` base model
- Adding new cron jobs (vs. using existing refresh mechanisms)
- Adding new views that differ from provider patterns
- Changing the OAuth flow pattern

### Never
- Store tokens in logs
- Log `access_token`, `refresh_token`, or `client_secret`
- Use unofficial Reddit APIs
- Hardcode user-specific values
- Skip `User-Agent` header
- Ignore rate limit headers

## Success Criteria

- [ ] Reddit appears in the Social Media list alongside Facebook, X, LinkedIn
- [ ] Users can connect a Reddit account via OAuth2
- [ ] Connected accounts show username, avatar, karma
- [ ] Users can create posts with text, links, and images
- [ ] Posts can be published immediately or scheduled
- [ ] Reddit posts appear in the Stream Feed
- [ ] Account statistics (karma, followers) are refreshed periodically
- [ ] Post statistics (score, comments) are refreshed periodically
- [ ] Expired tokens are automatically refreshed
- [ ] The module follows all Odoo Social conventions
- [ ] All tests pass

## Open Questions

1. Should we support IAP relay for Reddit? (No — Reddit is not an IAP partner, users configure own app)
2. Should we support the `wikiedit` scope for subreddit wiki? (No — out of scope for social marketing)
3. Should we support comment management via stream posts? (Yes — like Facebook/Linkedin)

## Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Reddit API rate limits | Medium | High | Implement exponential backoff, respect Retry-After |
| Token expiry | High | Medium | Auto-refresh before every API call |
| Subreddit posting restrictions | Medium | High | Validate before posting, clear error messages |
| Karma requirements | Medium | Medium | Catch API errors with friendly messages |
| API changes | High | Low | Use versioned approach via User-Agent header |
| Image upload complexity | Medium | Medium | Use /api/media/asset.json endpoint |
