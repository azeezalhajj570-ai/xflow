# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Branch social.media's add-account flow to the GetXAPI path.

When the configured X provider is 'getxapi', adding an X account should
open the session import wizard pre-set to the GetXAPI path.
"""

from odoo import models


class SocialMedia(models.Model):
    _inherit = 'social.media'

    def _action_add_account(self):
        """Open the X import-session wizard with provider default 'getxapi' when
        the configured X provider is 'getxapi'."""
        self.ensure_one()
        if self.media_type == 'twitter':
            provider = self.env['ir.config_parameter'].sudo().get_param(
                'x_account.provider', 'session_web')
            if provider == 'getxapi':
                return {
                    'name': 'Import X Session',
                    'type': 'ir.actions.act_window',
                    'res_model': 'x.import.session',
                    'view_mode': 'form',
                    'target': 'new',
                    'context': {
                        'default_media_id': self.id,
                        'default_provider': 'getxapi',
                    },
                }
        return super()._action_add_account()
