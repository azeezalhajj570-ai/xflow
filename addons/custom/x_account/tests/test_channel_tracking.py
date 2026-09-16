from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestChannelTracking(XAccountTestBase):
    """The X fields of a conversation carry mail.thread tracking, so a chat's
    X account / partner / sync state changes are auditable in its chatter."""

    TRACKED = ['x_account_id', 'x_partner_id', 'x_conversation_id',
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

    def test_plain_channels_are_not_touched(self):
        """A Discuss channel with no X fields logs no X tracking value."""
        channel = self.env['discuss.channel'].create({'name': 'Plain Chat'})
        self._flush_tracking()
        values = channel.message_ids.tracking_value_ids
        self.assertFalse(
            values.filtered(lambda v: v.field_id.name in self.TRACKED))
