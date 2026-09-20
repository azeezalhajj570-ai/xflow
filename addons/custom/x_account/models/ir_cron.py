from dateutil.relativedelta import relativedelta

from odoo import fields, models

from odoo.addons.base.models.ir_cron import _intervalTypes


_intervalTypes['seconds'] = lambda interval: relativedelta(seconds=interval)


class IrCron(models.Model):
    _inherit = 'ir.cron'

    interval_type = fields.Selection(
        selection_add=[
            ('seconds', 'Seconds'),
        ],
        ondelete={'seconds': 'set default'},
    )