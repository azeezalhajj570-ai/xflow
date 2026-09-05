# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, api, exceptions, models


class BaseAutomation(models.Model):
    _inherit = 'base.automation'

    def write(self, vals):
        # Prevent archiving the X Channel Retweet Automation
        if 'active' in vals and not vals['active']:
            for record in self:
                if record.id == self.env.ref('x_account.base_automation_x_message_retweet', raise_if_not_found=False).id:
                    raise exceptions.UserError(
                        _('The X Channel Retweet Automation cannot be disabled. '
                          'It is required for automatic retweet functionality.')
                    )
        return super().write(vals)

    def unlink(self):
        # Prevent deleting the X Channel Retweet Automation
        retweet_automation_id = self.env.ref('x_account.base_automation_x_message_retweet', raise_if_not_found=False).id
        if retweet_automation_id and retweet_automation_id in self.ids:
            raise exceptions.UserError(
                _('The X Channel Retweet Automation cannot be deleted. '
                  'It is required for automatic retweet functionality.')
            )
        return super().unlink()
