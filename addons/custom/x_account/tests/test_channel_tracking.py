from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestChannelTracking(XAccountTestBase):
    """The X fields of a conversation carry mail.thread tracking, so a chat's
    X account / partner / sync state changes are auditable in its chatter."""

    TRACKED = ['active', 'x_account_id', 'x_partner_id', 'x_conversation_id',
               'x_sync_status']

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Channel Tracked Account',
            'media_id': cls.media.id,
            'social_account_handle': 'channel_tracked_acc',
        })
        cls._flush_tracking()

    @classmethod
    def _flush_tracking(cls):
        cls.env.flush_all()
        cls.env.cr.precommit.run()

    def _channel(self, **vals):
        base = {
            'name': 'X Conversation',
            'channel_type': 'x',
            'x_account_id': self.account.id,
        }
        base.update(vals)
        channel = self.env['discuss.channel'].create(base)
        self._flush_tracking()
        return channel

    def _tracking_value(self, channel, field_name):
        self._flush_tracking()
        channel.invalidate_recordset()
        values = channel.message_ids.tracking_value_ids.filtered(
            lambda v: v.field_id.name == field_name)
        return values.sorted('id', reverse=True)[:1]

    def test_channel_form_has_a_chatter(self):
        self.assertIn('message_ids', self.env['discuss.channel']._fields)
        arch = self.env.ref('x_account.x_group_channel_view_form').arch
        self.assertIn('chatter', str(arch))

    def test_fields_are_declared_as_tracked(self):
        channel_fields = self.env['discuss.channel']._fields
        for name in self.TRACKED:
            with self.subTest(field=name):
                self.assertTrue(
                    getattr(channel_fields[name], 'tracking', None),
                    '%s is not tracked' % name)

    def test_sync_status_change_is_logged(self):
        channel = self._channel(x_conversation_id='conv-1')
        channel.write({'x_sync_status': 'partial'})
        value = self._tracking_value(channel, 'x_sync_status')
        self.assertTrue(value, 'x_sync_status change was not logged')
        self.assertEqual(value.new_value_char, 'Partially Synchronized')

    def test_partner_change_is_logged(self):
        channel = self._channel(x_conversation_id='conv-2')
        partner = self.env['res.partner'].create({'name': 'X User'})
        channel.write({'x_partner_id': partner.id})
        value = self._tracking_value(channel, 'x_partner_id')
        self.assertTrue(value, 'x_partner_id change was not logged')
        self.assertEqual(value.new_value_char, 'X User')

    def test_change_body_spells_out_the_new_value(self):
        """The client renders a channel notification from its body, not from
        the tracking values, so the body has to carry the change."""
        channel = self._channel(x_conversation_id='conv-body')
        channel.write({'x_sync_status': 'failed'})
        value = self._tracking_value(channel, 'x_sync_status')
        self.assertTrue(value)
        body = str(value.mail_message_id.body)
        self.assertIn('X Sync Status', body)
        self.assertIn('Synchronization Failed', body)

    def test_change_body_labels_a_partner_and_a_boolean(self):
        channel = self._channel(x_conversation_id='conv-label')
        partner = self.env['res.partner'].create({'name': 'Labeled User'})
        channel.write({'x_partner_id': partner.id, 'active': False})
        partner_body = str(self._tracking_value(
            channel, 'x_partner_id').mail_message_id.body)
        self.assertIn('Labeled User', partner_body)
        self.assertIn('X Partner', partner_body)

    def test_archiving_a_chat_is_logged(self):
        """The X fields of a conversation are written when it is created, and
        Odoo drops tracking for the creating transaction — so the chatter's
        first entry is normally the archive done from the form."""
        channel = self._channel(x_conversation_id='conv-3')
        self.assertTrue(channel.active)
        channel.write({'active': False})
        value = self._tracking_value(channel, 'active')
        self.assertTrue(value, 'archiving the chat was not logged')
        self.assertEqual(value.new_value_integer, 0)

    def test_creation_of_an_x_chat_logs_no_tracking(self):
        """Documents the limitation: a conversation is created with its X
        fields already set, so nothing is tracked at creation time."""
        channel = self._channel(x_conversation_id='conv-4')
        self.assertFalse(channel.message_ids.tracking_value_ids)

    def test_plain_channels_are_not_touched(self):
        """A Discuss channel with no X fields logs no X tracking value."""
        channel = self.env['discuss.channel'].create({'name': 'Plain Chat'})
        self._flush_tracking()
        values = channel.message_ids.tracking_value_ids
        self.assertFalse(
            values.filtered(lambda v: v.field_id.name in self.TRACKED))
