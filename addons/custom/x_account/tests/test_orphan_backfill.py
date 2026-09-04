from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.x_account.tests.common import XAccountTestBase


@tagged('post_install', '-at_install', 'x_account')
class TestOrphanBackfill(XAccountTestBase):
    """Deterministic-only backfill of orphaned X channels."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account_a = cls.env['social.account'].create({
            'name': 'Owner A',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'ownera',
            'twitter_user_id': '111',
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
        })
        cls.account_b = cls.env['social.account'].create({
            'name': 'Owner B',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'ownerb',
            'twitter_user_id': '222',
            'x_provider': 'session_web',
            'x_auth_method': 'session_cookie',
        })
        cls.channel_model = cls.env['discuss.channel'].sudo()

    def _make_channel(self, **kw):
        vals = {'channel_type': 'x', 'x_account_id': False, 'name': 'Test Channel'}
        vals.update(kw)
        return self.channel_model.create(vals)

    def test_1to1_orphan_maps_to_matching_account(self):
        partner = self.env['res.partner'].sudo().create({
            'name': 'Owner A', 'x_user_id': '111'})
        channel = self._make_channel(
            channel_type='x', x_conversation_id='111-333')
        self.env['discuss.channel.member'].sudo().create([
            {'channel_id': channel.id, 'partner_id': partner.id}])

        result = self.env['x.orphan.channel.backfill'].backfill_orphans(dry_run=False)
        channel.invalidate_recordset()
        self.assertEqual(channel.x_account_id.id, self.account_a.id)
        self.assertEqual(result['assigned'], 1)

    def test_group_orphan_maps_to_single_member_owner(self):
        partner_a = self.env['res.partner'].sudo().create({
            'name': 'Owner A', 'x_user_id': '111'})
        partner_b = self.env['res.partner'].sudo().create({
            'name': 'Bob', 'x_user_id': '333'})
        channel = self._make_channel(
            channel_type='x_group', x_conversation_id='999000999000999001')
        self.env['discuss.channel.member'].sudo().create([
            {'channel_id': channel.id, 'partner_id': partner_a.id},
            {'channel_id': channel.id, 'partner_id': partner_b.id},
        ])

        self.env['x.orphan.channel.backfill'].backfill_orphans(dry_run=False)
        channel.invalidate_recordset()
        self.assertEqual(channel.x_account_id.id, self.account_a.id)

    def test_xchat_group_orphan_is_not_guessed(self):
        """g-prefixed group ids without a resolvable owner must stay untouched."""
        channel = self._make_channel(
            channel_type='x_group', x_conversation_id='g1234567890')

        result = self.env['x.orphan.channel.backfill'].backfill_orphans(dry_run=False)
        channel.invalidate_recordset()
        self.assertFalse(channel.x_account_id)
        self.assertEqual(len(result['unmappable']), 1)

    def test_ambiguous_1to1_orphan_is_not_guessed(self):
        """When both halves belong to different accounts, do not pick one."""
        partner_a = self.env['res.partner'].sudo().create({
            'name': 'A', 'x_user_id': '111'})
        partner_b = self.env['res.partner'].sudo().create({
            'name': 'B', 'x_user_id': '222'})
        channel = self._make_channel(
            channel_type='x', x_conversation_id='111-222')
        self.env['discuss.channel.member'].sudo().create([
            {'channel_id': channel.id, 'partner_id': partner_a.id},
            {'channel_id': channel.id, 'partner_id': partner_b.id},
        ])

        result = self.env['x.orphan.channel.backfill'].backfill_orphans(dry_run=False)
        channel.invalidate_recordset()
        self.assertFalse(channel.x_account_id)
        self.assertEqual(len(result['ambiguous']), 1)

    def test_dry_run_does_not_write(self):
        partner = self.env['res.partner'].sudo().create({
            'name': 'Owner A', 'x_user_id': '111'})
        channel = self._make_channel(
            channel_type='x', x_conversation_id='111-333')
        self.env['discuss.channel.member'].sudo().create([
            {'channel_id': channel.id, 'partner_id': partner.id}])

        result = self.env['x.orphan.channel.backfill'].backfill_orphans(dry_run=True)
        channel.invalidate_recordset()
        self.assertFalse(channel.x_account_id)
        self.assertEqual(result['assigned'], 1)
        self.assertTrue(result['dry_run'])
