from datetime import timedelta
from unittest.mock import patch
import time

from odoo import fields
from odoo.tests import tagged
from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestXTaskQueue(XAccountTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account_a = cls.env['social.account'].create({
            'name': 'Account A',
            'media_id': cls.twitter_media.id,
        })
        cls.account_b = cls.env['social.account'].create({
            'name': 'Account B',
            'media_id': cls.twitter_media.id,
        })

    def _make_task(self, account, operation='get_conversations', **vals):
        base = {
            'account_id': account.id,
            'operation': operation,
            'next_retry_at': fields.Datetime.now(),
        }
        base.update(vals)
        return self.env['x.account.task'].create(base)

    def test_task_defaults(self):
        task = self._make_task(self.account_a)
        self.assertEqual(task.status, 'pending')
        self.assertEqual(task.retry_count, 0)
        self.assertEqual(task.max_attempts, 3)
        self.assertTrue(task.next_retry_at)

    def test_process_queue_success(self):
        task = self._make_task(self.account_a)
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   return_value={'conversations': []}):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'success')

    def test_process_queue_retry_then_fail(self):
        task = self._make_task(self.account_a, max_attempts=2)
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   side_effect=RuntimeError('rate_limit')):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'pending')
        self.assertEqual(task.retry_count, 1)
        self.assertIn('rate_limit', task.error)
        # Defeat backoff so the next sweep is due immediately.
        task.write({'next_retry_at': fields.Datetime.now()})
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   side_effect=RuntimeError('rate_limit')):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'failed')
        self.assertEqual(task.retry_count, 1)

    def test_retry_backoff_sets_next_retry(self):
        task = self._make_task(self.account_a, max_attempts=3, backoff_base=60)
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   side_effect=RuntimeError('fail')):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        expected = fields.Datetime.now() + timedelta(seconds=60)
        self.assertGreaterEqual(task.next_retry_at, expected - timedelta(seconds=5))

    def test_concurrency_per_account(self):
        """Tasks for the same account run sequentially (never two at once),
        but a single sweep must be able to claim and run ALL of them — the
        single-flight guard must not count this run's own in-flight claims
        (that throttled claiming to one task per account per minute and let a
        13k-event webhook backlog pile up)."""
        t1 = self._make_task(self.account_a, operation='get_conversations')
        t2 = self._make_task(self.account_a, operation='get_conversations')
        state = {'running': 0, 'max': 0, 'done': 0}

        def fake_get_conversations(**kwargs):
            state['running'] += 1
            state['max'] = max(state['max'], state['running'])
            time.sleep(0.01)
            state['running'] -= 1
            state['done'] += 1
            return {'conversations': []}

        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                    side_effect=fake_get_conversations):
            # One sweep drains both tasks, still never concurrently.
            self.env['x.account.task']._process_queue()
            self.assertEqual(state['max'], 1)
            self.assertEqual(state['done'], 2)
        t1.invalidate_recordset()
        t2.invalidate_recordset()
        self.assertEqual(t1.status, 'success')
        self.assertEqual(t2.status, 'success')

    def test_process_queue_drains_backlog_one_sweep(self):
        """Regression: claiming must not count the tasks claimed earlier in
        the same sweep as 'running' against the account — three due tasks for
        one account must all be claimed and executed by a single sweep."""
        tasks = [self._make_task(self.account_a, operation='get_conversations')
                 for _ in range(3)]
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                    return_value={'conversations': []}):
            claimed = self.env['x.account.task']._process_queue()
        self.assertEqual(claimed, 3)
        for task in tasks:
            task.invalidate_recordset()
            self.assertEqual(task.status, 'success')

    def test_process_queue_blocked_by_stale_running_task(self):
        """A stale 'running' task left behind by another worker still blocks
        new claims for that account (the guard's real purpose)."""
        stale = self._make_task(self.account_a, operation='get_conversations')
        stale.write({'status': 'running'})
        fresh = self._make_task(self.account_a, operation='get_conversations')
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                    return_value={'conversations': []}):
            claimed = self.env['x.account.task']._process_queue()
        self.assertEqual(claimed, 0)
        fresh.invalidate_recordset()
        self.assertEqual(fresh.status, 'pending')
