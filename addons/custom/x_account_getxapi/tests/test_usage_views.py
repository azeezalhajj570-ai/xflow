from odoo.tests import tagged

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIUsageViews(XAccountGetXAPITestBase):
    """GetXAPI Usage offers a search view (filters + group-by) and a pivot."""

    def _arch(self, model, view_type):
        result = self.env[model].get_view(view_type=view_type)
        return (result.get('arch') if isinstance(result, dict) else result[0]) or ''

    def test_search_view_filters_and_group_by(self):
        self.env.ref('x_account_getxapi.getxapi_usage_view_search')
        arch = self._arch('getxapi.api.usage', 'search')
        for name in ('filter_success', 'filter_failed', 'filter_get',
                     'filter_post', 'filter_4xx', 'filter_5xx',
                     'filter_rate_limit', 'filter_daily_dm_limit',
                     'filter_timestamp',
                     'groupby_account', 'groupby_endpoint', 'groupby_method',
                     'groupby_success', 'groupby_status_code',
                     'groupby_error_type', 'groupby_timestamp'):
            self.assertIn('name="%s"' % name, arch)

    def test_pivot_view_measures_and_groups(self):
        self.env.ref('x_account_getxapi.getxapi_usage_view_pivot')
        arch = self._arch('getxapi.api.usage', 'pivot')
        self.assertIn('<pivot', arch)
        for name in ('estimated_cost', 'request_duration', 'account_id',
                     'endpoint', 'method'):
            self.assertIn('name="%s"' % name, arch)
        self.assertIn('type="measure"', arch)
        self.assertIn('type="row"', arch)
        self.assertIn('type="col"', arch)

    def test_action_offers_pivot_and_groups_by_account(self):
        action = self.env.ref('x_account_getxapi.getxapi_usage_action')
        self.assertIn('pivot', action.view_mode)
        self.assertIn(
            "'search_default_groupby_account': 1", action.context or '')
