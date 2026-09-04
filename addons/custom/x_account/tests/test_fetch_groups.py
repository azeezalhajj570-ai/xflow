from unittest.mock import patch

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