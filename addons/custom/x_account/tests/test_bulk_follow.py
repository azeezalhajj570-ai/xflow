import json
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase
from odoo.addons.x_account.services.providers.session_web import SessionWebProvider


@tagged('post_install', '-at_install', 'x_account')
class TestXBulkFollow(XAccountTestBase):
    """x.follow.composer: bulk-follow wizard for X group members."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        for xmlid in (
            'x_account.base_automation_x_task_follow',
        ):
            rule = cls.env.ref(xmlid)
            rule.write({'active': False})
            if not hasattr(cls, '_automation_xmlids'):
                cls._automation_xmlids = []
            cls._automation_xmlids.append(rule)

    @classmethod
    def tearDownClass(cls):
        for rule in reversed(getattr(cls, '_automation_xmlids', [])):
            rule.write({'active': True})
        super().tearDownClass()

    def _make_account(self, handle, status='new'):
        return self.env['social.account'].create({
            'name': handle,
            'media_id': self.twitter_media.id,
            'social_account_handle': handle,
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
            'x_connection_status': status,
        })

    def _make_group_channel(self, account, members):
        channel = self.env['discuss.channel'].create({
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': 'g-bulk-1',
            'name': 'X Group',
        })
        self.env['discuss.channel.member'].create([
            {'channel_id': channel.id, 'partner_id': m.id}
            for m in members
        ])
        return channel

    def _make_member(self, handle, username=None):
        return self.env['res.partner'].create({
            'name': handle,
            'x_user_id': handle,
            'x_username': username,
        })

    def test_channel_action_bulk_follow_opens_composer(self):
        account = self._make_account('follow_page')
        channel = self.env['discuss.channel'].create({
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': 'g-bulk-2',
            'name': 'X Group',
        })
        action = channel.action_bulk_follow()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'x.follow.composer')
        self.assertEqual(action['context']['default_channel_id'], channel.id)
        self.assertEqual(action['target'], 'new')

    def test_channel_action_bulk_follow_rejects_non_x(self):
        channel = self.env['discuss.channel'].create({
            'channel_type': 'channel',
            'name': 'Internal',
        })
        with self.assertRaises(ValueError):
            channel.action_bulk_follow()

    def test_default_get_prefills_members_with_username(self):
        account = self._make_account('follow_default')
        m1 = self._make_member('u1', username='user_one')
        m2 = self._make_member('u2', username=None)
        channel = self._make_group_channel(account, m1 | m2)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({})
        self.assertEqual(wizard.channel_id.id, channel.id)
        self.assertEqual(wizard.member_ids, m1)
        self.assertEqual(wizard.cooldown_sec, 10)

    def test_action_follow_enqueues_one_task_per_member_staggered(self):
        account = self._make_account('follow_enqueue')
        m1 = self._make_member('u1', username='user_one')
        m2 = self._make_member('u2', username='user_two')
        m3 = self._make_member('u3', username='user_three')
        channel = self._make_group_channel(account, m1 | m2 | m3)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'cooldown_sec': 30})

        result = wizard.action_follow()

        self.assertEqual(result['params']['type'], 'success')
        tasks = self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', 'follow'),
        ])
        self.assertEqual(len(tasks), 3)
        usernames = {
            json.loads(t.task_context)['screen_name']
            for t in tasks
        }
        self.assertEqual(usernames, {'user_one', 'user_two', 'user_three'})
        retries = sorted(tasks.mapped('next_retry_at'))
        self.assertEqual(len(set(retries)), 3)
        self.assertEqual(retries[0] < retries[1] < retries[2], True)

    def test_action_follow_unstaggered_when_cooldown_zero(self):
        account = self._make_account('follow_zerocd')
        m1 = self._make_member('u1', username='user_one')
        m2 = self._make_member('u2', username='user_two')
        channel = self._make_group_channel(account, m1 | m2)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'cooldown_sec': 0})

        result = wizard.action_follow()

        self.assertEqual(result['params']['type'], 'success')
        tasks = self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', 'follow'),
        ])
        self.assertEqual(len(tasks), 2)
        self.assertEqual(
            len(set(tasks.mapped('next_retry_at'))), 1)

    def test_action_follow_only_follows_selected_members(self):
        account = self._make_account('follow_skipped')
        m1 = self._make_member('u1', username='user_one')
        m2 = self._make_member('u2', username='user_two')
        channel = self._make_group_channel(account, m1 | m2)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({})
        wizard.write({'member_ids': [(6, 0, [m1.id])]})

        result = wizard.action_follow()

        self.assertEqual(result['params']['type'], 'success')
        tasks = self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', 'follow'),
        ])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(
            json.loads(tasks.task_context)['screen_name'], 'user_one')

    def test_action_follow_members_without_username_skipped(self):
        account = self._make_account('follow_nousername')
        m1 = self._make_member('u1', username='user_one')
        m2 = self._make_member('u2', username=None)
        channel = self._make_group_channel(account, m1 | m2)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'member_ids': [(6, 0, [m1.id, m2.id])]})

        result = wizard.action_follow()

        self.assertEqual(result['params']['type'], 'success')
        tasks = self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', 'follow'),
        ])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(
            json.loads(tasks.task_context)['screen_name'], 'user_one')

    def test_action_follow_no_members_warns(self):
        account = self._make_account('follow_nomembers')
        channel = self._make_group_channel(
            account, self.env['res.partner'])
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({})

        result = wizard.action_follow()

        self.assertEqual(result['params']['type'], 'warning')

    def test_action_follow_disabled_account_warns(self):
        account = self._make_account('follow_disabled', status='disabled')
        m1 = self._make_member('u1', username='user_one')
        channel = self._make_group_channel(account, m1)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({})

        result = wizard.action_follow()

        self.assertEqual(result['params']['type'], 'danger')
        self.assertFalse(
            self.env['x.account.task'].search([('account_id', '=', account.id)]))

    def test_enqueued_tasks_execute_via_provider(self):
        account = self._make_account('follow_provider')
        m1 = self._make_member('u1', username='user_one')
        channel = self._make_group_channel(account, m1)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'cooldown_sec': 0})
        wizard.action_follow()
        task = self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', 'follow'),
        ], limit=1)
        self.assertEqual(task.status, 'pending')
        with patch.object(SessionWebProvider, 'follow',
                          return_value={'screen_name': 'user_one', 'followed': True}):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'success')