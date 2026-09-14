from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestXMessageAgeMinutes(XAccountTestBase):
    """x.message.age_minutes: a searchable received-time filter.

    Automation rules match through ``Model.search(filter_domain)`` (time
    triggers) and ``filtered_domain`` (create/write triggers), so the field is
    searchable without being stored: "now" is resolved when the filter runs.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_account.dev_encryption_key', 'age-test-key')
        cls.account = cls.env['social.account'].create({
            'name': 'age_filter',
            'media_id': cls.env.ref('social_twitter.social_media_twitter').id,
            'social_account_handle': 'age_filter',
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
            'x_connection_status': 'new',
        })
        cls.channel = cls.env['discuss.channel'].create({
            'channel_type': 'x',
            'x_account_id': cls.account.id,
            'x_conversation_id': 'age-conv',
            'name': 'Age Filter',
        })

    def _message(self, minutes_ago, external_id):
        return self.env['x.message'].create({
            'channel_id': self.channel.id,
            'account_id': self.account.id,
            'direction': 'inbound',
            'external_id': external_id,
            'body_plain': 'https://x.com/someone/status/123',
            'external_created_at': fields.Datetime.now() - timedelta(minutes=minutes_ago),
        })

    def test_field_is_searchable_without_being_stored(self):
        field = self.env['x.message']._fields['age_minutes']
        self.assertFalse(field.store)
        self.assertEqual(field.search, '_search_age_minutes')
        self.assertTrue(field.get_description(self.env, ['searchable'])['searchable'])

    def test_compute_counts_minutes_since_received(self):
        self.assertAlmostEqual(self._message(30, 'age-compute').age_minutes, 30, delta=1)

    def test_compute_falls_back_to_create_date(self):
        message = self._message(30, 'age-compute-fallback')
        message.write({'external_created_at': False})
        self.assertAlmostEqual(message.age_minutes, 0, delta=1)

    def test_search_older_than(self):
        old = self._message(30, 'age-old')
        recent = self._message(2, 'age-recent')
        found = self.env['x.message'].search([('age_minutes', '>=', 10)])
        self.assertIn(old, found)
        self.assertNotIn(recent, found)

    def test_search_younger_than(self):
        old = self._message(30, 'age-young-old')
        recent = self._message(2, 'age-young-recent')
        found = self.env['x.message'].search([('age_minutes', '<=', 10)])
        self.assertIn(recent, found)
        self.assertNotIn(old, found)

    def test_search_inverts_strict_operators(self):
        message = self._message(30, 'age-strict')
        self.assertIn(message, self.env['x.message'].search([('age_minutes', '>', 10)]))
        self.assertNotIn(message, self.env['x.message'].search([('age_minutes', '<', 10)]))

    def test_search_uses_receipt_when_external_timestamp_missing(self):
        message = self._message(30, 'age-no-external')
        message.write({'external_created_at': False})
        self.assertIn(message, self.env['x.message'].search([('age_minutes', '<=', 10)]))
        self.assertNotIn(message, self.env['x.message'].search([('age_minutes', '>=', 10)]))

    def test_automation_rule_filter_keeps_only_old_messages(self):
        """A rule filter on age_minutes is applied by base.automation."""
        old = self._message(30, 'age-rule-old')
        recent = self._message(2, 'age-rule-recent')
        rule = self.env['base.automation'].create({
            'name': 'Age filter (test)',
            'model_id': self.env.ref('x_account.model_x_message').id,
            'trigger': 'on_time_created',
        })
        rule.write({'filter_domain': "[('age_minutes', '>=', 5)]"})
        passed = rule._filter_post(old | recent)[0]
        self.assertIn(old, passed)
        self.assertNotIn(recent, passed)
