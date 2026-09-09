import datetime
import logging
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)


def _post_init_hook(env):
    _logger.info("base_automation_seconds: Running post_init_hook")
    from odoo.addons.base_automation.models import base_automation
    
    base_automation.DATE_RANGE['seconds'] = relativedelta(seconds=1)
    base_automation.DATE_RANGE_FACTOR['seconds'] = 1.0 / 60.0
    base_automation.TIMEDELTA_TYPES['seconds'] = lambda interval: datetime.timedelta(seconds=interval)
    _logger.info("base_automation_seconds: Added 'seconds' to DATE_RANGE, DATE_RANGE_FACTOR, and TIMEDELTA_TYPES")
