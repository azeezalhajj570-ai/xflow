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


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestSubscriptionEventTracking(XAccountTwitterTestBase):
    """Which events an account subscribes to is tracked in the chatter, so the
    subscription history stays auditable."""

    def _flush_tracking(self):
        self.env.flush_all()
        self.env.cr.precommit.run()

    def test_subscription_events_are_tracked(self):
        media = self.env.ref('social_twitter.social_media_twitter')
        event_type = self.env['x.subscription.event.type'].search([], limit=1)
        self.assertTrue(event_type, 'no subscription event types loaded')
        account = self.env['social.account'].create({
            'name': 'Tracked Subs',
            'media_id': media.id,
            'social_account_handle': 'tracked_subs',
        })
        self.assertTrue(getattr(
            account._fields['x_subscription_event_ids'], 'tracking', None))
        self._flush_tracking()
        account.write({'x_subscription_event_ids': [(4, event_type.id)]})
        self._flush_tracking()
        account.invalidate_recordset()
        values = account.message_ids.tracking_value_ids.filtered(
            lambda v: v.field_id.name == 'x_subscription_event_ids')
        self.assertTrue(values, 'subscription events change was not logged')


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestResubscribeOnUnarchive(XAccountTwitterTestBase):
    """Unarchiving an X account must re-create the XAA subscriptions that
    archiving pruned, otherwise the account comes back silent: X no longer
    delivers its DM/chat events and nothing re-subscribes it until the next
    self-heal sweep."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env['ir.config_parameter'].sudo()
        icp.set_param('x_account.event_provider', 'official')
        icp.set_param('x_account.action_provider', 'getxapi')
        cls.media = cls.env.ref('social_twitter.social_media_twitter')

    def _account(self, handle, user_id):
        """Create the account with subscription side effects muted, but return
        it in the plain context so the writes under test run the real paths."""
        account = self.env['social.account'].with_context(
            x_skip_subscription_sync=True).create({
                'name': handle,
                'media_id': self.media.id,
                'social_account_handle': handle,
                'twitter_user_id': user_id,
                'x_connection_status': 'active',
                'x_oauth2_access_token': 'fake-at',
            })
        return self.env['social.account'].browse(account.id)

    def _archived_account(self, handle, user_id):
        account = self._account(handle, user_id)
        account.with_context(x_skip_subscription_sync=True).write(
            {'active': False})
        return account

    def test_unarchive_resubscribes(self):
        account = self._archived_account('resub_acc', '420000420000420000')
        self.assertFalse(account.active)
        with patch.object(
                TwitterProvider, 'subscribe_account',
                return_value={'created': 2}) as subscribe:
            account.write({'active': True})
        self.assertTrue(account.active)
        subscribe.assert_called_once()
        self.assertEqual(subscribe.call_args.args[0].id, account.id)

    def test_write_active_on_an_active_account_does_not_resubscribe(self):
        """Only a real False -> True transition resubscribes; other writes that
        carry active must not hit the X API."""
        account = self._account('already_on', '430000430000430000')
        with patch.object(
                TwitterProvider, 'subscribe_account',
                return_value={}) as subscribe:
            account.write({'active': True})
            account.write({'name': 'already_on renamed'})
        subscribe.assert_not_called()

    def test_unarchive_is_not_blocked_by_a_subscribe_failure(self):
        """A failing X API call must not leave the account archived."""
        account = self._archived_account('resub_boom', '440000440000440000')
        with patch.object(
                TwitterProvider, 'subscribe_account',
                side_effect=Exception('boom')):
            account.write({'active': True})
        self.assertTrue(account.active)

    def test_archive_then_unarchive_round_trip(self):
        """Archive prunes the local rows, unarchive re-creates them."""
        account = self._account('round_trip', '450000450000450000')
        subs = self.env['x.twitter.subscription'].sudo()
        subs.create({
            'account_id': account.id,
            'event_type': 'dm.received',
            'subscription_id': 'sub-rt',
            'state': 'active',
        })
        with patch.object(
                TwitterWebhook, 'list_subscriptions',
                return_value={'data': []}), patch.object(
                TwitterWebhook, 'delete_subscription', return_value={}):
            account.write({'active': False})
        self.assertFalse(subs.search_count([('account_id', '=', account.id)]))
        with patch.object(
                TwitterProvider, 'subscribe_account',
                return_value={'created': 1}) as subscribe:
            account.write({'active': True})
        subscribe.assert_called_once()
