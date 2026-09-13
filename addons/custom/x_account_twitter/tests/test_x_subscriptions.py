from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account_twitter.services.twitter_provider import TwitterProvider

from .common import XAccountTwitterTestBase


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestDeleteXSubscriptions(XAccountTwitterTestBase):
    """``action_delete_x_subscriptions`` must run on the official X provider.

    Subscriptions are an event-provider concern: even when the account's action
    provider is GetXAPI, deleting subscriptions goes through the official X API.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env['ir.config_parameter'].sudo()
        icp.set_param('x_account.event_provider', 'official')
        icp.set_param('x_account.action_provider', 'getxapi')
        cls.media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Subscriptions Account',
            'media_id': cls.media.id,
            'social_account_handle': 'subs_acc',
            'twitter_user_id': '1234567890',
            'x_oauth2_access_token': 'fake-at',
        })

    def test_delete_uses_official_event_provider(self):
        with patch.object(
                TwitterProvider, 'unsubscribe_all_events',
                return_value={'deleted': 2}) as mocked:
            action = self.account.action_delete_x_subscriptions()
        self.assertTrue(mocked.called)
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'success')
        self.assertIn('Deleted 2', action['params']['message'])

    def test_delete_without_user_id_warns_instead_of_skipping(self):
        self.account.twitter_user_id = False
        action = self.account.action_delete_x_subscriptions()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'warning')

    def test_delete_reports_failure(self):
        with patch.object(
                TwitterProvider, 'unsubscribe_all_events',
                side_effect=Exception('boom')):
            action = self.account.action_delete_x_subscriptions()
        self.assertEqual(action['params']['type'], 'danger')
        self.assertIn('boom', action['params']['message'])
