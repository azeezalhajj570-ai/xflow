# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.x_account.services.providers.session_web import SessionWebProvider
from odoo.addons.x_account.services.session_manager import XSessionManager

_logger = logging.getLogger(__name__)


class XImportSession(models.TransientModel):
    _name = 'x.import.session'
    _description = 'Import X Session'

    media_id = fields.Many2one('social.media', string='X Media', required=True)
    name = fields.Char(string='Display Name')
    auth_token = fields.Char(string='auth_token', required=True)
    ct0 = fields.Char(string='ct0')
    username = fields.Char(string='Username / Handle')
    provider = fields.Selection(
        [
            ('session_web', 'Session Web'),
            ('official_publish', 'Official Publish'),
            ('omnix', 'OmniX REST API'),
        ],
        string='Provider',
        default='session_web',
    )

    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        if 'media_id' in fields_list and not result.get('media_id'):
            media = self.env['social.media'].search(
                [('media_type', '=', 'twitter')], limit=1)
            if media:
                result['media_id'] = media.id
        return result

    def action_import(self):
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

        twitter_media = self.media_id
        account = self.env['social.account'].with_context(
            x_no_default_stream=True).create({
            'name': self.name or self.username or 'X Account',
            'social_account_handle': self.username or '',
            'media_id': twitter_media.id,
            'x_provider': self.provider,
            'x_auth_method': 'session_cookie',
            'x_connection_status': 'authenticating',
        })

        # Validate before persisting: if invalid, rollback the account.
        if self.provider == 'omnix':
            from odoo.addons.x_account.services.providers.omnix import OmniXProvider
            provider = OmniXProvider(self.env, account, cookies)
        else:
            provider = SessionWebProvider(self.env, account, cookies)
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
        XSessionManager.create_store(account, cookie_string, source='wizard')
        XSessionManager.register_runtime(account, provider)
        return {'type': 'ir.actions.act_window_close'}
