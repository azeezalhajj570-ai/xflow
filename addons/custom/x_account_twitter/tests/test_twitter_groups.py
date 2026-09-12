from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from odoo.addons.x_account_twitter.services.twitter_api_client import TwitterApiClient
from odoo.addons.x_account_twitter.services.twitter_errors import (
    TwitterAuthenticationError, TwitterPermissionError, TwitterNotFoundError,
    TwitterTemporaryError)
from odoo.addons.x_account_twitter.services.twitter_provider import TwitterProvider

from .common import XAccountTwitterTestBase


GROUP_ID = '999000999000999001'
OWNER_ID = '999000999000999000'
CHAT_GROUP_ID = 'g999000999000999001'
DIRECT_ID = '111-222'


def _chat_page():
    """One page of GET /2/chat/conversations: one XChat group + one 1:1."""
    return {
        'data': [
            {'id': CHAT_GROUP_ID, 'type': 'group', 'group_name': 'Design Team',
             'created_at': '2026-09-01T10:00:00.000Z',
             'member_ids': ['111', '222'], 'admin_ids': ['111'],
             'participant_ids': ['111', '222']},
            {'id': DIRECT_ID, 'type': 'direct',
             'participant_ids': ['111', '222']},
        ],
        'includes': {
            'users': [
                {'id': '111', 'name': 'Alice', 'username': 'alice'},
                {'id': '222', 'name': 'Bob', 'username': 'bob'},
            ],
        },
        'meta': {'has_more': False, 'result_count': 2},
    }


def _events_page():
    """One page of /2/dm_events: a group ParticipantsJoin + MessageCreate and a
    one-to-one MessageCreate."""
    return {
        'data': [
            {'id': 'ev1', 'event_type': 'ParticipantsJoin',
             'dm_conversation_id': GROUP_ID, 'sender_id': '111',
             'participant_ids': ['111', '222'], 'created_at': '2026-09-01T10:00:00Z'},
            {'id': 'ev2', 'event_type': 'MessageCreate',
             'dm_conversation_id': GROUP_ID, 'sender_id': '222',
             'text': 'hello group', 'created_at': '2026-09-01T10:01:00Z'},
            {'id': 'ev3', 'event_type': 'MessageCreate',
             'dm_conversation_id': DIRECT_ID, 'sender_id': '111',
             'text': 'direct hi', 'created_at': '2026-09-01T10:02:00Z'},
        ],
        'includes': {
            'users': [
                {'id': '111', 'name': 'Alice', 'username': 'alice'},
                {'id': '222', 'name': 'Bob', 'username': 'bob'},
            ],
        },
        'meta': {},
    }


def _chat_events_page():
    """One page of GET /2/chat/conversations/{id}/events: one plaintext
    MessageCreate + one encrypted event."""
    return {
        'data': [
            {'id': 'cm1', 'event_type': 'MessageCreate', 'sender_id': '111',
             'text': 'hello chat', 'created_at': '2026-09-01T11:00:00Z'},
            {'id': 'cm2', 'event_type': 'MessageCreate', 'sender_id': '222',
             'encoded_event': 'encrypted-blob', 'created_at': '2026-09-01T11:01:00Z'},
        ],
        'meta': {},
    }


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterGroups(XAccountTwitterTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def _make_account(self):
        return self.env['social.account'].create({
            'name': 'Group X Account',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'grpowner',
            'twitter_user_id': OWNER_ID,
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_oauth2_access_token': 'fake-at',
            'x_oauth2_refresh_token': 'fake-rt',
        })

    # ------------------------------------------------------------- chat API
    def test_fetch_groups_uses_chat_api_and_syncs_group_channel(self):
        account = self._make_account()
        with patch.object(TwitterApiClient, 'request',
                          return_value=_chat_page()) as mocked:
            result = account.with_context(dialog=True).action_fetch_groups()
        self.assertEqual(mocked.call_args.args[:2], ('GET', '/2/chat/conversations'))
        self.assertEqual(result['params']['type'], 'success')
        self.assertEqual(result['params']['message'],
                         'Groups: 2, created: 2, updated: 0, members: 3')

        channel = self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'x_group'),
            ('x_account_id', '=', account.id),
            ('x_conversation_id', '=', CHAT_GROUP_ID),
        ])
        self.assertTrue(channel)
        self.assertEqual(channel.name, 'Design Team')
        # 111 + 222 + owner + admin user (auto-added by mail).
        self.assertEqual(channel.x_group_member_count, 4)
        alice = self.env['res.partner'].sudo().search([('x_user_id', '=', '111')])
        bob = self.env['res.partner'].sudo().search([('x_user_id', '=', '222')])
        self.assertTrue(alice and bob)
        self.assertIn(alice.id, channel.x_group_member_ids.ids)
        self.assertIn(bob.id, channel.x_group_member_ids.ids)

    def test_fetch_groups_syncs_1to1_into_x_channel(self):
        """1:1 conversations must be synced into 'x' channels, not dropped."""
        account = self._make_account()
        with patch.object(TwitterApiClient, 'request', return_value=_chat_page()):
            account.action_fetch_groups()
        channel = self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'x'),
            ('x_account_id', '=', account.id),
            ('x_conversation_id', '=', DIRECT_ID),
        ])
        self.assertTrue(channel)
        self.assertEqual(channel.x_account_id.id, account.id)

    def test_fetch_groups_is_idempotent(self):
        """Re-syncing the same conversations must not duplicate channels."""
        account = self._make_account()
        with patch.object(TwitterApiClient, 'request', return_value=_chat_page()):
            account.action_fetch_groups()
            result = account.with_context(dialog=True).action_fetch_groups()
        self.assertEqual(result['params']['message'],
                         'Groups: 2, created: 0, updated: 2, members: 0')
        count = self.env['discuss.channel'].sudo().search_count([
            ('channel_type', 'in', ('x', 'x_group')),
            ('x_account_id', '=', account.id),
        ])
        self.assertEqual(count, 2)

    def test_fetch_groups_derives_name_when_group_name_is_encrypted(self):
        account = self._make_account()
        page = _chat_page()
        page['data'][0]['group_name'] = 'A' * 88
        with patch.object(TwitterApiClient, 'request', return_value=page):
            account.action_fetch_groups()
        channel = self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'x_group'),
            ('x_account_id', '=', account.id),
            ('x_conversation_id', '=', CHAT_GROUP_ID),
        ])
        self.assertTrue(channel)
        self.assertEqual(channel.name, 'alice, bob, %s' % OWNER_ID)

    def test_fetch_groups_paginates_chat_conversations(self):
        account = self._make_account()
        page1 = _chat_page()
        page1['meta'] = {'has_more': True, 'next_token': 'tok2'}
        second = _chat_page()
        second['data'][0]['id'] = 'g999000999000999002'
        second['data'][0]['group_name'] = 'Second Group'
        second['data'][0]['member_ids'] = ['333']
        second['includes'] = {'users': [
            {'id': '333', 'name': 'Carol', 'username': 'carol'}]}
        second['meta'] = {'has_more': False}

        def _request(method, path, params=None, body=None):
            self.assertEqual(method, 'GET')
            self.assertEqual(path, '/2/chat/conversations')
            return page1 if params.get('pagination_token') != 'tok2' else second

        with patch.object(TwitterApiClient, 'request', side_effect=_request):
            result = account.with_context(dialog=True).action_fetch_groups()
        self.assertEqual(result['params']['message'],
                         'Groups: 4, created: 3, updated: 1, members: 4')
        channels = self.env['discuss.channel'].sudo().search([
            ('channel_type', 'in', ('x', 'x_group')),
            ('x_account_id', '=', account.id),
        ])
        self.assertEqual(len(channels), 3)

    def test_fetch_groups_reattaches_orphan_channel(self):
        account = self._make_account()
        channel_model = self.env['discuss.channel'].sudo()
        orphan = channel_model.create({
            'channel_type': 'x_group',
            'x_conversation_id': CHAT_GROUP_ID,
            'name': 'Legacy Group',
            'x_account_id': False,
        })
        with patch.object(TwitterApiClient, 'request', return_value=_chat_page()):
            result = account.with_context(dialog=True).action_fetch_groups()
        self.assertEqual(result['params']['message'],
                         'Groups: 2, created: 1, updated: 1, members: 3')
        orphan.invalidate_recordset()
        self.assertEqual(orphan.x_account_id.id, account.id)
        self.assertEqual(len(channel_model.search([
            ('channel_type', '=', 'x_group'),
            ('x_conversation_id', '=', CHAT_GROUP_ID),
        ])), 1)

    # ------------------------------------------------------ fallback behavior
    def test_fetch_groups_falls_back_to_dm_events_on_temporary_error(self):
        account = self._make_account()

        def _request(method, path, params=None, body=None):
            if path == '/2/chat/conversations':
                raise TwitterTemporaryError('Service Unavailable')
            self.assertEqual(path, '/2/dm_events')
            return _events_page()

        with patch.object(TwitterApiClient, 'request', side_effect=_request):
            result = account.with_context(dialog=True).action_fetch_groups()
        self.assertEqual(result['params']['type'], 'success')
        self.assertEqual(result['params']['message'],
                         'Groups: 2, created: 2, updated: 0, members: 3')
        group = self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'x_group'),
            ('x_account_id', '=', account.id),
            ('x_conversation_id', '=', GROUP_ID),
        ])
        self.assertTrue(group)
        direct = self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'x'),
            ('x_account_id', '=', account.id),
            ('x_conversation_id', '=', DIRECT_ID),
        ])
        self.assertTrue(direct)

    def test_fetch_groups_does_not_fallback_on_permanent_error(self):
        """401/403/404 must NOT be silently hidden by the DM fallback."""
        account = self._make_account()
        for error in (TwitterAuthenticationError('unauthorized'),
                      TwitterPermissionError('forbidden'),
                      TwitterNotFoundError('not found')):
            with patch.object(TwitterApiClient, 'request',
                              side_effect=error), self.assertRaises(type(error)):
                account.action_fetch_groups()

    # ------------------------------------------------- get conversation info
    def test_action_fetch_group_info_updates_group_name(self):
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'Stale Group Name',
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': CHAT_GROUP_ID,
        })
        payload = {
            'data': {'id': CHAT_GROUP_ID, 'type': 'group',
                     'group_name': 'Design Team',
                     'member_ids': ['111', '222'], 'admin_ids': ['111'],
                     'participant_ids': ['111', '222']},
            'includes': {'users': [
                {'id': '111', 'name': 'Alice', 'username': 'alice'},
                {'id': '222', 'name': 'Bob', 'username': 'bob'}]},
        }
        with patch.object(TwitterApiClient, 'request', return_value=payload) as mocked:
            result = channel.action_fetch_group_info()
        self.assertEqual(mocked.call_args.args[:2],
                         ('GET', '/2/chat/conversations/%s' % CHAT_GROUP_ID))
        self.assertEqual(channel.name, 'Design Team')
        self.assertEqual(result['type'], 'ir.actions.client')
        self.assertEqual(result['params']['type'], 'success')
        self.assertIn('Design Team', result['params']['message'])

    def test_action_fetch_group_info_direct_names_from_other_participant(self):
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'Old Direct Name',
            'channel_type': 'x',
            'x_account_id': account.id,
            'x_conversation_id': DIRECT_ID,
        })
        payload = {
            'data': {'id': DIRECT_ID, 'type': 'direct',
                     'participant_ids': ['111', '222']},
            'includes': {'users': [
                {'id': '111', 'name': 'Alice', 'username': 'alice'},
                {'id': '222', 'name': 'Bob', 'username': 'bob'}]},
        }
        with patch.object(TwitterApiClient, 'request', return_value=payload):
            channel.action_fetch_group_info()
        self.assertEqual(channel.name, 'alice, bob')

    def test_action_fetch_group_info_not_found_warns(self):
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'Kept Name',
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': CHAT_GROUP_ID,
        })
        with patch.object(TwitterApiClient, 'request', return_value={}):
            result = channel.action_fetch_group_info()
        self.assertEqual(channel.name, 'Kept Name')
        self.assertEqual(result['params']['type'], 'warning')

    def test_action_fetch_group_info_decrypts_group_name_via_key_events(self):
        """Encrypted group_name needs the conversation key recovered from the
        events API's ``meta.conversation_key_events``; without it the channel
        would fall back to member names."""
        account = self._make_account()
        account.write({'x_chat_key_blob': 'fake-blob',
                       'x_chat_signing_key_version': '1',
                       'x_chat_key_mode': 'key_blob'})
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'Members-only Name',
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': CHAT_GROUP_ID,
        })
        cipher = 'A' * 88  # long base64-like ciphertext rejected by _safe_group_name
        conv_page = {
            'data': {'id': CHAT_GROUP_ID, 'type': 'group',
                     'group_name': cipher, 'member_ids': ['111'],
                     'participant_ids': ['111'], 'admin_ids': ['111']},
            'includes': {'users': [
                {'id': '111', 'name': 'Alice', 'username': 'alice'}]},
        }
        events_page = {
            'data': [],
            'meta': {'conversation_key_events': ['kce-1'], 'has_more': False},
        }
        fake_chat = MagicMock()
        fake_chat.extract_conversation_keys.return_value = {
            'keys': {'1788994678330': b'k' * 32}}

        def _decrypt(ciphertext, key=None):
            if key is None:
                raise TypeError('conversation key required')
            return 'Design Team'

        fake_chat.decrypt.side_effect = _decrypt

        def _request(method, url, **kwargs):
            return events_page if url.endswith('/events') else conv_page

        with patch.object(TwitterApiClient, 'request', side_effect=_request), \
             patch('chat_xdk.Chat', return_value=fake_chat):
            result = channel.action_fetch_group_info()
        self.assertEqual(channel.name, 'Design Team')
        self.assertEqual(result['params']['type'], 'success')
        self.assertIn('Design Team', result['params']['message'])
        self.assertTrue(
            (account.x_chat_conversation_keys or {}).get(CHAT_GROUP_ID),
            'recovered keys should be cached on the account')

    def test_action_fetch_group_info_does_not_clobber_name_when_decrypt_fails(self):
        """A ciphertext group_name we cannot decrypt must not be replaced with
        a member list -- keep the existing channel name and warn instead."""
        account = self._make_account()
        account.write({'x_chat_key_blob': 'fake-blob',
                       'x_chat_signing_key_version': '1',
                       'x_chat_key_mode': 'key_blob'})
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'Real Name (kept)',
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': CHAT_GROUP_ID,
        })
        cipher = 'A' * 88
        conv_page = {
            'data': {'id': CHAT_GROUP_ID, 'type': 'group',
                     'group_name': cipher, 'member_ids': ['111'],
                     'participant_ids': ['111'], 'admin_ids': ['111']},
            'includes': {'users': [
                {'id': '111', 'name': 'Alice', 'username': 'alice'}]},
        }
        events_page = {'data': [], 'meta': {}}
        fake_chat = MagicMock()

        def _decrypt(ciphertext, key=None):
            raise TypeError('conversation key required')

        fake_chat.decrypt.side_effect = _decrypt

        def _request(method, url, **kwargs):
            return events_page if url.endswith('/events') else conv_page

        with patch.object(TwitterApiClient, 'request', side_effect=_request), \
             patch('chat_xdk.Chat', return_value=fake_chat):
            result = channel.action_fetch_group_info()
        self.assertEqual(channel.name, 'Real Name (kept)')
        self.assertEqual(result['params']['type'], 'warning')
        self.assertIn('Could not decrypt', result['params']['message'])

    # ------------------------------------------------- fetch group members
    def test_action_fetch_group_members_upserts_partners_and_membership(self):
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'Members Sync',
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': CHAT_GROUP_ID,
        })
        payload = {
            'data': {'id': CHAT_GROUP_ID, 'type': 'group',
                     'member_ids': ['111', '222'],
                     'participant_ids': ['111', '222'],
                     'admin_ids': ['111']},
            'includes': {'users': [
                {'id': '111', 'name': 'Alice', 'username': 'alice'},
                {'id': '222', 'name': 'Bob', 'username': 'bob'},
                {'id': OWNER_ID, 'name': 'Owner', 'username': 'grpowner'}]},
        }
        with patch.object(TwitterApiClient, 'request',
                          return_value=payload) as mocked:
            result = channel.action_fetch_group_members()
        self.assertEqual(mocked.call_args.args[:2],
                         ('GET', '/2/chat/conversations/%s' % CHAT_GROUP_ID))
        self.assertEqual(result['params']['type'], 'success')
        self.assertIn('3 member(s)', result['params']['message'])
        self.assertIn('@alice', result['params']['message'])
        alice = self.env['res.partner'].sudo().search(
            [('x_user_id', '=', '111')], limit=1)
        bob = self.env['res.partner'].sudo().search(
            [('x_user_id', '=', '222')], limit=1)
        self.assertTrue(alice and bob)
        self.assertEqual(alice.x_username, 'alice')
        self.assertEqual(bob.x_username, 'bob')
        member_partners = channel.channel_member_ids.partner_id
        self.assertIn(alice.id, member_partners.ids)
        self.assertIn(bob.id, member_partners.ids)

    def test_action_fetch_group_members_updates_stale_username(self):
        account = self._make_account()
        alice = self.env['res.partner'].sudo().create({
            'name': 'Stale Alice',
            'x_user_id': '111',
            'x_username': 'old_handle',
        })
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'Members Update',
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': CHAT_GROUP_ID,
        })
        payload = {
            'data': {'id': CHAT_GROUP_ID, 'type': 'group',
                     'member_ids': ['111'],
                     'participant_ids': ['111'],
                     'admin_ids': ['111']},
            'includes': {'users': [
                {'id': '111', 'name': 'Alice New', 'username': 'alice'}]},
        }
        with patch.object(TwitterApiClient, 'request', return_value=payload):
            result = channel.action_fetch_group_members()
        alice.invalidate_recordset()
        self.assertEqual(alice.name, 'Stale Alice')
        self.assertEqual(alice.x_username, 'alice')
        self.assertEqual(result['params']['type'], 'success')
        self.assertIn('1 new', result['params']['message'])
        self.assertIn('@alice', result['params']['message'])

    def test_action_fetch_group_members_not_found_warns(self):
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'Members Not Found',
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': CHAT_GROUP_ID,
        })
        with patch.object(TwitterApiClient, 'request', return_value={}):
            result = channel.action_fetch_group_members()
        self.assertEqual(result['params']['type'], 'warning')

    def test_action_fetch_group_members_rejects_non_x(self):
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'Internal',
            'channel_type': 'channel',
            'x_account_id': account.id,
        })
        with self.assertRaises(ValueError):
            channel.action_fetch_group_members()

    def _members_payload(self, member_ids=None, users=None):
        member_ids = member_ids or ['111', '222']
        users = users or [
            {'id': '111', 'name': 'Alice', 'username': 'alice'},
            {'id': '222', 'name': 'Bob', 'username': 'bob'},
            {'id': OWNER_ID, 'name': 'Owner', 'username': 'grpowner'}]
        return {
            'data': {'id': CHAT_GROUP_ID, 'type': 'group',
                     'member_ids': member_ids,
                     'participant_ids': member_ids,
                     'admin_ids': member_ids[:1]},
            'includes': {'users': users},
        }

    def _make_member_channel(self, name, conversation_id=None):
        account = self._make_account()
        return self.env['discuss.channel'].sudo().create({
            'name': name,
            'channel_type': 'x_group',
            'x_account_id': account.id,
            'x_conversation_id': conversation_id or CHAT_GROUP_ID,
        })

    def test_action_fetch_group_members_bulk_aggregates(self):
        c1 = self._make_member_channel('Bulk Chat 1')
        c2 = self._make_member_channel('Bulk Chat 2')
        with patch.object(TwitterApiClient, 'request',
                          return_value=self._members_payload()) as mocked:
            result = (c1 | c2).action_fetch_group_members_bulk()
        self.assertEqual(result['params']['type'], 'success')
        self.assertIn('2 chat(s)', result['params']['message'])
        self.assertIn('6 member(s)', result['params']['message'])
        self.assertIn('3 new', result['params']['message'])
        for channel in (c1 | c2):
            self.assertEqual(
                len(channel.channel_member_ids.filtered(
                    lambda m: m.partner_id.x_user_id)), 3)

    def test_action_fetch_group_members_bulk_reports_failures(self):
        ok_channel = self._make_member_channel('Bulk Ok')
        broken = self.env['discuss.channel'].sudo().create({
            'name': 'Bulk Broken',
            'channel_type': 'x_group',
            'x_account_id': self._make_account().id,
            'x_conversation_id': CHAT_GROUP_ID,
        })
        internal = self.env['discuss.channel'].sudo().create({
            'name': 'Bulk Internal',
            'channel_type': 'channel',
            'x_account_id': self._make_account().id,
        })
        with patch.object(TwitterApiClient, 'request', side_effect=[
                self._members_payload(), {}]):
            result = (ok_channel | broken | internal).action_fetch_group_members_bulk()
        self.assertEqual(result['params']['type'], 'warning')
        self.assertIn('1 chat(s)', result['params']['message'])
        self.assertIn('1 skipped', result['params']['message'])
        self.assertIn('1 failed', result['params']['message'])
        self.assertIn('Bulk Broken', result['params']['message'])
        self.assertIn('not found', result['params']['message'])

    def test_action_fetch_group_members_bulk_no_actionable_chats(self):
        config = self.env['discuss.channel'].sudo().create({
            'name': 'Plain Group',
            'channel_type': 'channel',
        })
        result = config.action_fetch_group_members_bulk()
        self.assertEqual(result['params']['type'], 'info')
        self.assertIn('No X conversations selected', result['params']['message'])

    def test_action_fetch_group_members_server_action_binding(self):
        action = self.env.ref('x_account.action_server_fetch_group_members')
        self.assertEqual(action.state, 'code')
        self.assertEqual(action.model_id.model, 'discuss.channel')
        self.assertTrue(action.binding_model_id)
        self.assertEqual(action.binding_model_id.model, 'discuss.channel')
        self.assertEqual(action.binding_type, 'action')
        self.assertIn('action_fetch_group_members_bulk()', action.code)

    def test_action_fetch_group_members_server_action_runs_multiple(self):
        action = self.env.ref('x_account.action_server_fetch_group_members')
        c1 = self._make_member_channel('Action Chat 1')
        c2 = self._make_member_channel('Action Chat 2')
        with patch.object(TwitterApiClient, 'request',
                          return_value=self._members_payload()):
            result = action.with_context(
                active_model='discuss.channel',
                active_ids=(c1 | c2).ids).run()
        self.assertTrue(result)
        self.assertEqual(result['params']['type'], 'success')
        self.assertIn('2 chat(s)', result['params']['message'])
        for channel in (c1 | c2):
            self.assertEqual(
                len(channel.channel_member_ids.filtered(
                    lambda m: m.partner_id.x_user_id)), 3)

    def test_safe_group_name_rejects_short_base64_ciphertext(self):
        """A base64 blob short enough to slip old length checks must NOT become
        a channel name (it is ciphertext, not a readable name)."""
        from odoo.addons.x_account_twitter.services.twitter_group_sync import (
            TwitterGroupSync)
        self.assertEqual(
            TwitterGroupSync._safe_group_name(
                'vS43RO7ag+CSJ0zts3D00G9aLkb9h3drFBNw51I/'), '')
        self.assertEqual(TwitterGroupSync._safe_group_name('Design Team'),
                         'Design Team')
        self.assertEqual(
            TwitterGroupSync._safe_group_name('لايك ومرجعيه قروب الخير'),
            'لايك ومرجعيه قروب الخير')

    # ----------------------------------------------------------- messages
    def test_fetch_group_messages_stores_x_messages(self):
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            account, conversation_id=GROUP_ID, channel_type='x_group',
            create_if_not_found=True)
        page = {
            'data': [
                {'id': 'm1', 'event_type': 'MessageCreate',
                 'dm_conversation_id': GROUP_ID, 'sender_id': '111',
                 'text': 'from alice', 'created_at': '2026-09-01T11:00:00Z'},
                {'id': 'm2', 'event_type': 'MessageCreate',
                 'dm_conversation_id': GROUP_ID,
                 'sender_id': account.twitter_user_id,
                 'text': 'from owner', 'created_at': '2026-09-01T11:01:00Z'},
            ],
            'meta': {},
        }
        with patch.object(TwitterApiClient, 'request', return_value=page):
            result = account.action_fetch_group_messages()
        self.assertEqual(result, {'groups': 1, 'messages': 2, 'failures': 0,
                                  'encrypted_skipped': 0})
        xmsgs = self.env['x.message'].sudo().search([
            ('channel_id', '=', channel.id),
        ])
        self.assertEqual(len(xmsgs), 2)
        by_body = {m.body_plain: m for m in xmsgs}
        self.assertEqual(by_body['from alice'].direction, 'inbound')
        self.assertEqual(by_body['from owner'].direction, 'outbound')
        self.assertEqual(channel.x_sync_status, 'ok')

    def test_fetch_group_messages_uses_chat_events_api_for_xchat(self):
        account = self._make_account()
        self.env['discuss.channel'].sudo()._get_x_channel(
            account, conversation_id=CHAT_GROUP_ID, channel_type='x_group',
            create_if_not_found=True)
        with patch.object(TwitterApiClient, 'request',
                          return_value=_chat_events_page()) as mocked:
            result = account.action_fetch_group_messages()
        self.assertEqual(mocked.call_args.args[:2],
                         ('GET', '/2/chat/conversations/%s/events' % CHAT_GROUP_ID))
        # 1 plaintext message stored; 1 encrypted event explicitly tracked.
        self.assertEqual(result['messages'], 1)
        self.assertEqual(result['encrypted_skipped'], 1)
        self.assertEqual(result['failures'], 0)
        xmsgs = self.env['x.message'].sudo().search([
            ('channel_id', '=', self.env['discuss.channel'].sudo().search([
                ('x_conversation_id', '=', CHAT_GROUP_ID)], limit=1).id),
        ])
        by_id = {m.external_id: m for m in xmsgs}
        self.assertFalse(by_id['cm1'].encrypted)
        self.assertEqual(by_id['cm1'].body_plain, 'hello chat')
        self.assertTrue(by_id['cm2'].encrypted)

    def test_fetch_group_messages_is_idempotent(self):
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            account, conversation_id=GROUP_ID, channel_type='x_group',
            create_if_not_found=True)
        page = {
            'data': [
                {'id': 'm1', 'event_type': 'MessageCreate',
                 'dm_conversation_id': GROUP_ID, 'sender_id': '111',
                 'text': 'hi', 'created_at': '2026-09-01T11:00:00Z'},
            ],
            'meta': {},
        }
        with patch.object(TwitterApiClient, 'request', return_value=page):
            account.action_fetch_group_messages()
            account.action_fetch_group_messages()
        count = self.env['x.message'].sudo().search_count([
            ('channel_id', '=', channel.id),
        ])
        self.assertEqual(count, 1)

    def test_fetch_group_messages_marks_partial_when_encrypted(self):
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            account, conversation_id=CHAT_GROUP_ID, channel_type='x_group',
            create_if_not_found=True)
        with patch.object(TwitterApiClient, 'request',
                          return_value=_chat_events_page()):
            account.action_fetch_group_messages()
        self.assertEqual(channel.x_sync_status, 'partial')

    def test_get_dms_marks_from_me(self):
        account = self._make_account()
        provider = TwitterProvider(self.env, account)
        page = {
            'data': [
                {'id': 'm1', 'event_type': 'MessageCreate',
                 'dm_conversation_id': GROUP_ID, 'sender_id': '111',
                 'text': 'inbound', 'created_at': '2026-09-01T11:00:00Z'},
                {'id': 'm2', 'event_type': 'MessageCreate',
                 'dm_conversation_id': GROUP_ID, 'sender_id': OWNER_ID,
                 'text': 'outbound', 'created_at': '2026-09-01T11:01:00Z'},
            ],
            'meta': {},
        }
        with patch.object(TwitterApiClient, 'request', return_value=page):
            result = provider.get_dms(GROUP_ID, limit=100)
        by_text = {m['text']: m for m in result['messages']}
        self.assertFalse(by_text['inbound']['from_me'])
        self.assertTrue(by_text['outbound']['from_me'])
        self.assertEqual(by_text['inbound']['sender_id'], '111')

    def test_fetch_group_messages_does_not_require_encryption_code(self):
        """The XChat PIN gate only applies to providers that need it."""
        account = self._make_account()
        self.assertFalse(account.x_encryption_code)
        with patch.object(TwitterProvider, 'fetch_group_messages',
                          return_value={'groups': 1, 'messages': 0, 'failures': 0,
                                        'encrypted_skipped': 0}):
            action = account.with_context(dialog=True).action_fetch_group_messages()
        self.assertEqual(action['params']['type'], 'success')

    def test_provider_flags_no_session_and_no_pin(self):
        self.assertFalse(TwitterProvider._needs_cookies)
        self.assertFalse(TwitterProvider._needs_encryption_code)
        self.assertIn('fetch_groups', TwitterProvider.supported_operations(False))

    def test_fetch_group_messages_auth_failure_shows_warning(self):
        """A dead X OAuth 2.0 credential must not surface as a raw RPC_ERROR."""
        from odoo.addons.x_account_twitter.services import twitter_errors
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            account, conversation_id=GROUP_ID, channel_type='x_group',
            create_if_not_found=True)
        with patch.object(
                TwitterProvider, 'get_dms',
                side_effect=twitter_errors.TwitterError(
                    'http_400', 'Invalid or expired refresh token')):
            action = channel.with_context(dialog=True).action_fetch_group_messages()
        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'warning')
        self.assertIn('needs reauthentication', action['params']['message'])

    def test_fetch_group_messages_auth_failure_non_dialog_raises(self):
        """Without dialog context the auth failure still propagates."""
        from odoo.addons.x_account_twitter.services import twitter_errors
        account = self._make_account()
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            account, conversation_id=GROUP_ID, channel_type='x_group',
            create_if_not_found=True)
        with patch.object(
                TwitterProvider, 'get_dms',
                side_effect=twitter_errors.TwitterError('http_400')):
            with self.assertRaises(twitter_errors.TwitterError):
                channel.action_fetch_group_messages()


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestXChatDecryption(XAccountTwitterTestBase):
    """Chat XDK decryption of encoded_event blobs + encrypted metadata."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')

    def _make_account(self, with_blob=True):
        vals = {
            'name': 'Chat X Account',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'chatowner',
            'twitter_user_id': OWNER_ID,
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_oauth2_access_token': 'fake-at',
            'x_oauth2_refresh_token': 'fake-rt',
        }
        if with_blob:
            vals['x_chat_key_blob'] = 'fake-blob'
            vals['x_chat_signing_key_version'] = '1'
        return self.env['social.account'].create(vals)

    def test_without_key_blob_keeps_encrypted_marker(self):
        """No key blob -> encrypted events stay encrypted markers."""
        account = self._make_account(with_blob=False)
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            account, conversation_id=CHAT_GROUP_ID, channel_type='x_group',
            create_if_not_found=True)
        page = {
            'data': [
                {'id': 'cm1', 'event_type': 'MessageCreate', 'sender_id': '111',
                 'encoded_event': 'blob-1', 'created_at': '2026-09-01T11:00:00Z'},
            ],
            'meta': {},
        }
        with patch.object(TwitterApiClient, 'request', return_value=page):
            result = account.action_fetch_group_messages()
        self.assertEqual(result['messages'], 0)
        self.assertEqual(result['encrypted_skipped'], 1)
        xmsg = self.env['x.message'].sudo().search([
            ('channel_id', '=', channel.id), ('external_id', '=', 'cm1')], limit=1)
        self.assertTrue(xmsg.encrypted)

    def test_with_key_blob_decrypts_to_message(self):
        """Key blob present + XDK decrypt ok -> plaintext x.message."""
        account = self._make_account(with_blob=True)
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            account, conversation_id=CHAT_GROUP_ID, channel_type='x_group',
            create_if_not_found=True)
        page = {
            'data': [
                {'id': 'cm1', 'event_type': 'MessageCreate', 'sender_id': '111',
                 'encoded_event': 'blob-1', 'created_at': '2026-09-01T11:00:00Z'},
            ],
            'meta': {'conversation_key_events': ['kc-blob']},
        }
        fake_chat = MagicMock()
        fake_chat.decrypt_events.return_value = {
            'messages': [
                {'event': {'id': 'cm1', 'type': 'Message',
                           'sender_id': '111',
                           'content': {'text': 'hello decrypted'}}},
            ],
            'errors': {},
        }
        with patch.object(TwitterApiClient, 'request', return_value=page), \
             patch('chat_xdk.Chat', return_value=fake_chat):
            result = account.action_fetch_group_messages()
        self.assertEqual(result['messages'], 1)
        self.assertEqual(result['encrypted_skipped'], 0)
        xmsg = self.env['x.message'].sudo().search([
            ('channel_id', '=', channel.id), ('external_id', '=', 'cm1')], limit=1)
        self.assertTrue(xmsg)
        self.assertFalse(xmsg.encrypted)
        self.assertEqual(xmsg.body_plain, 'hello decrypted')

    def test_decrypt_failure_keeps_encrypted_marker(self):
        """XDK decrypt raises -> event stays encrypted, no crash."""
        account = self._make_account(with_blob=True)
        channel = self.env['discuss.channel'].sudo()._get_x_channel(
            account, conversation_id=CHAT_GROUP_ID, channel_type='x_group',
            create_if_not_found=True)
        page = {
            'data': [
                {'id': 'cm1', 'event_type': 'MessageCreate', 'sender_id': '111',
                 'encoded_event': 'blob-1', 'created_at': '2026-09-01T11:00:00Z'},
            ],
            'meta': {},
        }
        fake_chat = MagicMock()
        fake_chat.decrypt_events.side_effect = ValueError('bad blob')
        with patch.object(TwitterApiClient, 'request', return_value=page), \
             patch('chat_xdk.Chat', return_value=fake_chat):
            result = account.action_fetch_group_messages()
        self.assertEqual(result['messages'], 0)
        self.assertEqual(result['encrypted_skipped'], 1)
        self.assertEqual(result['failures'], 0)

    def test_encrypted_group_name_decrypted(self):
        """Encrypted group_name is decrypted via the XDK when a blob exists."""
        account = self._make_account(with_blob=True)
        encrypted_name = 'A' * 88  # long base64-like ciphertext rejected by _safe_group_name
        page = {
            'data': [
                {'id': CHAT_GROUP_ID, 'type': 'group',
                 'group_name': encrypted_name,
                 'member_ids': ['111'], 'participant_ids': ['111']},
            ],
            'includes': {'users': [
                {'id': '111', 'name': 'Alice', 'username': 'alice'}]},
            'meta': {'has_more': False},
        }
        fake_chat = MagicMock()
        fake_chat.decrypt.return_value = 'Decrypted Team'
        with patch.object(TwitterApiClient, 'request', return_value=page), \
             patch('chat_xdk.Chat', return_value=fake_chat):
            account.action_fetch_groups()
        channel = self.env['discuss.channel'].sudo().search([
            ('channel_type', '=', 'x_group'),
            ('x_account_id', '=', account.id),
            ('x_conversation_id', '=', CHAT_GROUP_ID),
        ], limit=1)
        self.assertTrue(channel)
        self.assertEqual(channel.name, 'Decrypted Team')
