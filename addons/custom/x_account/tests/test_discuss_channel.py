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