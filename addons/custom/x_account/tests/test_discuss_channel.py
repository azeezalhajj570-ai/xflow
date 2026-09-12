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

    def test_saves_encrypted_event_with_empty_body(self):
        channel = self._channel()
        xm = channel._save_x_message(
            direction='inbound',
            external_id='33333333-3333-3333-3333-333333333333',
            body='',
            encrypted=True,
            external_created_at=False,
            no_mail=True,
        )
        self.assertTrue(xm)
        self.assertEqual(xm.body_plain, '')
        self.assertTrue(xm.encrypted)

    def test_encrypted_event_is_idempotent(self):
        channel = self._channel('g-test-2')
        first = channel._save_x_message(
            direction='inbound',
            external_id='44444444-4444-4444-4444-444444444444',
            body='',
            encrypted=True,
            external_created_at=False,
            no_mail=True,
        )
        second = channel._save_x_message(
            direction='inbound',
            external_id='44444444-4444-4444-4444-444444444444',
            body='',
            encrypted=True,
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