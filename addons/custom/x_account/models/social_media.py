# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models


class SocialMedia(models.Model):
    _inherit = 'social.media'

    def _action_add_account(self):
        """Branch by configured provider/auth method.

        - omnix provider -> open the X import-session wizard (OmniX REST path).
        - session_cookie -> open the X import-session wizard.
        - oauth1 (official publish) -> delegate to super() (social_twitter OAuth flow).
        """
        self.ensure_one()
        if self.media_type == 'twitter':
            provider = self.env['ir.config_parameter'].sudo().get_param(
                'x_account.provider', 'session_web')
            auth_method = self.env['ir.config_parameter'].sudo().get_param(
                'x_account.auth_method', 'session_cookie')
            if provider == 'omnix' or auth_method == 'session_cookie':
                return {
                    'name': 'Import X Session',
                    'type': 'ir.actions.act_window',
                    'res_model': 'x.import.session',
                    'view_mode': 'form',
                    'target': 'new',
                    'context': {
                        'default_media_id': self.id,
                        'default_provider': 'omnix' if provider == 'omnix' else 'session_web',
                    },
                }
        return super()._action_add_account()
