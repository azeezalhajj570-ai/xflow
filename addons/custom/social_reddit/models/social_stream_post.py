# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SocialStreamPost(models.Model):
    _inherit = 'social.stream.post'

    reddit_post_fullname = fields.Char('Reddit Post Fullname')
    reddit_score = fields.Integer('Reddit Score')
    reddit_num_comments = fields.Integer('Reddit Comments')
    reddit_url = fields.Char('Reddit URL')
    reddit_thumbnail = fields.Char('Reddit Thumbnail URL')
    reddit_over_18 = fields.Boolean('Reddit NSFW')
    reddit_flair_text = fields.Char('Reddit Flair')
    reddit_is_video = fields.Boolean('Reddit Is Video')
    reddit_author_fullname = fields.Char('Reddit Author Fullname')

    def _compute_author_link(self):
        reddit_posts = self._filter_by_media_types(['reddit'])
        super(SocialStreamPost, (self - reddit_posts))._compute_author_link()
        for post in reddit_posts:
            post.author_link = 'https://www.reddit.com/user/%s/' % post.author_name

    def _compute_post_link(self):
        reddit_posts = self._filter_by_media_types(['reddit'])
        super(SocialStreamPost, (self - reddit_posts))._compute_post_link()
        for post in reddit_posts:
            if post.post_link and not post.post_link.startswith('http'):
                post.post_link = 'https://www.reddit.com%s' % post.post_link

    def _fetch_matching_post(self):
        self.ensure_one()
        if self.media_type != 'reddit':
            return super()._fetch_matching_post()
        if self.reddit_post_fullname:
            return self.env['social.post'].search([
                ('live_post_ids.reddit_post_fullname', '=', self.reddit_post_fullname)
            ], limit=1)
        return self.env['social.post']

    def _reddit_comment_add(self, message, comment_id=None, attachment=None):
        self.ensure_one()
        account = self.stream_id.account_id
        client = account._get_reddit_client()
        if not client:
            return {'error': 'Could not authenticate with Reddit.'}

        if comment_id:
            parent = comment_id
        else:
            parent = self.reddit_post_fullname

        response = client.submit_comment(parent, message)
        if not response.ok:
            return {'error': 'Failed to add comment.'}

        result = response.json()
        if result.get('json', {}).get('errors'):
            return {'error': '; '.join('%s: %s' % (e[0], e[1]) for e in result['json']['errors'])}

        comment_data = result.get('json', {}).get('data', {})
        return {
            'id': comment_data.get('name'),
            'message': message,
            'formatted_created_time': fields.Datetime.now().isoformat(),
        }

    def _reddit_comment_delete(self, comment_id):
        self.ensure_one()
        account = self.stream_id.account_id
        client = account._get_reddit_client()
        if not client:
            return {'error': 'Could not authenticate with Reddit.'}

        response = client.delete_post(comment_id)
        return {'success': response.ok}

    def _reddit_comment_fetch(self, limit=50):
        self.ensure_one()
        account = self.stream_id.account_id
        client = account._get_reddit_client()
        if not client:
            return {'comments': []}

        response = client.get_comments(self.reddit_post_fullname, limit=limit)
        if not response.ok:
            return {'comments': []}

        data = response.json()
        if not data or len(data) < 2:
            return {'comments': []}

        comments_listing = data[1]
        comments = []
        for child in comments_listing.get('data', {}).get('children', []):
            if child.get('kind') != 't1':
                continue
            cdata = child.get('data', {})
            comments.append({
                'id': cdata.get('name'),
                'message': cdata.get('body', ''),
                'author': cdata.get('author', ''),
                'score': cdata.get('score', 0),
                'created_utc': cdata.get('created_utc', 0),
                'replies': [],
            })

        return {'comments': comments}
