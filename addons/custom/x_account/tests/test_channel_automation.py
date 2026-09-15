from odoo import fields
from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestChannelAutomationArchivedScope(XAccountTestBase):
    """Archiving an account or a group (chat) channel must stop the
    message-model automation rules (like/repost/comment/bookmark/...) from
    running any task for it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def _make_account(self, handle, status='active'):
        return self.env['social.account'].create({
            'name': handle,
            'media_id': self.twitter_media.id,
            'social_account_handle': handle,
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
            'x_connection_status': status,
        })

    def _make_channel(self, account):
        return self.env['discuss.channel'].create({
            'channel_type': 'x',
            'x_account_id': account.id,
            'x_conversation_id': 'cv-%s' % account.id,
            'name': 'X chat',
        })

    def _make_message(self, account, channel, body='Check https://x.com/status/123456789'):
        return self.env['x.message'].create({
            'channel_id': channel.id,
            'account_id': account.id,
            'direction': 'inbound',
            'external_id': 'evt-%s' % channel.id,
            'body_plain': body,
            'external_created_at': fields.Datetime.now(),
        })

    def test_archived_account_does_not_run_like(self):
        account = self._make_account('archived_like')
        msg = self._make_message(account, self._make_channel(account))
        account.write({'active': False})
        msg._run_channel_like()
        self.assertFalse(self.env['x.account.task'].search([
            ('operation', '=', 'like'),
            ('account_id', '=', account.id),
        ]))

    def test_archived_channel_does_not_run_repost(self):
        account = self._make_account('archived_channel')
        channel = self._make_channel(account)
        msg = self._make_message(account, channel)
        channel.write({'active': False})
        msg._run_channel_repost()
        self.assertFalse(self.env['x.account.task'].search([
            ('operation', '=', 'repost'),
            ('account_id', '=', account.id),
        ]))

    def test_archived_account_does_not_fallback_to_company_account(self):
        """Regression: an archived account must stop the automation entirely
        instead of silently rerouting it to another company X account."""
        archived = self._make_account('archived_no_fallback')
        self._make_account('fallback_target', status='active')
        msg = self._make_message(archived, self._make_channel(archived))
        archived.write({'active': False})
        msg._run_channel_like()
        self.assertFalse(self.env['x.account.task'].search([
            ('operation', '=', 'like'),
        ]))

    def test_archived_channel_skips_send_dm(self):
        account = self._make_account('archived_dm', status='active')
        channel = self._make_channel(account)
        msg = self._make_message(account, channel)
        channel.write({'active': False})
        self.assertFalse(msg._run_channel_send_dm(text='Hi'))
        self.assertFalse(self.env['x.account.task'].search([
            ('account_id', '=', account.id),
        ]))