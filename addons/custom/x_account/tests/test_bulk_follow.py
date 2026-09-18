import json
from unittest.mock import patch

from odoo.exceptions import ValidationError
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
        self.assertEqual(wizard.min_delay_sec, 60)
        self.assertEqual(wizard.max_delay_sec, 300)

    def test_member_pool_excludes_self_no_username_and_followed(self):
        account = self._make_account('poolowner')
        account.write({'twitter_user_id': 'z99'})
        self_member = self._make_member('z99', username='poolowner')
        no_username = self._make_member('x2', username=None)
        already = self._make_member('x3', username='already_followed')
        target = self._make_member('x4', username='pool_target')
        channel = self._make_group_channel(
            account, self_member | no_username | already | target)
        account.write({'x_following_ids': [(6, 0, [already.id])]})
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({})
        self.assertEqual(wizard.member_pool_ids, target)
        self.assertEqual(wizard.member_ids, target)

    def test_action_follow_skips_self_and_already_followed(self):
        account = self._make_account('skipself')
        account.write({'twitter_user_id': 's1'})
        self_member = self._make_member('s1', username='skipself')
        already = self._make_member('x5', username='already_followed')
        target = self._make_member('x6', username='real_target')
        channel = self._make_group_channel(
            account, self_member | already | target)
        account.write({'x_following_ids': [(6, 0, [already.id])]})
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({
            'member_ids': [(6, 0, [self_member.id, already.id, target.id])],
        })

        result = wizard.action_follow()

        self.assertEqual(result['params']['type'], 'success')
        tasks = self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', 'follow'),
        ])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(
            json.loads(tasks.task_context)['screen_name'], 'real_target')

    def test_action_follow_enqueues_one_task_per_member_staggered(self):
        account = self._make_account('follow_enqueue')
        m1 = self._make_member('u1', username='user_one')
        m2 = self._make_member('u2', username='user_two')
        m3 = self._make_member('u3', username='user_three')
        channel = self._make_group_channel(account, m1 | m2 | m3)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'min_delay_sec': 30, 'max_delay_sec': 30})

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

    def test_action_follow_unstaggered_when_delay_zero(self):
        account = self._make_account('follow_zerocd')
        m1 = self._make_member('u1', username='user_one')
        m2 = self._make_member('u2', username='user_two')
        channel = self._make_group_channel(account, m1 | m2)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'min_delay_sec': 0, 'max_delay_sec': 0})

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
        ).create({'min_delay_sec': 0, 'max_delay_sec': 0})
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

    def test_executed_follow_marks_account_following(self):
        account = self._make_account('follow_marks')
        m1 = self._make_member('u1', username='user_one')
        m2 = self._make_member('u2', username='user_two')
        channel = self._make_group_channel(account, m1 | m2)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'min_delay_sec': 0, 'max_delay_sec': 0})
        wizard.action_follow()
        tasks = self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', 'follow'),
        ])
        self.assertEqual(len(tasks), 2)
        with patch.object(
            SessionWebProvider, 'follow',
            autospec=True,
            side_effect=lambda self_, screen_name=None, **kw: {
                'screen_name': screen_name, 'followed': True},
        ):
            # One bulk follow per sweep: two sweeps drain the two tasks.
            for _ in range(len(tasks)):
                self.env['x.account.task']._process_queue()
        for task in tasks:
            task.invalidate_recordset()
            self.assertEqual(task.status, 'success')
        self.assertEqual(account.x_following_ids, m1 | m2)

    def test_only_one_bulk_follow_per_sweep(self):
        account = self._make_account('follow_sweep')
        m1 = self._make_member('s1', username='sweep_one')
        m2 = self._make_member('s2', username='sweep_two')
        m3 = self._make_member('s3', username='sweep_three')
        channel = self._make_group_channel(account, m1 | m2 | m3)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'min_delay_sec': 0, 'max_delay_sec': 0})
        wizard.action_follow()
        tasks = self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', 'follow'),
        ])
        self.assertEqual(len(tasks), 3)

        with patch.object(
            SessionWebProvider, 'follow',
            autospec=True,
            side_effect=lambda self_, screen_name=None, **kw: {
                'screen_name': screen_name, 'followed': True},
        ):
            for expected in (1, 2, 3):
                claimed = self.env['x.account.task']._process_queue()
                tasks.invalidate_recordset()
                self.assertEqual(claimed, 1)
                self.assertEqual(
                    len(tasks.filtered(lambda t: t.status == 'success')),
                    expected)
                self.assertEqual(
                    len(tasks.filtered(lambda t: t.status == 'pending')),
                    3 - expected)

    def test_random_gaps_within_range_and_monotonic(self):
        account = self._make_account('follow_random')
        members = self.env['res.partner']
        for index in range(4):
            members |= self._make_member(
                'r%d' % index, username='rand_user_%d' % index)
        channel = self._make_group_channel(account, members)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'min_delay_sec': 30, 'max_delay_sec': 90})

        with patch(
            'odoo.addons.x_account.wizards.x_follow_composer.random.sample',
            side_effect=lambda population, k: list(population),
        ), patch(
            'odoo.addons.x_account.wizards.x_follow_composer.random.randint',
            side_effect=[40, 75, 55],
        ):
            wizard.action_follow()

        tasks = self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', 'follow'),
        ], order='next_retry_at asc')
        self.assertEqual(len(tasks), 4)
        contexts = [json.loads(t.task_context) for t in tasks]
        self.assertEqual(
            [c['scheduled_offset_sec'] for c in contexts], [0, 40, 115, 170])
        for context in contexts:
            self.assertEqual(context['min_delay_sec'], 30)
            self.assertEqual(context['max_delay_sec'], 90)
        offsets = [c['scheduled_offset_sec'] for c in contexts]
        for gap in [b - a for a, b in zip(offsets, offsets[1:])]:
            self.assertGreaterEqual(gap, 30)
            self.assertLessEqual(gap, 90)
        retries = tasks.mapped('next_retry_at')
        self.assertEqual(retries, sorted(retries))

    def test_gaps_are_not_constant(self):
        account = self._make_account('follow_varied')
        members = self.env['res.partner']
        for index in range(4):
            members |= self._make_member(
                'v%d' % index, username='varied_user_%d' % index)
        channel = self._make_group_channel(account, members)
        wizard = self.env['x.follow.composer'].with_context(
            active_model='discuss.channel', active_id=channel.id
        ).create({'min_delay_sec': 5, 'max_delay_sec': 500})

        with patch(
            'odoo.addons.x_account.wizards.x_follow_composer.random.sample',
            side_effect=lambda population, k: list(population),
        ), patch(
            'odoo.addons.x_account.wizards.x_follow_composer.random.randint',
            side_effect=[10, 20, 30],
        ):
            wizard.action_follow()

        tasks = self.env['x.account.task'].search([
            ('account_id', '=', account.id),
            ('operation', '=', 'follow'),
        ], order='next_retry_at asc')
        offsets = [
            json.loads(t.task_context)['scheduled_offset_sec'] for t in tasks]
        gaps = [b - a for a, b in zip(offsets, offsets[1:])]
        self.assertEqual(gaps, [10, 20, 30])
        self.assertGreater(len(set(gaps)), 1)

    def test_min_greater_than_max_raises(self):
        account = self._make_account('follow_badrange')
        m1 = self._make_member('b1', username='bad_range_user')
        channel = self._make_group_channel(account, m1)
        with self.assertRaises(ValidationError):
            self.env['x.follow.composer'].with_context(
                active_model='discuss.channel', active_id=channel.id
            ).create({'min_delay_sec': 100, 'max_delay_sec': 10})