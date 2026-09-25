# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Account entry points: fetch the account's X posts and browse them.

`x_account` deliberately suppresses `social_twitter`'s default stream for
session/managed accounts, so this module owns its own "X Account Posts" stream
type and creates the stream lazily when the operator fetches posts.
"""

from odoo import api, fields, models

from .social_stream import (
    X_ACCOUNT_POSTS_STREAM_TYPE,
    provider_supports_post_reads,
)


class SocialAccount(models.Model):
    _inherit = 'social.account'

    x_social_stream_post_count = fields.Integer(
        'X Posts', compute='_compute_x_social_stream_post_count')
    x_can_fetch_posts = fields.Boolean(
        'Can Fetch X Posts', compute='_compute_x_can_fetch_posts')

    @api.depends('media_type')
    def _compute_x_social_stream_post_count(self):
        Post = self.env['social.stream.post']
        for account in self:
            account.x_social_stream_post_count = Post.search_count(
                [('account_id', '=', account.id)])

    @api.depends('media_type', 'x_provider')
    def _compute_x_can_fetch_posts(self):
        """True when the account's provider implements the timeline read."""
        for account in self:
            account.x_can_fetch_posts = (
                account.media_type == 'twitter'
                and provider_supports_post_reads(account))

    def _get_or_create_x_posts_stream(self):
        """Return the account's 'X Account Posts' stream, creating it once."""
        self.ensure_one()
        stream = self.env['social.stream'].search([
            ('account_id', '=', self.id),
            ('stream_type_id.stream_type', '=', X_ACCOUNT_POSTS_STREAM_TYPE),
        ], limit=1)
        if stream:
            return stream
        stream_type = self.env.ref(
            'x_account_social_posts.stream_type_x_account_posts',
            raise_if_not_found=False)
        if not stream_type:
            return self.env['social.stream']
        handle = self.social_account_handle or self.name
        return self.env['social.stream'].with_context(
            x_skip_stream_fetch=True).create({
                'name': '%s - X Account Posts' % handle,
                'media_id': self.media_id.id,
                'account_id': self.id,
                'stream_type_id': stream_type.id,
            })

    def action_fetch_x_posts(self):
        """Open the fetch wizard for this account."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Fetch X Posts',
            'res_model': 'x.post.fetch.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_account_id': self.id},
        }

    def action_open_x_posts(self):
        """Open the account's fetched X posts."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'X Posts',
            'res_model': 'social.stream.post',
            'view_mode': 'list,form',
            'domain': [('account_id', '=', self.id)],
            'context': {'search_default_group_by_stream': 1},
        }
