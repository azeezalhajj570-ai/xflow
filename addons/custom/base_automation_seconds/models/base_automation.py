import datetime
from odoo import fields, models
from dateutil.relativedelta import relativedelta
from odoo.addons.base_automation.models import base_automation as ba_module


# Patch the dictionaries at module load time
ba_module.DATE_RANGE['seconds'] = relativedelta(seconds=1)
ba_module.DATE_RANGE_FACTOR['seconds'] = 1.0 / 60.0
ba_module.TIMEDELTA_TYPES['seconds'] = lambda interval: datetime.timedelta(seconds=interval)


class BaseAutomation(models.Model):
    _inherit = 'base.automation'

    trg_date_range_type = fields.Selection(
        selection_add=[
            ('seconds', 'Seconds'),
        ],
        ondelete={'seconds': 'set null'},
    )
