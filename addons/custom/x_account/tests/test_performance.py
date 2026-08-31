import gc
import time
import tracemalloc
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account.services.providers.session_web import SessionWebProvider
from odoo.addons.x_account.services.session_manager import XSessionManager
from odoo.addons.x_account.services.x_service import XService
from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account', 'perf')
class TestPerformance(XAccountTestBase):
    """T15: performance harness for 1/10/25/50 accounts.

    Measures session-restore, validation, and task throughput plus peak memory
    and DB query counts. No Chromium: every provider operation is mocked (or
    trivially succeeds) so we measure Odoo orchestration overhead, not network.
    """

    ACCOUNT_COUNTS = [1, 10, 25, 50]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'perf-test-key')

    def _make_account(self, i):
        acc = self.env['social.account'].create({
            'name': 'Perf Account %d' % i,
            'media_id': self.twitter_media.id,
        })
        # Store real encrypted sessions so restore does genuine decrypt work.
        XSessionManager.create_store(acc, 'auth_token=perf_a;ct0=perf_b', source='perf')
        return acc

    def _measure(self, fn):
        """Run fn once, returning (result, elapsed_seconds, peak_bytes, db_queries)."""
        gc.collect()
        tracemalloc.start()
        cr = self.env.cr
        queries_before = cr.sql_log_count
        t0 = time.perf_counter()
        try:
            result = fn()
        finally:
            elapsed = time.perf_counter() - t0
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        return result, elapsed, peak, cr.sql_log_count - queries_before

    def _sweep_metrics(self):
        """Return dict of max-time / peak-mem baselines per metric (empty now)."""
        return {}

    # ------------------------------------------------------------ session restore
    def test_session_restore_throughput(self):
        self._assert_restore_latency_ok(self.ACCOUNT_COUNTS)

    # ------------------------------------------------------------ validation
    def test_validation_throughput(self):
        self._assert_validation_latency_ok(self.ACCOUNT_COUNTS)

    # ------------------------------------------------------------ task throughput
    def test_task_throughput(self):
        self._assert_task_throughput_ok(self.ACCOUNT_COUNTS)

    # ------------------------------------------------------------ contention
    def test_contention_per_account(self):
        """Per-account single-flight holds at increasing account counts."""
        from odoo import fields as _fields
        N = 25
        accounts = [self._make_account(i) for i in range(N)]
        tasks = self.env['x.account.task']
        for acc in accounts:
            # two tasks per account to exercise single-flight contention
            for _ in range(2):
                tasks |= self.env['x.account.task'].create({
                    'account_id': acc.id,
                    'operation': 'like',
                })
        tasks.write({'next_retry_at': _fields.Datetime.now()})

        state = {'running': 0, 'max': 0, 'done': 0}

        def fake_like(**kwargs):
            state['running'] += 1
            state['max'] = max(state['max'], state['running'])
            time.sleep(0.001)
            state['running'] -= 1
            state['done'] += 1
            return {'tweet_id': kwargs.get('target_id') or ''}

        sweeps = 0
        with patch.object(SessionWebProvider, 'like', side_effect=fake_like):
            while self.env['x.account.task'].search_count([('status', '=', 'pending')]):
                self.env['x.account.task']._process_queue()
                sweeps += 1
                if sweeps > 2 * N + 10:
                    break
        self.assertEqual(state['done'], 2 * N)
        self.assertEqual(state['max'], 1)
        self.assertLess(sweeps, 2 * N + 1)

    # ------------------------------------------------------------ helpers
    def _expect_max_seconds(self, n):
        if n <= 1:
            return 2.0
        if n <= 10:
            return 5.0
        if n <= 25:
            return 10.0
        return 20.0

    def _assert_restore_latency_ok(self, counts):
        accounts = {n: [self._make_account(i) for i in range(n)] for n in counts}
        report = []
        for n in counts:
            accs = accounts[n]
            result, elapsed, peak, queries = self._measure(
                lambda accs=accs: [XService.get_provider(a) for a in accs]
            )
            report.append((n, elapsed, peak, queries))
            self.assertLessEqual(
                elapsed, self._expect_max_seconds(n),
                'session restore for %d accounts took %.2fs' % (n, elapsed))
        self._log_report('session-restore', report)

    def _assert_validation_latency_ok(self, counts):
        accounts = {n: [self._make_account(i) for i in range(n)] for n in counts}
        report = []
        for n in counts:
            accs = accounts[n]
            with patch.object(SessionWebProvider, 'validate_session',
                              return_value={'valid': True, 'reason': ''}):
                result, elapsed, peak, queries = self._measure(
                    lambda accs=accs: [XService.validate(a) for a in accs]
                )
            report.append((n, elapsed, peak, queries))
            self.assertLessEqual(
                elapsed, self._expect_max_seconds(n),
                'validation for %d accounts took %.2fs' % (n, elapsed))
        self._log_report('validation', report)

    def _assert_task_throughput_ok(self, counts):
        from odoo import fields as _fields
        report = []
        for n in counts:
            accs = [self._make_account(i) for i in range(n)]
            tasks = self.env['x.account.task'].create([
                {'account_id': accs[i % n].id, 'operation': 'like'} for i in range(n)
            ])
            tasks.write({'next_retry_at': _fields.Datetime.now()})
            with patch.object(SessionWebProvider, 'like', return_value={'tweet_id': 't'}):
                result, elapsed, peak, queries = self._measure(
                    lambda tasks=tasks: self.env['x.account.task']._process_queue()
                )
            report.append((n, elapsed, peak, queries))
            self.assertEqual(
                tasks.mapped('status'), ['success'] * n if n else [])
            self.assertLessEqual(
                elapsed, self._expect_max_seconds(n),
                'N=%d task queue sweep took %.2fs' % (n, elapsed))
        self._log_report('task-throughput', report)

    def _log_report(self, name, report):
        lines = [
            '\n=== PERF [%s] accounts size -> elapsed_s, peak_mb, db_queries ===' % name,
        ]
        for n, elapsed, peak, queries in report:
            lines.append(
                '  %4d -> %8.3fs  peak=%7.2f MB  queries=%d'
                % (n, elapsed, peak / (1024 * 1024), queries))
        import logging as _logging
        _logger = _logging.getLogger('odoo.addons.x_account.tests.test_performance')
        _logger.info('\n'.join(lines))
