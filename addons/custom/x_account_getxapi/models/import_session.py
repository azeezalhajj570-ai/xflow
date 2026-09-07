# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Extend x.import.session with the GetXAPI provider option.

GetXAPI uses API key authentication only (no cookies required). The wizard
validates the session by calling the GetXAPI user info endpoint with the
provided handle.
"""

from odoo import _, fields, models
from odoo.exceptions import ValidationError


class XImportSession(models.TransientModel):
    _inherit = 'x.import.session'

    provider = fields.Selection(
        selection_add=[
            ('getxapi', 'GetXAPI REST API'),
        ],
        ondelete={'getxapi': 'cascade'},
    )

    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        if 'provider' in fields_list and not result.get('provider'):
            provider = self.env['ir.config_parameter'].sudo().get_param(
                'x_account.provider', 'session_web')
            if provider in ('getxapi', 'omnix', 'session_web', 'official_publish'):
                result['provider'] = provider
        return result

    def action_import(self):
        """Validate the session via the selected provider, then persist."""
        if self.provider == 'getxapi':
            return self._action_import_getxapi()
        return super().action_import()

    def _action_import_getxapi(self):
        """Import account using GetXAPI provider (API key only, no cookies)."""
        self.ensure_one()
        handle = self.username.strip() if self.username else ''
        if not handle:
            raise ValidationError(_('Username / Handle is required for GetXAPI.'))

        auth_token = self.auth_token.strip() if self.auth_token else ''

        account = self.env['social.account'].with_context(
            x_no_default_stream=True).create({
                'name': self.name or self.username or 'X Account',
                'social_account_handle': handle,
                'media_id': self.media_id.id,
                'x_provider': 'getxapi',
                'x_auth_method': 'session_cookie',
                'x_connection_status': 'authenticating',
                'x_getxapi_auth_token': auth_token,
            })

        from odoo.addons.x_account_getxapi.services.getxapi_provider import GetXAPIProvider
        provider = GetXAPIProvider(self.env, account)
        result = provider.validate_session()
        if not result.get('valid'):
            account.unlink()
            raise ValidationError(_('Session validation failed: %s') % result.get('reason'))

        user = result.get('user') or {}
        account.write({
            'twitter_user_id': user.get('id') or account.twitter_user_id,
            'social_account_handle': user.get('username') or account.social_account_handle,
            'name': user.get('name') or account.name,
            'x_connection_status': 'active',
            'last_connected': fields.Datetime.now(),
            'last_validated': fields.Datetime.now(),
        })
        return {'type': 'ir.actions.act_window_close'}
