from datetime import timedelta
from unittest.mock import patch
import time

from odoo import api, fields
from odoo.sql_db import Cursor
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

    def test_non_retryable_error_fails_immediately(self):
        """A permanent provider error (credits depleted, bad token) must not
        burn every attempt — it fails on the first try."""
        task = self._make_task(self.account_a, operation='get_conversations')

        class PermanentError(Exception):
            retryable = False

        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   side_effect=PermanentError('credits depleted')):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'failed')
        self.assertEqual(task.retry_count, 0)
        self.assertIn('credits depleted', task.error)

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

    def test_done_at_stamped_on_success(self):
        task = self._make_task(self.account_a)
        self.assertFalse(task.done_at)
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   return_value={'conversations': []}):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'success')
        self.assertTrue(task.done_at)

    def test_done_at_stamped_on_permanent_failure(self):
        task = self._make_task(self.account_a, max_attempts=1)

        class PermanentError(Exception):
            retryable = False

        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   side_effect=PermanentError('permanent')):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'failed')
        self.assertTrue(task.done_at)

    def test_done_at_stamped_on_cancel(self):
        task = self._make_task(self.account_a)
        task.action_cancel()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'cancelled')
        self.assertTrue(task.done_at)

    def test_retry_does_not_stamp_done_at(self):
        """An in-flight retry stays pending and keeps done_at empty."""
        task = self._make_task(self.account_a, max_attempts=3)
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   side_effect=RuntimeError('transient')):
            self.env['x.account.task']._process_queue()
        task.invalidate_recordset()
        self.assertEqual(task.status, 'pending')
        self.assertEqual(task.retry_count, 1)
        self.assertFalse(task.done_at)

    def test_task_targets_parsed_from_context(self):
        task = self._make_task(
            self.account_a, operation='repost',
            task_context='{"post_id": "191919", "screen_name": "@alice", '
                         '"channel_id": 7, "source": "channel_automation"}')
        self.assertEqual(task.target_post_id, '191919')
        self.assertEqual(task.target_screen_name, '@alice')
        self.assertEqual(task.source, 'channel_automation')
        empty = self._make_task(self.account_a, operation='follow')
        self.assertFalse(empty.target_post_id)
        self.assertFalse(empty.target_screen_name)
        self.assertFalse(empty.source)

    # ------------------------------------------------- concurrency / sharding

    def _second_cursor_env(self):
        """A second, independent DB connection to simulate a concurrent worker."""
        cr = self.env.registry.cursor()
        return cr, api.Environment(cr, self.env.uid, {})

    def test_claim_is_atomic_and_skip_locked(self):
        """Claiming uses the account advisory lock and FOR UPDATE SKIP LOCKED.

        A cross-connection row lock cannot be used to prove skipping here: the
        test's rows are uncommitted, so a second session cannot lock or even
        see them. The advisory-lock test below exercises the real cross-worker
        guard; this asserts claiming issues the non-blocking, atomic SQL.
        """
        self._make_task(self.account_a, operation='get_conversations')
        self.env.flush_all()
        queries = []
        original = Cursor.execute

        def spy(cursor, query, params=None):
            queries.append(str(query))
            return original(cursor, query, params)

        with patch.object(Cursor, 'execute', spy):
            self.env['x.account.task']._claim_account_tasks(
                self.account_a.id, 10, fields.Datetime.now())
        self.assertTrue(
            any('FOR UPDATE SKIP LOCKED' in q for q in queries), queries)
        self.assertTrue(
            any('pg_try_advisory_xact_lock' in q for q in queries), queries)

    def test_account_advisory_lock_enforces_single_flight(self):
        """A worker that does not own the account lock claims nothing for it."""
        self._make_task(self.account_a, operation='get_conversations')
        self.env.flush_all()
        namespace = self.env['x.account.task']._ACCOUNT_LOCK_NAMESPACE
        cr2, _env2 = self._second_cursor_env()
        try:
            cr2.execute(
                'SELECT pg_try_advisory_xact_lock(%s, %s)',
                (namespace, self.account_a.id))
            self.assertTrue(cr2.fetchone()[0])
            claimed = self.env['x.account.task']._claim_account_tasks(
                self.account_a.id, 10, fields.Datetime.now())
            self.assertFalse(claimed)
        finally:
            cr2.rollback()
            cr2.close()

    def test_shards_cover_every_account_once(self):
        """Running all shards processes every account exactly once."""
        accounts = self.env['social.account'].create([
            {'name': 'Shard Account %d' % i, 'media_id': self.twitter_media.id}
            for i in range(3)
        ])
        tasks = [self._make_task(a, operation='get_conversations')
                 for a in accounts]
        calls = []

        def fake_get_conversations(**kwargs):
            calls.append(kwargs)
            return {'conversations': []}

        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   side_effect=fake_get_conversations):
            self.env['x.account.task']._process_queue(shard=0, shards=2)
            self.env['x.account.task']._process_queue(shard=1, shards=2)
        self.assertEqual(len(calls), len(tasks))
        for task in tasks:
            task.invalidate_recordset()
            self.assertEqual(task.status, 'success')

    def test_shard_ignores_accounts_outside_its_bucket(self):
        """With shards=2, a shard only claims accounts whose id is in its bucket."""
        self._make_task(self.account_a, operation='get_conversations')
        target = self.account_a.id % 2
        with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   return_value={'conversations': []}):
            other = self.env['x.account.task']._claim_and_run(
                limit=100, shard=1 - target, shards=2)
            self.assertFalse(other)
            mine = self.env['x.account.task']._claim_and_run(
                limit=100, shard=target, shards=2)
            self.assertTrue(mine)

    def test_manual_path_never_commits(self):
        """The manual/test path (commit=False) must not commit the transaction."""
        self._make_task(self.account_a, operation='get_conversations')
        with patch.object(Cursor, 'commit') as commit_mock:
            with patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                       return_value={'conversations': []}):
                self.env['x.account.task']._process_queue()
        self.assertFalse(commit_mock.called)

    def test_cron_path_commits_each_chunk(self):
        """The cron path (commit=True) drains the account in bounded chunks and
        commits after each, so a sweep killed at ``limit_time_real`` loses at
        most one chunk instead of rolling the whole batch back to pending.

        ``_commit_queue_progress`` is substituted because the test framework
        forbids committing a test transaction; the loop's chunking is what is
        under test.
        """
        tasks = [self._make_task(self.account_a, operation='get_conversations')
                 for _ in range(5)]
        model = self.env['x.account.task']
        with patch.object(type(model), '_CLAIM_CHUNK_SIZE', 2), \
             patch.object(type(model), '_commit_queue_progress') as commit_mock, \
             patch('odoo.addons.x_account.services.providers.session_web.SessionWebProvider.get_conversations',
                   return_value={'conversations': []}):
            claimed = model._process_queue(commit=True)
        self.assertEqual(claimed, 5)
        # ceil(5 / 2) chunks -> one commit per chunk, never a single batch commit.
        self.assertEqual(commit_mock.call_count, 3)
        for task in tasks:
            task.invalidate_recordset()
            self.assertEqual(task.status, 'success')
