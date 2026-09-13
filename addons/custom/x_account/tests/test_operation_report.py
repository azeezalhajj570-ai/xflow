import json

from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestXAccountOperationReport(XAccountTestBase):
    """x.account.operation.report: SQL view over engagement tasks."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Report Account',
            'media_id': cls.media.id,
            'social_account_handle': 'report_acc',
        })
        # auto_execute stays False so creating the group enqueues nothing.
        cls.group = cls.env['x.account.group'].create({
            'name': 'Report Group',
            'account_ids': [(6, 0, [cls.account.id])],
        })
        cls.Report = cls.env['x.account.operation.report']

    def _task(self, operation, context, **vals):
        return self.env['x.account.task'].create({
            'account_id': self.account.id,
            'operation': operation,
            'task_context': json.dumps(context),
            **vals,
        })

    def _row(self, task):
        return self.Report.search([('id', '=', task.id)], limit=1)

    def test_view_exposes_group_account_operation_and_link(self):
        task = self._task(
            'like', {'post_id': '111', 'screen_name': 'alice'},
            group_id=self.group.id)
        row = self._row(task)
        self.assertEqual(row.account_id, self.account)
        self.assertEqual(row.group_id, self.group)
        self.assertEqual(row.operation, 'like')
        self.assertEqual(row.tweet_id, '111')
        self.assertEqual(row.tweet_screen_name, 'alice')
        self.assertEqual(row.tweet_url, 'https://x.com/alice/status/111')
        self.assertEqual(row.operation_count, 1)

    def test_nested_channel_context_uses_generic_link(self):
        task = self._task(
            'comment', {'post': {'post_id': '222'}, 'channel_id': 7})
        row = self._row(task)
        self.assertEqual(row.tweet_id, '222')
        self.assertEqual(row.channel_id.id, 7)
        self.assertEqual(row.tweet_url, 'https://x.com/i/web/status/222')

    def test_group_target_id_context(self):
        task = self._task(
            'repost', {'target_id': '333'}, group_id=self.group.id)
        row = self._row(task)
        self.assertEqual(row.tweet_id, '333')
        self.assertEqual(row.tweet_url, 'https://x.com/i/web/status/333')

    def test_author_partner_supplies_screen_name(self):
        self.env['res.partner'].create({
            'name': 'Bob', 'x_user_id': '42', 'x_username': 'bob',
        })
        task = self._task('bookmark', {'post_id': '444', 'author_x_id': '42'})
        row = self._row(task)
        self.assertEqual(row.tweet_screen_name, 'bob')
        self.assertEqual(row.tweet_url, 'https://x.com/bob/status/444')

    def test_only_engagement_operations_are_reported(self):
        self._task('like', {'post_id': '1'})
        self._task('follow', {'screen_name': 'x'})
        self._task('send_dm', {'recipient_id': '9'})
        rows = self.Report.search([('account_id', '=', self.account.id)])
        self.assertEqual(set(rows.mapped('operation')), {'like'})

    def test_all_four_operations_group_by_group(self):
        for op in ('like', 'repost', 'bookmark', 'comment'):
            self._task(op, {'post_id': '900'}, group_id=self.group.id)
        rows = self.Report.search([('group_id', '=', self.group.id)])
        self.assertEqual(
            set(rows.mapped('operation')),
            {'like', 'repost', 'bookmark', 'comment'})
        self.assertEqual(sum(rows.mapped('operation_count')), 4)

    def test_action_and_menu_wired(self):
        action = self.env.ref('x_account.action_x_account_operation_report')
        self.assertEqual(action.res_model, 'x.account.operation.report')
        self.env.ref('x_account.menu_x_account_operation_report')
        self.env.ref('x_account.x_account_operation_report_view_pivot')
        self.env.ref('x_account.x_account_operation_report_view_graph')
