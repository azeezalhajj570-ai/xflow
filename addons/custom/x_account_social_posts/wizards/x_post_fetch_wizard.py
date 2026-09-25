# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Operator-driven fetch of an account's X posts and their interactions.

Manual by design: reads are paid on per-call providers, so the operator picks
the depth (how many posts, which interaction kinds, caps) for each run.
"""

from odoo import fields, models


class XPostFetchWizard(models.TransientModel):
    _name = 'x.post.fetch.wizard'
    _description = 'Fetch X Posts'

    account_id = fields.Many2one(
        'social.account', 'Account', required=True,
        domain="[('media_type', '=', 'twitter')]")
    limit = fields.Integer('Timeline posts', default=20)
    fetch_comments = fields.Boolean('Fetch comments', default=True)
    max_comments = fields.Integer('Max comments per post', default=50)
    fetch_retweeters = fields.Boolean('Fetch retweeters', default=True)
    max_retweeters = fields.Integer('Max retweeters per post', default=100)
    fetch_likers = fields.Boolean(
        'Fetch likers', default=False,
        help="X exposes no public 'who liked a post' endpoint, so this usually "
             "returns nothing. Likes are stored as the post's like count. "
             "Enable only if your provider implements a likers source.")
    max_likers = fields.Integer('Max likers per post', default=50)

    def action_fetch(self):
        self.ensure_one()
        from ..services.post_sync import XPostSync
        account = self.account_id
        stream = account._get_or_create_x_posts_stream()
        if not stream:
            return account._display_notification(
                'X Posts', 'No X Account Posts stream type is available.',
                kind='danger')

        sync = XPostSync(self.env, account)

        kinds = []
        if self.fetch_comments:
            kinds.append('comment')
        if self.fetch_retweeters:
            kinds.append('retweet')
        if self.fetch_likers:
            kinds.append('like')

        # Providers that read audiences inline (XActions) take one cap for all
        # types, so pass the largest enabled cap; per-call providers ignore it.
        per_post_limit = max(
            self.max_comments if self.fetch_comments else 0,
            self.max_retweeters if self.fetch_retweeters else 0,
            self.max_likers if self.fetch_likers else 0,
        ) or None
        timeline = sync.sync_timeline(
            stream, limit=self.limit or 20, per_post_limit=per_post_limit)

        totals = {'comments': 0, 'retweets': 0, 'likes': 0}
        notes = []
        if timeline.get('inline_interactions'):
            # The provider returned engagers with the timeline read; the posts
            # already carry their interactions, so don't fetch them again.
            totals.update(timeline.get('interactions') or {})
        elif kinds:
            posts = self.env['social.stream.post'].search(
                [('stream_id', '=', stream.id)])
            for post in posts:
                summary = sync.sync_interactions(
                    post, kinds=kinds,
                    max_comments=self.max_comments or 50,
                    max_retweeters=self.max_retweeters or 100,
                    max_likers=self.max_likers or 50)
                totals['comments'] += summary['comments']
                totals['retweets'] += summary['retweets']
                totals['likes'] += summary['likes']
                notes.extend(summary['unsupported_notes'])

        message = (
            '%(posts)s post(s) synced (%(created)s new, %(updated)s updated). '
            '%(comments)s comment(s), %(retweets)s retweet(s), %(likes)s '
            'like(s) stored.' % {
                'posts': timeline['posts'],
                'created': timeline['created'],
                'updated': timeline['updated'],
                'comments': totals['comments'],
                'retweets': totals['retweets'],
                'likes': totals['likes'],
            })
        notes = sorted(set(notes))
        if notes:
            message = '%s\n%s' % (message, '\n'.join(notes))
        return account._display_notification(
            'X Posts', message, kind='warning' if notes else 'success')
