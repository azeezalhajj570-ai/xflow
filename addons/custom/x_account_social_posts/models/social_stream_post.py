# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""X data + interaction drill-down on `social.stream.post`.

Fetched X posts live in the standard Social Marketing feed; the fields below
carry the X identifiers/counters and expose the post's interactions as a
drill-down. The link computes are gated on `media_type == 'twitter'` and
delegate every other media to `super()`, mirroring the YouTube/Reddit
providers.
"""

from odoo import api, fields, models


class SocialStreamPost(models.Model):
    _inherit = 'social.stream.post'

    x_tweet_id = fields.Char('X Post ID', index=True, copy=False)
    x_author_x_id = fields.Char('X Author ID')
    x_author_x_username = fields.Char('X Author Username')
    x_favorite_count = fields.Integer('X Likes')
    x_retweet_count = fields.Integer('X Retweets')
    x_reply_count = fields.Integer('X Replies')
    x_quote_count = fields.Integer('X Quotes')
    x_source_provider = fields.Char('X Provider')
    x_raw_metadata = fields.Json('X Raw Metadata')
    x_interactions_fetched_at = fields.Datetime(
        'Interactions Fetched At', readonly=True)
    x_interaction_ids = fields.One2many(
        'x.post.interaction', 'stream_post_id', string='X Interactions')
    x_comment_count = fields.Integer(
        'X Comment Records', compute='_compute_x_interaction_counts')
    x_like_count = fields.Integer(
        'X Like Records', compute='_compute_x_interaction_counts')
    x_retweet_count_records = fields.Integer(
        'X Retweet Records', compute='_compute_x_interaction_counts')

    @api.depends('x_interaction_ids.kind')
    def _compute_x_interaction_counts(self):
        for post in self:
            kinds = post.x_interaction_ids.mapped('kind')
            post.x_comment_count = kinds.count('comment')
            post.x_like_count = kinds.count('like')
            post.x_retweet_count_records = kinds.count('retweet')

    def _compute_author_link(self):
        x_posts = self._filter_by_media_types(['twitter'])
        super(SocialStreamPost, (self - x_posts))._compute_author_link()
        for post in x_posts:
            username = (post.x_author_x_username
                        or post.stream_id.account_id.social_account_handle or '')
            post.author_link = 'https://x.com/%s' % username if username else False

    def _compute_post_link(self):
        x_posts = self._filter_by_media_types(['twitter'])
        super(SocialStreamPost, (self - x_posts))._compute_post_link()
        for post in x_posts:
            username = (post.x_author_x_username
                        or post.stream_id.account_id.social_account_handle or '')
            if post.x_tweet_id and username:
                post.post_link = 'https://x.com/%s/status/%s' % (
                    username, post.x_tweet_id)
            else:
                post.post_link = False

    def _compute_is_author(self):
        x_posts = self._filter_by_media_types(['twitter'])
        super(SocialStreamPost, (self - x_posts))._compute_is_author()
        for post in x_posts:
            account = post.stream_id.account_id
            handle = (account.social_account_handle or '').lstrip('@').lower()
            author = (post.x_author_x_username or '').lstrip('@').lower()
            post.is_author = bool(handle and author == handle)

    def _fetch_matching_post(self):
        self.ensure_one()
        if self.media_type != 'twitter' or not self.x_tweet_id:
            return super()._fetch_matching_post()
        live_post = self.env['social.live.post'].search(
            [('twitter_tweet_id', '=', self.x_tweet_id)], limit=1)
        return live_post.post_id

    def action_refresh_x_interactions(self):
        """Fetch comments/retweets/likes for this post from the provider."""
        self.ensure_one()
        from ..services.post_sync import XPostSync
        account = self.stream_id.account_id
        summary = XPostSync(self.env, account).sync_interactions(self)
        message = 'Fetched %s comment(s) and %s retweet(s) for this post.' % (
            summary.get('comments', 0), summary.get('retweets', 0))
        notes = summary.get('unsupported_notes') or []
        if notes:
            message = '%s\n%s' % (message, '\n'.join(notes))
        return account._display_notification(
            'X Interactions', message, kind='warning' if notes else 'success')

    def action_open_x_interactions(self):
        """Open the interactions of this post, filtered by kind."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'X Interactions',
            'res_model': 'x.post.interaction',
            'view_mode': 'list,form',
            'domain': [('stream_post_id', '=', self.id)],
            'context': {'default_stream_post_id': self.id},
        }
