# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Extend x.import.session with the OmniX provider option.

x_account's import wizard hard-codes session_web + official_publish. This
module adds 'omnix' as a third provider option and dispatches validation to
OmniXProvider when selected (SRP: the wizard only picks the provider, the
provider validates).
"""

from odoo import _, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.x_account.services.providers.session_web import SessionWebProvider


class XImportSession(models.TransientModel):
    _inherit = 'x.import.session'

    provider = fields.Selection(
        selection_add=[
            ('omnix', 'OmniX REST API'),
        ],
        ondelete={'omnix': 'cascade'},
    )

    def action_import(self):
        """Validate the session via the selected provider, then persist."""
        if self.provider == 'omnix':
            return self._action_import_omnix()
        return super().action_import()

    def _action_import_omnix(self):
        self.ensure_one()
        auth_token = self.auth_token.strip()
        if not auth_token:
            raise ValidationError(_('auth_token is required.'))
        cookie_parts = ['auth_token=%s' % auth_token]
        if self.ct0:
            cookie_parts.append('ct0=%s' % self.ct0.strip())
        cookie_string = '; '.join(cookie_parts)
        cookies = SessionWebProvider.parse_cookie_string(cookie_string)
        if not cookies.get('auth_token'):
            raise ValidationError(_('auth_token is required.'))

        account = self.env['social.account'].with_context(
            x_no_default_stream=True).create({
                'name': self.name or self.username or 'X Account',
                'social_account_handle': self.username or '',
                'media_id': self.media_id.id,
                'x_provider': 'omnix',
                'x_auth_method': 'session_cookie',
                'x_connection_status': 'authenticating',
            })

        from odoo.addons.x_account_omnix.services.omnix_provider import OmniXProvider
        provider = OmniXProvider(self.env, account, cookies)
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
        from odoo.addons.x_account.services.session_manager import XSessionManager
        XSessionManager.create_store(account, cookie_string, source='wizard')
        XSessionManager.register_runtime(account, provider)
        return {'type': 'ir.actions.act_window_close'}
