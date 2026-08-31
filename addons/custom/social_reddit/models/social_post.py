# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models


class SocialPost(models.Model):
    _inherit = 'social.post'

    reddit_accounts_count = fields.Integer(
        'Selected Reddit Accounts',
        compute='_compute_reddit_accounts_count')
    reddit_accounts_other_count = fields.Integer(
        'Selected Non-Reddit Accounts',
        compute='_compute_reddit_accounts_count')

    @api.depends('account_ids.media_type')
    def _compute_reddit_accounts_count(self):
        for post in self:
            post.reddit_accounts_count = len(post.account_ids.filtered(
                lambda a: a.media_type == 'reddit'))
            post.reddit_accounts_other_count = len(post.account_ids) - post.reddit_accounts_count

    @api.constrains('message', 'image_ids')
    def _check_has_message_or_image(self):
        reddit_only_posts = self.filtered(
            lambda p: all(m.media_type == 'reddit' for m in p.media_ids))
        super(SocialPost, self - reddit_only_posts)._check_has_message_or_image()

    @api.model
    def _message_fields(self):
        res = super()._message_fields()
        res['reddit'] = 'reddit_message'
        return res

    @api.model
    def _images_fields(self):
        res = super()._images_fields()
        res['reddit'] = 'image_ids'
        return res

    @api.model
    def _get_post_message_modifying_fields(self):
        res = super()._get_post_message_modifying_fields()
        res += ['reddit_title', 'reddit_subreddit', 'reddit_flair_text']
        return res
