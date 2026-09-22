# Part of Odoo. See LICENSE file for full copyright and licensing details.

import datetime

from dateutil.relativedelta import relativedelta

from odoo import fields, models
from odoo.addons.base_automation.models.base_automation import (
    DATE_RANGE,
    DATE_RANGE_FACTOR,
    TIME_TRIGGERS,
    TIMEDELTA_TYPES,
)

# ``base.automation`` only knows minutes/hours/days/months, and its evaluator
# cron is floored at one minute. Register a ``seconds`` unit so the X rules can
# be configured (and evaluated) within seconds instead of a whole minute.
DATE_RANGE.setdefault('seconds', relativedelta(seconds=1))
DATE_RANGE_FACTOR.setdefault('seconds', 1 / 60)
TIMEDELTA_TYPES.setdefault(
    'seconds', lambda interval: datetime.timedelta(seconds=interval))


class BaseAutomation(models.Model):
    _inherit = 'base.automation'

    trg_date_range_type = fields.Selection(
        selection_add=[('seconds', 'Seconds')],
        ondelete={'seconds': 'set null'},
    )

    def _get_cron_interval(self, automations=None):
        """Run the evaluator cron at the cadence of the shortest seconds delay.

        The stock implementation maps every delay to whole minutes and floors
        the interval at one minute, so a seconds-based rule would still only be
        picked up once a minute. When any rule's delay is in seconds, the cron
        is set to that same number of seconds (worst-case latency ~= 2x the
        delay); otherwise the original minute/hour behaviour is kept.
        """
        if automations is None:
            automations = self.with_context(active_test=True).search(
                [('trigger', 'in', TIME_TRIGGERS)])
        delays = [
            abs(automation.trg_date_range)
            for automation in automations
            if automation.trg_date_range
            and automation.trg_date_range_type == 'seconds'
        ]
        if delays:
            return max(1, int(min(delays))), 'seconds'
        return super()._get_cron_interval(automations)
