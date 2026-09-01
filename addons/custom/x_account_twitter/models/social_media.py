# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Route the X 'Link Account' flow to Odoo's OAuth 2.0 (PKCE) flow.

X no longer allows OAuth 1.0a authentication for Free-tier/new apps, so adding
an X account runs our OAuth 2.0 flow (authorize -> callback), which stores the
access/refresh tokens on the account. Session-cookie auth keeps x_account's
import-session wizard.
"""

from odoo import models


class SocialMedia(models.Model):
    _inherit = 'social.media'

    def _action_add_account(self):
        """Start the OAuth 2.0 flow when the auth method is 'oauth2' (or the
        legacy 'oauth1' value); otherwise keep the existing x_account behavior."""
        self.ensure_one()
        if self.media_type == 'twitter':
            auth_method = self.env['ir.config_parameter'].sudo().get_param(
                'x_account.auth_method', 'session_cookie')
            if auth_method in ('oauth1', 'oauth2'):
                return {
                    'name': 'Link X Account',
                    'type': 'ir.actions.act_url',
                    'url': '/x_account/twitter/oauth2/authorize',
                    'target': 'self',
                }
        return super()._action_add_account()