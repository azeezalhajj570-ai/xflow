# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain


class SocialPost(models.Model):
    _inherit = 'social.post'

    # Redefine with a unique relation table (same name as template field but different table)
    tiktok_video_ids = fields.Many2many(
        'ir.attachment',
        relation='tiktok_post_video_ids_rel',
        string='TikTok Videos',
        help='Video file(s) to post on TikTok.',
    )
    tiktok_privacy_level = fields.Selection([
        ('PUBLIC_TO_EVERYONE', 'Public'),
        ('MUTUAL_FOLLOW_FRIENDS', 'Friends'),
        ('FOLLOWER_OF_CREATOR', 'Followers'),
        ('SELF_ONLY', 'Private'),
    ], string='TikTok Privacy', default='PUBLIC_TO_EVERYONE')
    tiktok_accounts_count = fields.Integer(
        'Selected TikTok Accounts',
        compute='_compute_tiktok_accounts_count')
    tiktok_accounts_other_count = fields.Integer(
        'Selected Other Accounts',
        compute='_compute_tiktok_accounts_count')

    @api.constrains('message', 'image_ids')
    def _check_has_message_or_image(self):
        tiktok_posts_only = self.filtered(
            lambda post: all(media.media_type == 'tiktok' for media in post.media_ids))
        super(SocialPost, self - tiktok_posts_only)._check_has_message_or_image()

    @api.depends('live_post_ids.tiktok_video_id')
    def _compute_stream_posts_count(self):
        super()._compute_stream_posts_count()

    @api.depends('account_ids.media_type')
    def _compute_tiktok_accounts_count(self):
        for post in self:
            post.tiktok_accounts_count = len(post.account_ids.filtered(
                lambda account: account.media_type == 'tiktok'))
            post.tiktok_accounts_other_count = len(post.account_ids) - post.tiktok_accounts_count

    def _get_stream_post_domain(self):
        domain = super()._get_stream_post_domain()
        tiktok_video_ids = [
            vid for vid in self.live_post_ids.mapped('tiktok_video_id') if vid
        ]
        if tiktok_video_ids:
            return Domain.OR([domain, [('tiktok_video_id', 'in', tiktok_video_ids)]])
        return domain
