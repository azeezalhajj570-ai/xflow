from unittest.mock import Mock, patch

from odoo.tests import tagged

from odoo.addons.x_account_twitter.services.twitter_provider import TwitterProvider
from odoo.addons.x_account_twitter.services.twitter_webhook import TwitterWebhook

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


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestPruneXSubscriptionsOnArchive(XAccountTwitterTestBase):
    """Archiving an X account must delete its XAA subscriptions (X-side and
    local rows) so X stops delivering undeliverable DM/chat events to the
    webhook — archiving alone does not remove the X-side subscription."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env['ir.config_parameter'].sudo()
        icp.set_param('x_account.event_provider', 'official')
        icp.set_param('x_account.action_provider', 'getxapi')
        cls.media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Archive Prune Account',
            'media_id': cls.media.id,
            'social_account_handle': 'prune_acc',
            'twitter_user_id': '1234567890',
            'x_connection_status': 'active',
            'x_oauth2_access_token': 'fake-at',
            'x_oauth2_refresh_token': 'fake-rt',
        })
        cls.env['x.twitter.subscription'].sudo().create({
            'account_id': cls.account.id,
            'event_type': 'dm.received',
            'subscription_id': 'sub-dm',
            'state': 'active',
        })
        cls.env['x.twitter.subscription'].sudo().create({
            'account_id': cls.account.id,
            'event_type': 'chat.received',
            'subscription_id': 'sub-chat',
            'state': 'active',
        })

    def test_archive_write_prunes_local_rows_and_x_side_drift(self):
        """Archiving deletes every local subscription row plus any X-side
        subscription for the same user id that is not tracked locally (a
        listing entry re-aliasing a local sub must not be double-deleted)."""
        listing = {'data': [
            {'id': 'sub-dm', 'filter': {'user_id': self.account.twitter_user_id}},
            {'id': 'sub-stray', 'filter': {'user_id': self.account.twitter_user_id}},
        ]}
        with patch.object(
                TwitterWebhook, 'list_subscriptions',
                return_value=listing), patch.object(
                TwitterWebhook, 'delete_subscription', return_value={}) as delete:
            self.account.write({'active': False})
        self.assertFalse(self.account.active)
        deleted = {call.args[0] for call in delete.call_args_list}
        self.assertEqual(deleted, {'sub-dm', 'sub-chat', 'sub-stray'})
        self.assertFalse(self.env['x.twitter.subscription'].sudo().search_count(
            [('account_id', '=', self.account.id)]))

    def test_archive_write_webhook_failure_still_archives(self):
        """A transient X API failure during the archive prune must not block
        the archive (the self-heal cron retries the leftover subscription)."""
        with patch.object(
                TwitterWebhook, 'list_subscriptions',
                return_value={'data': []}), patch.object(
                TwitterWebhook, 'delete_subscription',
                side_effect=Exception('boom')):
            self.account.write({'active': False})
        self.assertFalse(self.account.active)
        self.assertFalse(self.env['x.twitter.subscription'].sudo().search_count(
            [('account_id', '=', self.account.id)]))

    def test_prune_archived_removes_x_side_only_subscription(self):
        """Accounts archived before this fix carried no local rows; the
        self-heal pass must still find their live X-side subscription via the
        listing (matched on filter.user_id) and delete it."""
        drift = self.env['social.account'].with_context(
            x_skip_subscription_sync=True).create({
                'name': 'Drift Account',
                'media_id': self.media.id,
                'social_account_handle': 'drift_acc',
                'twitter_user_id': '555000555000555000',
                'x_connection_status': 'active',
                'x_oauth2_access_token': 'fake-at',
            })
        drift.with_context(x_skip_subscription_sync=True).write(
            {'active': False})
        listing = {'data': [
            {'id': 'sub-drift', 'filter': {'user_id': '555000555000555000'}},
            {'id': 'sub-other', 'filter': {'user_id': '999000999000999000'}},
        ]}
        with patch.object(
                TwitterWebhook, 'list_subscriptions',
                return_value=listing), patch.object(
                TwitterWebhook, 'delete_subscription', return_value={}) as delete:
            result = self.env['social.account'].sudo() \
                ._prune_archived_x_subscriptions()
        deleted = {call.args[0] for call in delete.call_args_list}
        self.assertIn('sub-drift', deleted)
        self.assertNotIn('sub-other', deleted)
        self.assertEqual(result['deleted'], 1)

    def test_subscribe_all_skips_archived_accounts(self):
        """The self-heal cron must never re-create subscriptions for an
        archived account, or it would resurrect the dead-event loop."""
        archived = self.env['social.account'].with_context(
            x_skip_subscription_sync=True).create({
                'name': 'Archived Skip Account',
                'media_id': self.media.id,
                'social_account_handle': 'skip_acc',
                'twitter_user_id': '111222333444555666',
                'x_connection_status': 'active',
                'x_oauth2_access_token': 'fake-at',
            })
        archived.with_context(x_skip_subscription_sync=True).write(
            {'active': False})
        provider = TwitterProvider(self.env, self.account)
        with patch.object(
                TwitterProvider, '_subscribe_account',
                return_value={}) as subscribe:
            provider._subscribe_all(service=Mock(), hook=Mock())
        called_ids = [call.args[2].id for call in subscribe.call_args_list]
        self.assertIn(self.account.id, called_ids)
        self.assertNotIn(archived.id, called_ids)

    def test_ensure_webhook_prunes_archived_accounts(self):
        """The 30-minute self-heal cron prunes archived accounts' dead X-side
        subscriptions when webhooks are enabled and the app bearer is set."""
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('x_account_twitter.webhook_enabled', 'True')
        icp.set_param('x_account_twitter.app_bearer_token', 'test-bearer')
        icp.set_param('x_account_twitter.webhook_base_url', 'https://x.example.com')
        cron_target = self.env['social.account'].with_context(
            x_skip_subscription_sync=True).create({
                'name': 'Cron Prune Account',
                'media_id': self.media.id,
                'social_account_handle': 'cron_prune_acc',
                'twitter_user_id': '777000777000777000',
                'x_connection_status': 'active',
                'x_oauth2_access_token': 'fake-at',
            })
        cron_target.with_context(x_skip_subscription_sync=True).write(
            {'active': False})
        with patch.object(
                TwitterWebhook, 'list_subscriptions',
                return_value={'data': [
                    {'id': 'sub-cron',
                     'filter': {'user_id': '777000777000777000'}},
                ]}), patch.object(
                TwitterWebhook, 'delete_subscription',
                return_value={}) as delete, patch.object(
                TwitterProvider, 'register_webhook',
                return_value={'registered': True}) as register:
            result = self.env['social.account'].sudo() \
                ._ensure_x_webhook_subscriptions()
        self.assertIn('sub-cron', {call.args[0] for call in delete.call_args_list})
        self.assertTrue(register.called)
        self.assertEqual(result['archived_pruned']['deleted'], 1)
