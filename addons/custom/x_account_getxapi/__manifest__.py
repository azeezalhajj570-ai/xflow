# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'X Account GetXAPI Provider',
    'category': 'Marketing/Social Marketing',
    'summary': 'Optional GetXAPI REST provider for X Account & Session Platform',
    'version': '19.0.1.0.0',
    'description': """
X Account GetXAPI Provider
==========================
Optional GetXAPI REST provider for the `x_account` module. Implements the
`XProvider` contract and self-registers with `XProviderRegistry`.

GetXAPI is a paid per-call API gateway to X/Twitter data. This module provides
a clean abstraction with centralized cost tracking, cursor pagination, bounded
retries, and call minimization.

Highlights:
- `GetXAPIProvider` implementing the XProvider surface (validate, DMs, tweet
  reads/writes, user operations, media upload)
- Layered client: transport (`GetXAPIClient`), envelope parsing
  (`GetXAPIEnvelopeParser`), error classification (`GetXAPIError`)
- Centralized pricing table (`ENDPOINT_COSTS`) with automatic cost tracking
- `getxapi.api.usage` model with admin UI for monitoring API spend
- Cursor pagination helper (`GetXAPIClient.paginate`)
- Bounded retry policy with write-safe behavior (no retry on timeout for writes)
- Self-registration with `XProviderRegistry` at import time (OCP)
    """,
    'depends': [
        'x_account',
        'mail',
    ],
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
