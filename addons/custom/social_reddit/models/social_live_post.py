# Part of Odoo. See LICENSE file for full copyright and licensing details.
import json
import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class SocialLivePost(models.Model):
    _inherit = 'social.live.post'

    reddit_post_fullname = fields.Char('Reddit Post Fullname',
        help='Reddit fullname of the published post (e.g. t3_abc123).')
    reddit_permalink = fields.Char('Reddit Permalink',
        help='Permalink to the post on Reddit.')

    def _compute_live_post_link(self):
        reddit_live_posts = self._filter_by_media_types(['reddit']).filtered(
            lambda p: p.state == 'posted' and p.reddit_permalink)
        super(SocialLivePost, (self - reddit_live_posts))._compute_live_post_link()
        for post in reddit_live_posts:
            post.live_post_link = 'https://www.reddit.com%s' % post.reddit_permalink

    def _post(self):
        reddit_live_posts = self._filter_by_media_types(['reddit'])
        super(SocialLivePost, (self - reddit_live_posts))._post()
        for live_post in reddit_live_posts:
            live_post._post_reddit()

    def _post_reddit(self):
        self.ensure_one()
        account = self.account_id
        post = self.post_id
        client = account._get_reddit_client()
        if not client:
            self.write({'state': 'failed', 'failure_reason': _('Could not authenticate with Reddit. Please reconnect your account.')})
            return

        subreddit = (post.reddit_subreddit or '').strip().lower()
        if subreddit.startswith('r/'):
            subreddit = subreddit[2:]
        title = post.reddit_title or post.message[:300] if post.message else ''
        message = post.reddit_message or post.message or ''
        image_ids = post.image_ids
        flair_text = post.reddit_flair_text

        if not title:
            self.write({'state': 'failed', 'failure_reason': _('A title is required for Reddit posts.')})
            return

        if not subreddit:
            self.write({'state': 'failed', 'failure_reason': _('A subreddit is required. Please specify one in the post form.')})
            return

        subreddit_info_resp = client.get_subreddit_info(subreddit)
        if not subreddit_info_resp.ok:
            self.write({'state': 'failed', 'failure_reason': _('Subreddit "%s" not found or not accessible.', subreddit)})
            return

        subreddit_data = subreddit_info_resp.json().get('data', {})
        sr_fullname = subreddit_data.get('name')
        if not sr_fullname:
            self.write({'state': 'failed', 'failure_reason': _('Could not resolve subreddit "%s".', subreddit)})
            return

        kind = 'self'
        submit_kwargs = {}

        if image_ids:
            image = image_ids[0]
            image_data = image.with_context(bin_size=False).raw
            image_name = image.name or 'image.png'
            image_mimetype = image.mimetype or 'image/png'

            asset_id = client.upload_media(image_name, image_mimetype, image_data)
            if not asset_id:
                self.write({'state': 'failed', 'failure_reason': _('Could not upload image to Reddit.')})
                return

            kind = 'image'
            # Reddit requires image posts to use the image_asset_id parameter
            submit_kwargs['image_asset'] = asset_id
        else:
            url = self.env['social.post']._extract_url_from_message(message or '')
            if url:
                kind = 'link'
                submit_kwargs['url'] = url
            else:
                submit_kwargs['text'] = message

        if flair_text:
            submit_kwargs['flair_text'] = flair_text

        response = client.submit_post(kind, subreddit, title, **submit_kwargs)
        if not response.ok:
            error_msg = self._parse_reddit_error(response)
            self.write({'state': 'failed', 'failure_reason': error_msg})
            return

        result = response.json()
        if result.get('json', {}).get('errors'):
            errors = result['json']['errors']
            error_msg = '; '.join('%s: %s' % (e[0], e[1]) for e in errors)
            self.write({'state': 'failed', 'failure_reason': error_msg or _('Reddit returned an unknown error.')})
            return

        post_data = result.get('json', {}).get('data', {})
        post_fullname = post_data.get('name')
        post_url = post_data.get('url', post_data.get('permalink', ''))

        self.write({
            'state': 'posted',
            'failure_reason': False,
            'reddit_post_fullname': post_fullname,
            'reddit_permalink': post_url,
        })

    def _refresh_statistics(self):
        super()._refresh_statistics()
        accounts = self.env['social.account'].search([('media_type', '=', 'reddit')])
        for account in accounts:
            existing_posts = self.env['social.live.post'].sudo().search([
                ('account_id', '=', account.id),
                ('reddit_post_fullname', '!=', False),
            ], order='create_date DESC', limit=100)

            if not existing_posts:
                continue

            client = account._get_reddit_client()
            if not client:
                continue

            fullnames = existing_posts.mapped('reddit_post_fullname')
            for batch in [fullnames[i:i+100] for i in range(0, len(fullnames), 100)]:
                resp = client.get_post_info_batch(batch)
                if not resp.ok:
                    _logger.warning('Reddit: Failed to fetch post stats for account %s: %s', account.name, resp.text)
                    continue
                listing = resp.json()
                children = listing.get('data', {}).get('children', [])
                for child in children:
                    data = child.get('data', {})
                    child_fullname = data.get('name')
                    if not child_fullname:
                        continue
                    live_post = existing_posts.filtered(lambda p: p.reddit_post_fullname == child_fullname)
                    if live_post:
                        score = data.get('score', 0)
                        num_comments = data.get('num_comments', 0)
                        upvote_ratio = data.get('upvote_ratio', 0)
                        live_post.write({
                            'engagement': score + num_comments,
                        })

    @staticmethod
    def _parse_reddit_error(response):
        try:
            result = response.json()
            errors = result.get('json', {}).get('errors', [])
            if errors:
                return '; '.join('%s: %s' % (e[0], e[1]) for e in errors)
            reason = result.get('reason', '')
            if reason:
                return reason
            return response.text or _('Unknown Reddit API error (HTTP %s)', response.status_code)
        except (ValueError, AttributeError):
            return response.text or _('Unknown Reddit API error (HTTP %s)', response.status_code)
