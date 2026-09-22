from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestXSaveXMessage(XAccountTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Test X Account',
            'media_id': cls.twitter_media.id,
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
        })

    def _channel(self, conversation_id='g-test-1'):
        return self.env['discuss.channel'].sudo()._get_x_channel(
            self.account,
            conversation_id=conversation_id,
            channel_type='x_group',
            create_if_not_found=True,
        )

    def test_saves_plaintext_message(self):
        channel = self._channel()
        xm = channel._save_x_message(
            direction='inbound',
            external_id='11111111-1111-1111-1111-111111111111',
            body='plain text',
            external_created_at=False,
            no_mail=True,
        )
        self.assertTrue(xm)
        self.assertEqual(xm.body_plain, 'plain text')
        self.assertFalse(xm.encrypted)

    def test_drops_empty_unencrypted_event(self):
        channel = self._channel()
        xm = channel._save_x_message(
            direction='inbound',
            external_id='22222222-2222-2222-2222-222222222222',
            body='',
            external_created_at=False,
            no_mail=True,
        )
        self.assertFalse(xm)
        self.assertEqual(
            self.env['x.message'].sudo().search_count([
                ('channel_id', '=', channel.id),
            ]), 0)

    def test_drops_encrypted_event_with_empty_body(self):
        channel = self._channel()
        xm = channel._save_x_message(
            direction='inbound',
            external_id='33333333-3333-3333-3333-333333333333',
            body='',
            encrypted=True,
            external_created_at=False,
            no_mail=True,
        )
        self.assertFalse(xm)
        self.assertEqual(
            self.env['x.message'].sudo().search_count([
                ('channel_id', '=', channel.id),
            ]), 0)

    def test_drops_whitespace_only_body(self):
        channel = self._channel('g-test-ws')
        xm = channel._save_x_message(
            direction='inbound',
            external_id='55555555-5555-5555-5555-555555555555',
            body='   \n\t ',
            external_created_at=False,
            no_mail=True,
        )
        self.assertFalse(xm)

    def test_message_is_idempotent_by_external_id(self):
        channel = self._channel('g-test-2')
        first = channel._save_x_message(
            direction='inbound',
            external_id='44444444-4444-4444-4444-444444444444',
            body='once',
            external_created_at=False,
            no_mail=True,
        )
        second = channel._save_x_message(
            direction='inbound',
            external_id='44444444-4444-4444-4444-444444444444',
            body='once',
            external_created_at=False,
            no_mail=True,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            self.env['x.message'].sudo().search_count([
                ('channel_id', '=', channel.id),
            ]), 1)

    def test_chat_form_uses_notebook_members_formset(self):
        view = self.env.ref('x_account.x_group_channel_view_form')
        arch = view.arch
        self.assertIn('<notebook>', arch)
        self.assertIn('string="Information"', arch)
        self.assertIn('string="Members"', arch)
        self.assertIn('name="channel_member_ids"', arch)
        self.assertIn('mode="list"', arch)
        self.assertIn('editable="bottom"', arch)
        self.assertIn('x_partner_username', arch)
        self.assertIn('x_partner_blue_verified', arch)
        self.assertIn('x_partner_is_following', arch)

    def test_group_members_editable_formset_adds_member(self):
        channel = self._channel('g-test-3')
        partner = self.env['res.partner'].create({'name': 'Formset Member'})
        member = self.env['discuss.channel.member'].sudo().create({
            'channel_id': channel.id,
            'partner_id': partner.id,
        })
        self.assertIn(member, channel.channel_member_ids)
        self.assertIn(partner, channel.channel_member_ids.partner_id)

    def test_chat_form_header_buttons_use_server_actions(self):
        view = self.env.ref('x_account.x_group_channel_view_form')
        arch = view.arch_db
        self.assertIn('type="action"', arch)
        for xmlid in (
            'x_account.action_server_bulk_follow',
            'x_account.action_server_send_message',
        ):
            self.assertIn('name="%d"' % self.env.ref(xmlid).id, arch)
        for xmlid in (
            'x_account.action_server_fetch_group_info',
            'x_account.action_server_fetch_group_members_form',
        ):
            self.assertNotIn('name="%d"' % self.env.ref(xmlid).id, arch)

    def test_chat_header_server_actions_bound_to_discuss_channel(self):
        for xmlid in (
            'x_account.action_server_bulk_follow',
            'x_account.action_server_send_message',
            'x_account.action_server_fetch_group_info',
            'x_account.action_server_fetch_group_members_form',
        ):
            action = self.env.ref(xmlid)
            self.assertEqual(action.state, 'code')
            self.assertEqual(action.model_id.model, 'discuss.channel')

    def test_fetch_members_form_server_action_runs_on_active_id(self):
        action = self.env.ref('x_account.action_server_fetch_group_members_form')
        channel = self._channel('g-test-4')
        result = action.with_context(
            active_model='discuss.channel',
            active_id=channel.id).run()
        self.assertTrue(result)
        self.assertEqual(result['params']['type'], 'warning')

    def test_member_following_flag_reflects_account_following(self):
        channel = self._channel('g-test-5')
        followed = self.env['res.partner'].create({'name': 'Followed Member'})
        other = self.env['res.partner'].create({'name': 'Other Member'})
        self.env['discuss.channel.member'].sudo().create([
            {'channel_id': channel.id, 'partner_id': followed.id},
            {'channel_id': channel.id, 'partner_id': other.id},
        ])
        self.account.write({'x_following_ids': [(6, 0, [followed.id])]})

        flags = {
            member.partner_id: member.x_partner_is_following
            for member in channel.channel_member_ids
        }
        self.assertTrue(flags[followed])
        self.assertFalse(flags[other])

    def test_chat_list_allows_delete(self):
        view = self.env.ref('x_account.x_group_channel_view_tree')
        arch = view.arch
        self.assertNotIn('delete="false"', arch)
        self.assertIn('name="active"', arch)

    def test_deleting_chat_cascades_messages_and_members(self):
        channel = self._channel('g-test-del')
        xm = channel._save_x_message(
            direction='inbound',
            external_id='66666666-6666-6666-6666-666666666666',
            body='bye',
            external_created_at=False,
            no_mail=True,
        )
        partner = self.env['res.partner'].create({'name': 'Ghost Member'})
        self.env['discuss.channel.member'].sudo().create({
            'channel_id': channel.id,
            'partner_id': partner.id,
        })
        channel_id = channel.id
        channel.unlink()
        self.assertFalse(
            self.env['discuss.channel'].sudo().browse(channel_id).exists())
        self.assertFalse(xm.exists())

    def test_fetch_group_messages_action_is_bound_to_the_chat_model(self):
        """The action has to be reachable from a chat's Action menu.

        It shipped with a NULL ``binding_model_id`` inside a ``noupdate``
        block, so it existed but no menu ever offered it.
        """
        action = self.env.ref('x_account.action_server_fetch_group_messages')
        self.assertEqual(action.model_id.model, 'discuss.channel')
        self.assertEqual(action.binding_model_id.model, 'discuss.channel')
        self.assertEqual(action.binding_type, 'action')
        self.assertIn('action_fetch_group_messages_bulk', action.code)

    def test_fetch_group_messages_bulk_skips_non_x_chats(self):
        """A model-wide action must tolerate ordinary chats being selected."""
        plain = self.env['discuss.channel'].create({'name': 'Plain chat'})
        x_group = self._channel('g-test-bulk')
        channel_model = type(self.env['discuss.channel'])
        with patch.object(channel_model, 'action_fetch_group_messages',
                          return_value={'messages': 3}):
            action = (plain + x_group).action_fetch_group_messages_bulk()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'success')
        self.assertIn('1 chat(s)', action['params']['message'])
        self.assertIn('3 message(s) stored', action['params']['message'])
        self.assertIn('1 skipped', action['params']['message'])

    def test_fetch_group_messages_bulk_reports_failure_without_raising(self):
        """One failing conversation must not abort the others."""
        x_group = self._channel('g-test-bulk-fail')
        channel_model = type(self.env['discuss.channel'])
        with patch.object(channel_model, 'action_fetch_group_messages',
                          side_effect=ValueError('boom')):
            action = x_group.action_fetch_group_messages_bulk()
        self.assertEqual(action['params']['type'], 'danger')
        self.assertIn('1 failed', action['params']['message'])
        self.assertIn('boom', action['params']['message'])

    def test_archived_channel_is_reused_not_duplicated(self):
        """A hidden channel still owns its conversation id.

        ``UNIQUE(x_account_id, x_conversation_id)`` ignores ``active``, so the
        lookup has to see archived rows: otherwise the caller inserts a
        duplicate, the constraint rejects it, and the recovery lookup misses the
        row for the same reason — failing the batch and dropping the message.
        """
        channel = self._channel('g-test-archived')
        channel.write({'active': False})
        resolved = self.env['discuss.channel'].sudo()._get_x_channel(
            self.account,
            conversation_id='g-test-archived',
            channel_type='x_group',
            create_if_not_found=True,
        )
        self.assertEqual(resolved.id, channel.id)
        self.assertFalse(resolved.active)
        self.assertEqual(
            self.env['discuss.channel'].sudo().with_context(
                active_test=False).search_count([
                    ('x_account_id', '=', self.account.id),
                    ('x_conversation_id', '=', 'g-test-archived'),
                ]), 1)

    def test_message_is_still_stored_for_an_archived_channel(self):
        """Stopping the crash is not enough: the message must be recorded."""
        channel = self._channel('g-test-archived-msg')
        channel.write({'active': False})
        resolved = self.env['discuss.channel'].sudo()._get_x_channel(
            self.account,
            conversation_id='g-test-archived-msg',
            channel_type='x_group',
            create_if_not_found=True,
        )
        xm = resolved._save_x_message(
            direction='inbound',
            external_id='99999999-9999-9999-9999-999999999999',
            body='stored while hidden',
            external_created_at=False,
            no_mail=True,
        )
        self.assertTrue(xm)
        self.assertEqual(xm.body_plain, 'stored while hidden')

    def test_x_chat_opens_with_the_x_account_chat_form(self):
        """A chat must look the same wherever it is opened from.

        The message form reaches its chat through a many2one, which the web
        client resolves through ``get_formview_action``. Left to the model
        default, that click opened mail's generic group form instead of the X
        chat form the X Account ▸ Chat list uses.
        """
        chat_form = self.env.ref('x_account.x_group_channel_view_form')
        for channel_type in ('x', 'x_group'):
            channel = self.env['discuss.channel'].sudo()._get_x_channel(
                self.account,
                conversation_id='g-open-%s' % channel_type,
                channel_type=channel_type,
                create_if_not_found=True,
            )
            action = channel.get_formview_action()
            self.assertEqual(action['res_model'], 'discuss.channel')
            self.assertEqual(action['res_id'], channel.id)
            self.assertEqual(action['views'], [(chat_form.id, 'form')])

    def test_plain_chat_keeps_the_default_form(self):
        """Only X conversations are rerouted to the X chat form."""
        plain = self.env['discuss.channel'].create({'name': 'Plain chat'})
        self.assertEqual(
            plain.get_formview_action()['views'], [(False, 'form')])

    def test_search_chat_by_full_link(self):
        channel = self._channel('g-search-1')
        found = self.env['discuss.channel'].sudo().search([
            ('x_chat_link', '=', 'https://x.com/i/chat/g-search-1'),
        ])
        self.assertIn(channel, found)

    def test_search_chat_by_bare_conversation_id(self):
        """Pasting the link and typing the id have to agree."""
        channel = self._channel('g-search-2')
        found = self.env['discuss.channel'].sudo().search([
            ('x_chat_link', 'ilike', 'g-search-2'),
        ])
        self.assertIn(channel, found)

    def test_search_chat_by_partial_link(self):
        channel = self._channel('g-search-3')
        found = self.env['discuss.channel'].sudo().search([
            ('x_chat_link', 'ilike', 'https://x.com/i/chat/g-search-3'),
        ])
        self.assertIn(channel, found)

    def test_search_chat_link_matches_only_the_chat_it_points_at(self):
        target = self._channel('g-search-4')
        other = self._channel('g-search-5')
        found = self.env['discuss.channel'].sudo().search([
            ('x_chat_link', '=', 'https://x.com/i/chat/g-search-4'),
        ])
        self.assertIn(target, found)
        self.assertNotIn(other, found)

    def test_chat_without_a_conversation_id_has_no_link(self):
        plain = self.env['discuss.channel'].create({'name': 'No link chat'})
        self.assertFalse(plain.x_chat_link)

    def test_chat_search_view_offers_the_chat_link(self):
        view = self.env.ref(
            'x_account.view_discuss_channel_search_inherit_x_account')
        self.assertIn('name="x_chat_link"', view.arch_db)
