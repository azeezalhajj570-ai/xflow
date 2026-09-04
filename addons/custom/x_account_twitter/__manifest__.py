# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'X Account Twitter Provider',
    'category': 'Marketing/Social Marketing',
    'summary': 'Twitter/X provider for X Account & Session Platform via OAuth 2.0',
    'version': '19.0.2.0.0',
    'description': """
X Account Twitter Provider
==========================
Optional Twitter/X provider for the `x_account` module. Implements the
`XProvider` contract and self-registers with `XProviderRegistry`.

The provider links accounts through the official X API using OAuth 2.0 with
PKCE. X no longer offers OAuth 1.0a authentication to Free-tier/new apps. The
access and refresh tokens are stored on the `social.account`, refreshed lazily,
and used as a Bearer token for all official-API calls (shared with Social
Marketing's Twitter/X integration).

Account linking.

- Set the X Authentication Method to "OAuth 2.0 (Official Publish)".
- Configure the X OAuth 2.0 Client ID and Client Secret.
- Add the callback URI to the X app (see the settings page).
- "Link Account" then runs the OAuth 2.0 flow (authorize on x.com, callback).
- The resulting account is auto-assigned the `twitter` provider and works with
  the official X API. Multiple X accounts are supported.

Highlights.

- `TwitterOAuth2Client`: OAuth 2.0 PKCE authorize/token/user plumbing.
- `TwitterProvider` implementing the XProvider surface (validate_session, repost).
- Auto-refresh of expired OAuth 2.0 access tokens (lazy and on 401 retry).
- Backward compatible: pre-existing OAuth 1.0a accounts keep working.
- Layered client: transport (`TwitterApiClient`), envelope parsing.
  (`TwitterEnvelope`), error classification (`TwitterErrorMapper`).
- `TwitterLink` resolver for X/Twitter post URLs (platform, post_id, canonical).
- Self-registration with `XProviderRegistry` at import time (OCP).
    """,
    'depends': [
        'x_account',
        'social_twitter',
    ],
    'external_dependencies': {
        'python': ['chatxdk'],
    },
    'data': [
        'security/ir.model.access.csv',
        'security/ir_rules.xml',
        'data/cron.xml',
        'views/res_config_settings_views.xml',
        'views/social_media_views.xml',
        'views/social_account_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'OEEL-1',
}
