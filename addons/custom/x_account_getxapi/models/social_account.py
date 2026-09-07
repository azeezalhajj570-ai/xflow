# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Extend social.account with the GetXAPI provider option.

Adds 'getxapi' to the x_provider selection so accounts can use GetXAPI
as their X API provider.
"""

from odoo import fields, models


class SocialAccount(models.Model):
    _inherit = 'social.account'

    x_provider = fields.Selection(
        selection_add=[
            ('getxapi', 'GetXAPI REST API'),
        ],
        ondelete={'getxapi': 'cascade'},
    )

    x_getxapi_auth_token = fields.Char(
        string='GetXAPI Auth Token',
        help='Twitter session auth_token for GetXAPI write operations.',
    )

    def _skip_oauth_stats(self):
        """Skip OAuth-based stats for GetXAPI accounts (no OAuth tokens)."""
        skip = super()._skip_oauth_stats()
        return skip | self.filtered(
            lambda a: a.media_type == 'twitter' and a.x_provider == 'getxapi')

    def _get_twitter_oauth_header(self, url, headers=None, params=None, method='GET'):
        """Return empty headers for GetXAPI accounts (no OAuth tokens)."""
        self.ensure_one()
        if self.x_provider == 'getxapi':
            return headers or {}
        return super()._get_twitter_oauth_header(url, headers=headers, params=params, method=method)
