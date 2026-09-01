# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'X Account OmniX API',
    'category': 'Marketing/Social Marketing',
    'summary': 'Optional OmniX REST provider for X Account & Session Platform',
    'version': '19.0.1.0.0',
    'description': """
X Account OmniX API
===================
Optional OmniX REST provider for the `x_account` module. Implements the
`XProvider` interface registered with `XProviderRegistry`, plus the OmniX
webhook receiver and account webhook lifecycle.

The module is fully optional: accounts using SessionWebProvider or the
Official Publish adapter never import or depend on it. It is a per-account
either/or alternative to SessionWebProvider.

Highlights:
- `OmniXProvider` implementing the XProvider surface (validate, DMs, group
  automation ops) against the OmniX REST API
- Layered client: transport (`OmniXHttpClient`), envelope parsing
  (`OmniXEnvelopeParser`), error classification (`OmniXErrorMapper`)
- Group DM sync + member sync into `discuss.channel` / `res.partner`
- OmniX webhook registration/validation/deletion + CRC receiver route
- `x_omnix_api_key` configuration field in X Account Settings
    """,
    'depends': [
        'x_account',
        'mail',
    ],
    'data': [
        'security/x_account_omnix_security.xml',
        'data/server_actions.xml',
        'views/res_config_settings_views.xml',
        'views/social_account_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'OEEL-1',
}
