from odoo import fields, models


class BaseAutomation(models.Model):
    _inherit = 'base.automation'

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        index=True,
        help='Company to which this automation rule belongs. '
             'Users can only see automation rules for their company.'
    )
