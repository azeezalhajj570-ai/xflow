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
            ('getxapi', 'REST API'),
        ],
        ondelete={'getxapi': 'cascade'},
    )

    authtoken = fields.Char(
        string='Auth Token',
        help='Twitter session auth_token for this provider\'s write operations.',
    )
    ct0 = fields.Char(
        string='ct0',
        help='Twitter session ct0 (CSRF) cookie, sent alongside the auth '
             'token when set. Leave empty for accounts whose provider calls '
             'only need the auth token.',
    )
    x_getxapi_credit_blocked = fields.Boolean(
        string='API Credit Exhausted',
        default=False,
        copy=False,
        readonly=True,
        help='Set automatically when the provider reports the account has no '
             'credit (HTTP 402 or a credit-related 429). While set, the paid '
             'task queue for this account is halted and no paid call is sent, '
             'so the balance is not burned on doomed requests. Add credit, '
             'then use "Resume API Usage" to lift the block.',
    )
    x_getxapi_credit_blocked_at = fields.Datetime(
        string='Credit Blocked At',
        readonly=True,
        copy=False,
    )
    x_getxapi_credit_error = fields.Text(
        string='Credit Error',
        readonly=True,
        copy=False,
        help='Last credit-exhaustion error reported by the provider.',
    )

    def _x_action_blocked_reason(self):
        """Halt the paid action queue while GetXAPI credit is exhausted."""
        reason = super()._x_action_blocked_reason()
        if reason:
            return reason
        if self.filtered('x_getxapi_credit_blocked'):
            return ('API credit exhausted — add credit, then use '
                    '"Resume API Usage" on the account.')
        return False

    def action_resume_getxapi_credit(self):
        """Lift the credit circuit breaker and let the paid queue resume."""
        self.ensure_one()
        self.write({
            'x_getxapi_credit_blocked': False,
            'x_getxapi_credit_blocked_at': False,
            'x_getxapi_credit_error': False,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'API Usage Resumed',
                'message': 'Credit block lifted; the paid task queue will '
                           'resume on the next sweep.',
                'type': 'success',
                'sticky': False,
            },
        }

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
