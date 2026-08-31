# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models


class SocialPostTemplate(models.Model):
    _inherit = 'social.post.template'

    reddit_title = fields.Char('Reddit Title',
        help='Title of the Reddit post (required for link and image posts).')
    reddit_subreddit = fields.Char('Reddit Subreddit',
        help='Subreddit name (without r/) to post to, e.g. "python".')
    reddit_flair_text = fields.Char('Reddit Flair Text',
        help='Flair text for the post (if required by the subreddit).')
    reddit_message = fields.Text('Reddit Message',
        help='Message body for Reddit text/self posts. Use markdown formatting.')
