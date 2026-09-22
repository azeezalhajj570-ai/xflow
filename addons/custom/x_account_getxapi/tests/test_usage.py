from odoo.tests import tagged

from odoo.addons.x_account.models.account_task import TASK_CONTEXT_KEY
from odoo.addons.x_account_getxapi.services.getxapi_client import GetXAPIClient

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIUsage(XAccountGetXAPITestBase):
    """Usage records must keep their account attribution."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['social.account'].create({
            'name': 'Usage Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'usage_user',
            'twitter_user_id': '12345',
        })

    def _usage(self, **vals):
        base = {
            'account_id': self.account.id,
            'endpoint': 'twitter/tweet/favorite',
            'method': 'POST',
            'status_code': 402,
            'success': False,
        }
        base.update(vals)
        return self.env['getxapi.api.usage'].sudo().create(base)

    def test_usage_account_cannot_be_deleted(self):
        """Deleting an account with usage records is refused, so spend history
        is never silently anonymized and hidden from the usage page."""
        self._usage()
        with self.assertRaises(Exception):
            self.account.unlink()

    def test_account_without_usage_can_be_deleted(self):
        """The restriction only applies to accounts that have spend history."""
        self.account.unlink()
        self.assertFalse(self.account.exists())


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIUsageTaskLink(XAccountGetXAPITestBase):
    """Usage rows link back to the task that triggered the API call."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['social.account'].create({
            'name': 'Linked Usage Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'linked_usage',
            'twitter_user_id': '99999',
        })

    def _task(self, operation='like'):
        return self.env['x.account.task'].create({
            'account_id': self.account.id,
            'operation': operation,
        })

    def _last_usage(self):
        return self.env['getxapi.api.usage'].sudo().search(
            [('account_id', '=', self.account.id)],
            order='id desc', limit=1)

    def test_log_usage_records_task_from_context(self):
        task = self._task()
        client = GetXAPIClient(
            self.env(context={TASK_CONTEXT_KEY: task.id}), 'key',
            account_id=self.account.id)
        client._log_usage('twitter/tweet/favorite', 'POST', 200, True, 5)
        self.assertEqual(self._last_usage().task_id, task)

    def test_log_usage_without_context_has_no_task(self):
        client = GetXAPIClient(self.env, 'key', account_id=self.account.id)
        client._log_usage('twitter/tweet/favorite', 'POST', 200, True, 5)
        self.assertFalse(self._last_usage().task_id)

    def test_action_view_operation_targets_the_task_report_row(self):
        task = self._task('comment')
        usage = self.env['getxapi.api.usage'].sudo().create({
            'account_id': self.account.id,
            'endpoint': 'twitter/tweet/create',
            'method': 'POST',
            'status_code': 200,
            'success': True,
            'task_id': task.id,
        })
        action = usage.action_view_operation()
        self.assertEqual(action['res_model'], 'x.account.operation.report')
        self.assertEqual(action['domain'], [('id', '=', task.id)])

    def test_action_view_operation_without_task_does_nothing(self):
        usage = self.env['getxapi.api.usage'].sudo().create({
            'account_id': self.account.id,
            'endpoint': 'twitter/tweet/favorite',
            'method': 'POST',
            'status_code': 200,
            'success': True,
        })
        self.assertFalse(usage.action_view_operation())
