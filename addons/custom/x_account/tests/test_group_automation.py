from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase
from odoo.addons.x_account.services.providers.session_web import SessionWebProvider


@tagged('post_install', '-at_install', 'x_account')
class TestXGroupAutomation(XAccountTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'group-test-key')
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        # Deactivate the real base_automation rule so tests exercise
        # `_enqueue_group_operation` in isolation (no double-enqueue on create).
        cls._automation = cls.env.ref('x_account.base_automation_x_group_action')
        cls._automation.write({'active': False})

    @classmethod
    def tearDownClass(cls):
        cls._automation.write({'active': True})
        super().tearDownClass()

    def _make_account(self, handle):
        return self.env['social.account'].create({
            'name': handle,
            'media_id': self.twitter_media.id,
            'social_account_handle': handle,
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
        })

    def _make_group(self, accounts, **vals):
        base = {
            'name': 'Test Group',
            'account_ids': [(6, 0, accounts.ids)],
            'actions': 'like',
            'auto_execute': True,
            'cooldown_sec': 0,
        }
        base.update(vals)
        return self.env['x.account.group'].create(base)

    def test_enqueue_creates_task_per_account(self):
        acc_a = self._make_account('user_a')
        acc_b = self._make_account('user_b')
        group = self._make_group(acc_a | acc_b)
        group._enqueue_group_operation(target_id='999')
        tasks = self.env['x.account.task'].search([
            ('group_id', '=', group.id),
            ('operation', '=', 'like'),
        ])
        self.assertEqual(len(tasks), 2)
        self.assertSetEqual(set(tasks.mapped('account_id.id')), {acc_a.id, acc_b.id})
        group.invalidate_recordset()
        self.assertTrue(group.last_executed_at)

    def test_paused_group_enqueues_nothing(self):
        acc = self._make_account('user_c')
        group = self._make_group(acc, paused=True)
        group._enqueue_group_operation(target_id='1')
        tasks = self.env['x.account.task'].search([('group_id', '=', group.id)])
        self.assertFalse(tasks)

    def test_auto_execute_false_enqueues_nothing(self):
        acc = self._make_account('user_d')
        group = self._make_group(acc, auto_execute=False)
        group._enqueue_group_operation(target_id='1')
        tasks = self.env['x.account.task'].search([('group_id', '=', group.id)])
        self.assertFalse(tasks)

    def test_disabled_account_skipped(self):
        acc_ok = self._make_account('user_e')
        acc_off = self._make_account('user_f')
        acc_off.write({'x_connection_status': 'disabled'})
        group = self._make_group(acc_ok | acc_off)
        group._enqueue_group_operation(target_id='1')
        tasks = self.env['x.account.task'].search([('group_id', '=', group.id)])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks.account_id.id, acc_ok.id)

    def test_cooldown_skips_recent_runs(self):
        acc = self._make_account('user_g')
        group = self._make_group(acc, cooldown_sec=3600)
        group._enqueue_group_operation(target_id='1')
        self.assertEqual(len(self.env['x.account.task'].search([('group_id', '=', group.id)])), 1)
        # Second enqueue within cooldown window is skipped for that account.
        group._enqueue_group_operation(target_id='1')
        tasks = self.env['x.account.task'].search([('group_id', '=', group.id)])
        self.assertEqual(len(tasks), 1)

    def test_enqueued_tasks_execute_via_provider(self):
        acc = self._make_account('user_h')
        group = self._make_group(acc)
        group._enqueue_group_operation(target_id='12345')
        task = self.env['x.account.task'].search([('group_id', '=', group.id)], limit=1)
        self.assertEqual(task.status, 'pending')
        with patch.object(SessionWebProvider, 'like',
                          return_value={'tweet_id': '12345', 'liked': True}):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'success')

    def test_enqueued_tasks_execute_via_omnix_provider(self):
        """Group tasks on an OmniX account dispatch through OmniXProvider."""
        from odoo.addons.x_account.services.providers.omnix import OmniXProvider
        from odoo.addons.x_account.services.session_manager import XSessionManager
        acc = self._make_account('user_omx')
        acc.write({'x_provider': 'omnix'})
        XSessionManager.create_store(acc, 'auth_token=omx-test-token')
        group = self._make_group(acc)
        group._enqueue_group_operation(target_id='777')
        task = self.env['x.account.task'].search([('group_id', '=', group.id)], limit=1)
        self.assertEqual(task.status, 'pending')
        with patch.object(OmniXProvider, 'like',
                          return_value={'tweet_id': '777', 'liked': True}):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'success')
