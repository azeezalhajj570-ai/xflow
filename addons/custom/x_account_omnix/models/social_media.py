# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Branch social.media's add-account flow to the OmniX import wizard.

When the configured X provider is 'omnix', adding an X account should open the
session import wizard pre-set to the OmniX REST path instead of the default
session-web path. x_account already branches by provider/auth-method; this only
adds the OmniX-specific default.
"""

from odoo import models


class SocialMedia(models.Model):
    _inherit = 'social.media'

    def _action_add_account(self):
        """Open the X import-session wizard with provider default 'omnix' when
        the configured X provider is 'omnix'."""
        self.ensure_one()
        if self.media_type == 'twitter':
            provider = self.env['ir.config_parameter'].sudo().get_param(
                'x_account.provider', 'session_web')
            if provider == 'omnix':
                return {
                    'name': 'Import X Session',
                    'type': 'ir.actions.act_window',
                    'res_model': 'x.import.session',
                    'view_mode': 'form',
                    'target': 'new',
                    'context': {
                        'default_media_id': self.id,
                        'default_provider': 'omnix',
                    },
                }
        return super()._action_add_account()
