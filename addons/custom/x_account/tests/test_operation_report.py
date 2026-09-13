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
        cls.channel = cls.env['discuss.channel'].create({
            'name': 'Report Channel',
            'channel_type': 'group',
            'x_conversation_id': 'g2090169325890269541',
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
        # Odoo defers pending writes until flush; the report is a SQL view, so
        # flush first to make sure it reads the just-written task values.
        self.env.flush_all()
        return self.Report.search([('id', '=', task.id)], limit=1)

    def test_view_exposes_channel_account_operation_and_link(self):
        task = self._task('like', {
            'post_id': '111', 'screen_name': 'alice',
            'channel_id': self.channel.id,
        })
        row = self._row(task)
        self.assertEqual(row.account_id, self.account)
        self.assertEqual(row.channel_id, self.channel)
        self.assertEqual(row.operation, 'like')
        self.assertEqual(row.tweet_id, '111')
        self.assertEqual(row.tweet_screen_name, 'alice')
        self.assertEqual(row.tweet_url, 'https://x.com/alice/status/111')
        self.assertEqual(row.operation_count, 1)

    def test_channel_links_to_x_chat(self):
        task = self._task('like', {
            'post_id': '111', 'channel_id': self.channel.id})
        row = self._row(task)
        self.assertEqual(
            row.channel_url,
            'https://x.com/i/chat/g2090169325890269541')
        self.assertIn(
            'href="https://x.com/i/chat/g2090169325890269541"',
            row.channel_link)
        self.assertIn('Report Channel', row.channel_link)

    def test_channel_without_conversation_has_no_link(self):
        task = self._task('like', {'post_id': '111', 'channel_id': 7})
        row = self._row(task)
        self.assertFalse(row.channel_url)
        self.assertFalse(row.channel_link)

    def test_nested_channel_context_uses_generic_link(self):
        task = self._task(
            'comment', {'post': {'post_id': '222'}, 'channel_id': 7})
        row = self._row(task)
        self.assertEqual(row.tweet_id, '222')
        self.assertEqual(row.channel_id.id, 7)
        self.assertEqual(row.tweet_url, 'https://x.com/i/web/status/222')

    def test_target_id_context(self):
        task = self._task('repost', {
            'target_id': '333', 'channel_id': self.channel.id})
        row = self._row(task)
        self.assertEqual(row.tweet_id, '333')
        self.assertEqual(row.channel_id, self.channel)
        self.assertEqual(row.tweet_url, 'https://x.com/i/web/status/333')

    def test_author_link_is_clickable_profile(self):
        task = self._task('like', {'post_id': '111', 'screen_name': 'alice'})
        row = self._row(task)
        self.assertEqual(row.author_url, 'https://x.com/alice')
        self.assertIn('href="https://x.com/alice"', row.author_link)
        self.assertIn('>alice<', row.author_link)

    def test_author_falls_back_to_numeric_user_id(self):
        task = self._task('like', {'post_id': '112', 'author_x_id': '4242424242'})
        row = self._row(task)
        self.assertEqual(row.tweet_screen_name, '4242424242')
        self.assertEqual(row.author_url, 'https://x.com/i/user/4242424242')
        self.assertIn('href="https://x.com/i/user/4242424242"', row.author_link)

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

    def test_all_four_operations_group_by_channel(self):
        for op in ('like', 'repost', 'bookmark', 'comment'):
            self._task(op, {'post_id': '900', 'channel_id': self.channel.id})
        rows = self.Report.search([('channel_id', '=', self.channel.id)])
        self.assertEqual(
            set(rows.mapped('operation')),
            {'like', 'repost', 'bookmark', 'comment'})
        self.assertEqual(sum(rows.mapped('operation_count')), 4)

    def test_received_at_comes_from_the_message(self):
        message = self.env['x.message'].create({
            'channel_id': self.channel.id,
            'account_id': self.account.id,
            'direction': 'inbound',
            'external_id': 'msg-111',
            'body_plain': 'https://x.com/alice/status/111',
            'external_created_at': '2026-09-13 20:00:00',
        })
        task = self._task(
            'like', {'post_id': '111', 'channel_id': self.channel.id})
        row = self._row(task)
        self.assertEqual(row.received_at, message.external_created_at)

    def test_default_order_is_latest_first(self):
        for external_id, tweet, created in (
                ('msg-old', '1001', '2026-09-13 10:00:00'),
                ('msg-new', '1002', '2026-09-13 12:00:00')):
            self.env['x.message'].create({
                'channel_id': self.channel.id,
                'account_id': self.account.id,
                'direction': 'inbound',
                'external_id': external_id,
                'body_plain': 'https://x.com/alice/status/%s' % tweet,
                'external_created_at': created,
            })
        old_task = self._task(
            'like', {'post_id': '1001', 'channel_id': self.channel.id})
        new_task = self._task(
            'like', {'post_id': '1002', 'channel_id': self.channel.id})
        self.env.flush_all()
        rows = self.Report.search(
            [('id', 'in', [old_task.id, new_task.id])])
        self.assertEqual(rows.ids, [new_task.id, old_task.id])

    def test_processing_time_is_done_minus_received(self):
        self.env['x.message'].create({
            'channel_id': self.channel.id,
            'account_id': self.account.id,
            'direction': 'inbound',
            'external_id': 'msg-pt',
            'body_plain': 'https://x.com/alice/status/555',
            'external_created_at': '2026-09-13 20:00:00',
        })
        task = self._task(
            'like', {'post_id': '555', 'channel_id': self.channel.id})
        task.write({'status': 'success', 'done_at': '2026-09-13 20:30:00'})
        row = self._row(task)
        self.assertAlmostEqual(row.processing_time, 0.5)

    def test_received_at_falls_back_to_task_create(self):
        task = self._task(
            'like', {'post_id': '999999999', 'channel_id': self.channel.id})
        row = self._row(task)
        self.assertEqual(row.received_at, task.create_date)

    def test_company_comes_from_the_account(self):
        company = self.env['res.company'].create({'name': 'Report Co B'})
        account = self.env['social.account'].create({
            'name': 'Other Account',
            'media_id': self.media.id,
            'company_id': company.id,
        })
        task = self.env['x.account.task'].create({
            'account_id': account.id,
            'operation': 'like',
            'task_context': json.dumps({'post_id': '999'}),
        })
        row = self.Report.search([('id', '=', task.id)])
        self.assertEqual(row.company_id, company)

    def test_multi_company_rule(self):
        rule = self.env.ref(
            'x_account.security_rule_x_account_operation_report_company')
        self.assertEqual(rule.model_id.model, 'x.account.operation.report')
        self.assertIn('company_id', rule.domain_force)
        self.assertFalse(rule.groups)

    def test_action_and_menu_wired(self):
        action = self.env.ref('x_account.action_x_account_operation_report')
        self.assertEqual(action.res_model, 'x.account.operation.report')
        self.assertIn("search_default_groupby_account': 1", action.context or '')
        self.assertIn('groupby_channel', action.context or '')
        menu = self.env.ref('x_account.menu_x_account_operation_report')
        self.assertEqual(
            menu.parent_id, self.env.ref('x_account.menu_x_account_reporting'))
        self.env.ref('x_account.x_account_operation_report_view_pivot')
        self.env.ref('x_account.x_account_operation_report_view_graph')
