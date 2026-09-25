# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""One interaction (comment / like / retweet / quote) on a fetched X post.

Interactions are real records (not counters) so each engager's metadata is
queryable: who commented and what they said, who retweeted, when, and the raw
provider payload for anything not modelled explicitly.
"""

from odoo import fields, models


class XPostInteraction(models.Model):
    _name = 'x.post.interaction'
    _description = 'X Post Interaction'
    _order = 'external_created_at desc, id desc'

    stream_post_id = fields.Many2one(
        'social.stream.post', string='X Post',
        required=True, ondelete='cascade', index=True)
    account_id = fields.Many2one(
        'social.account', string='Account',
        related='stream_post_id.account_id', store=True, index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', related='account_id.company_id',
        store=True)
    kind = fields.Selection(
        selection=[
            ('comment', 'Comment'),
            ('like', 'Like'),
            ('retweet', 'Retweet'),
            ('quote', 'Quote'),
        ],
        string='Kind', required=True, index=True)
    external_id = fields.Char(
        'External ID', required=True, index=True,
        help='Comment tweet id, or the engager user id for likes/retweets.')
    author_x_id = fields.Char('Author X ID', index=True)
    author_x_username = fields.Char('Author Username')
    author_name = fields.Char('Author Name')
    author_partner_id = fields.Many2one(
        'res.partner', string='Author', ondelete='set null')
    text = fields.Text('Comment')
    external_created_at = fields.Datetime('Created on (X)')
    fetched_at = fields.Datetime('Fetched at', default=fields.Datetime.now)
    like_count = fields.Integer('Likes')
    reply_count = fields.Integer('Replies')
    retweet_count = fields.Integer('Retweets')
    provider = fields.Char('Provider')
    raw_metadata = fields.Json('Raw metadata')

    _x_post_interaction_uniq = models.Constraint(
        'UNIQUE(stream_post_id, kind, external_id)',
        'This interaction is already stored for this post.',
    )
