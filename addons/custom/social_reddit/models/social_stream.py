# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SocialStream(models.Model):
    _inherit = 'social.stream'

    reddit_subreddit_name = fields.Char('Subreddit',
        help='Subreddit to monitor (without r/). Only used for subreddit stream types.')

    def _fetch_stream_data(self):
        self.ensure_one()
        if self.media_id.media_type != 'reddit':
            return super()._fetch_stream_data()

        account = self.account_id
        client = account._get_reddit_client()
        if not client:
            _logger.warning('Reddit: No client available for stream %s', self.name)
            return False

        stream_type = self.stream_type_id.stream_type
        new_posts = False

        if stream_type == 'reddit_my_posts':
            username = account.social_account_handle or account.name
            new_posts = self._fetch_user_posts(client, username)
        elif stream_type == 'reddit_subreddit_hot':
            subreddit = self.reddit_subreddit_name
            if subreddit:
                new_posts = self._fetch_subreddit_posts(client, subreddit, 'hot')
        elif stream_type == 'reddit_subreddit_new':
            subreddit = self.reddit_subreddit_name
            if subreddit:
                new_posts = self._fetch_subreddit_posts(client, subreddit, 'new')

        return new_posts

    def _fetch_user_posts(self, client, username, limit=100):
        response = client.get_user_posts(username, limit=limit)
        if not response.ok:
            _logger.warning('Reddit: Failed to fetch user posts for %s: %s', username, response.text)
            return False
        return self._create_stream_posts_from_listing(response.json())

    def _fetch_subreddit_posts(self, client, subreddit, listing='hot', limit=100):
        response = client.get_subreddit_posts(subreddit, listing=listing, limit=limit)
        if not response.ok:
            _logger.warning('Reddit: Failed to fetch %s posts from r/%s: %s', listing, subreddit, response.text)
            return False
        return self._create_stream_posts_from_listing(response.json())

    def _create_stream_posts_from_listing(self, listing_data):
        children = listing_data.get('data', {}).get('children', [])
        posts_created = 0
        for child in children:
            data = child.get('data', {})
            post_fullname = data.get('name')
            if not post_fullname:
                continue

            existing = self.env['social.stream.post'].search_count([
                ('stream_id', '=', self.id),
                ('reddit_post_fullname', '=', post_fullname),
            ])
            if existing:
                continue

            permalink = data.get('permalink', '')
            author = data.get('author', '')
            author_fullname = data.get('author_fullname', '')
            title = data.get('title', '')
            selftext = data.get('selftext', '')
            score = data.get('score', 0)
            num_comments = data.get('num_comments', 0)
            url = data.get('url', '')
            thumbnail = data.get('thumbnail', '')
            created_utc = data.get('created_utc', 0)
            over_18 = data.get('over_18', False)
            link_flair_text = data.get('link_flair_text', '')
            is_video = data.get('is_video', False)
            is_gallery = data.get('is_gallery', False)

            message = title
            if selftext:
                message = '%s\n\n%s' % (title, selftext)

            from datetime import datetime, timezone
            published_date = datetime.fromtimestamp(created_utc, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

            self.env['social.stream.post'].create({
                'stream_id': self.id,
                'message': message,
                'author_name': author,
                'author_link': 'https://www.reddit.com/user/%s/' % author,
                'post_link': 'https://www.reddit.com%s' % permalink,
                'published_date': published_date,
                'reddit_post_fullname': post_fullname,
                'reddit_score': score,
                'reddit_num_comments': num_comments,
                'reddit_url': url if url and url.startswith('http') else '',
                'reddit_thumbnail': thumbnail if thumbnail and thumbnail.startswith('http') else '',
                'reddit_over_18': over_18,
                'reddit_flair_text': link_flair_text,
                'reddit_is_video': is_video,
                'reddit_author_fullname': author_fullname,
            })
            posts_created += 1

        return posts_created > 0

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        for stream in res:
            if stream.media_id.media_type == 'reddit':
                stream._apply_default_name()
        return res
