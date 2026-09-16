from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.x_account.tests.common import XAccountTestBase
from odoo.addons.x_account.services.providers.session_web import SessionWebProvider


@tagged('post_install', '-at_install', 'x_account')
class TestFetchGroupsNotSupported(XAccountTestBase):
    """Unsupported group-fetch must not surface as a 500.

    The 'Fetch Groups' / 'Fetch Group Messages' server actions are bound to
    every social.account, but only providers implementing `fetch_groups` /
    `fetch_group_messages` (e.g. OmniX) support them. The UI click (dialog
    context) must show a warning notification; only programmatic callers should
    still see NotImplementedError.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def _make_account(self):
        # session_web has no fetch_groups/fetch_group_messages -> unsupported.
        account = self.env['social.account'].create({
            'name': 'Unsupported Groups X Account',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'groupsnouser',
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
            'x_encryption_code': 'test-pin',
        })
        # Restore needs no network; keep the provider uncalled by validate.
        return account

    def test_fetch_groups_unsupported_returns_warning_in_dialog(self):
        account = self._make_account()
        action = account.with_context(dialog=True).action_fetch_groups()
        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'warning')
        self.assertIn('does not support fetching groups', action['params']['message'])

    def test_fetch_groups_unsupported_raises_outside_dialog(self):
        account = self._make_account()
        with self.assertRaises(NotImplementedError):
            account.action_fetch_groups()

    def test_fetch_group_messages_unsupported_returns_warning_in_dialog(self):
        account = self._make_account()
        action = account.with_context(dialog=True).action_fetch_group_messages()
        self.assertEqual(action['params']['type'], 'warning')
        self.assertIn('does not support fetching group messages', action['params']['message'])

    def test_supported_provider_returns_success_dialog(self):
        account = self._make_account()
        with patch.object(SessionWebProvider, 'fetch_groups', create=True,
                          return_value={'groups': 3, 'created': 1, 'updated': 2,
                                        'members': 7}):
            action = account.with_context(dialog=True).action_fetch_groups()
            self.assertEqual(action['params']['type'], 'success')
            self.assertIn('Groups: 3, created: 1, updated: 2, members: 7', action['params']['message'])


@tagged('post_install', '-at_install', 'x_account')
class TestFetchGroupsProviderRouting(XAccountTestBase):
    """Group reads prefer the event provider (official X Chat API).

    XChat (``g``-prefixed) groups are only enumerated by the official X Chat
    API; a provider like GetXAPI reads the legacy DM inbox and never returns
    them. The group actions must therefore dispatch to a provider that
    implements the read instead of blindly to the configured action provider.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def _make_account(self):
        return self.env['social.account'].create({
            'name': 'Group Routing X Account',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'grouprouting',
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
        })

    def test_fetch_groups_prefers_event_provider(self):
        account = self._make_account()
        event_provider = MagicMock()
        event_provider.fetch_groups.return_value = {
            'groups': 1, 'created': 1, 'updated': 0, 'members': 2}
        action_provider = MagicMock()
        action_provider.fetch_groups.return_value = {
            'groups': 0, 'created': 0, 'updated': 0, 'members': 0}
        with patch.object(type(account), 'get_event_provider',
                          return_value=event_provider), \
                patch.object(type(account), 'get_action_provider',
                             return_value=action_provider):
            action = account.with_context(dialog=True).action_fetch_groups()
        event_provider.fetch_groups.assert_called_once()
        action_provider.fetch_groups.assert_not_called()
        self.assertIn('Groups: 1', action['params']['message'])

    def test_fetch_groups_falls_back_to_action_provider(self):
        account = self._make_account()
        event_provider = MagicMock(spec=[])
        action_provider = MagicMock()
        action_provider.fetch_groups.return_value = {
            'groups': 2, 'created': 0, 'updated': 2, 'members': 0}
        with patch.object(type(account), 'get_event_provider',
                          return_value=event_provider), \
                patch.object(type(account), 'get_action_provider',
                             return_value=action_provider):
            action = account.with_context(dialog=True).action_fetch_groups()
        action_provider.fetch_groups.assert_called_once()
        self.assertIn('Groups: 2', action['params']['message'])

    def test_fetch_groups_falls_back_when_event_provider_unavailable(self):
        account = self._make_account()
        action_provider = MagicMock()
        action_provider.fetch_groups.return_value = {
            'groups': 3, 'created': 0, 'updated': 3, 'members': 0}
        with patch.object(type(account), 'get_event_provider',
                          side_effect=UserError('no credentials')), \
                patch.object(type(account), 'get_action_provider',
                             return_value=action_provider):
            action = account.with_context(dialog=True).action_fetch_groups()
        action_provider.fetch_groups.assert_called_once()
        self.assertIn('Groups: 3', action['params']['message'])

    def test_fetch_group_messages_prefers_event_provider(self):
        account = self._make_account()
        event_provider = MagicMock()
        event_provider._needs_encryption_code = False
        event_provider.fetch_group_messages.return_value = {
            'groups': 1, 'messages': 5, 'failures': 0}
        action_provider = MagicMock()
        action_provider._needs_encryption_code = False
        action_provider.fetch_group_messages.return_value = {
            'groups': 0, 'messages': 0, 'failures': 0}
        with patch.object(type(account), 'get_event_provider',
                          return_value=event_provider), \
                patch.object(type(account), 'get_action_provider',
                             return_value=action_provider):
            action = account.with_context(dialog=True).action_fetch_group_messages()
        event_provider.fetch_group_messages.assert_called_once()
        action_provider.fetch_group_messages.assert_not_called()
        self.assertIn('messages: 5', action['params']['message'])

    def test_fetch_groups_provider_error_shows_danger_notification(self):
        """A failing provider (e.g. Chat API 503) must not surface as a 500."""
        account = self._make_account()
        event_provider = MagicMock()
        event_provider.fetch_groups.side_effect = Exception('Service Unavailable')
        with patch.object(type(account), 'get_event_provider',
                          return_value=event_provider):
            action = account.with_context(dialog=True).action_fetch_groups()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'danger')
        self.assertIn('Service Unavailable', action['params']['message'])

    def test_fetch_groups_provider_error_raises_outside_dialog(self):
        account = self._make_account()
        event_provider = MagicMock()
        event_provider.fetch_groups.side_effect = Exception('Service Unavailable')
        with patch.object(type(account), 'get_event_provider',
                          return_value=event_provider):
            with self.assertRaises(Exception):
                account.action_fetch_groups()
