# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json

from odoo import fields, models


class XMessage(models.Model):
    """Mirror of an external X message, mapping X external id ↔ Odoo Discuss/mail
    message. Carries external identity that mail.message alone cannot represent."""

    _name = 'x.message'
    _description = 'X Message'
    _order = 'external_created_at desc'

    channel_id = fields.Many2one(
        'discuss.channel',
        string='Discuss Channel',
        required=True,
        index=True,
        ondelete='cascade',
    )
    account_id = fields.Many2one(
        'social.account',
        string='X Account',
        required=True,
        index=True,
        ondelete='cascade',
    )
    direction = fields.Selection(
        [
            ('inbound', 'Inbound'),
            ('outbound', 'Outbound'),
        ],
        string='Direction',
        required=True,
    )
    external_id = fields.Char(
        string='External ID',
        help='External X message/conversation-event id.',
    )
    body_plain = fields.Text(string='Body (plain text)')
    external_created_at = fields.Datetime(string='External Created At', index=True)
    author_partner_id = fields.Many2one('res.partner', string='Author Partner')
    author_x_id = fields.Char(string='Author X ID')
    author_x_username = fields.Char(string='Author X Username')
    encrypted = fields.Boolean(
        string='Encrypted',
        help='True when the external event body is end-to-end encrypted '
             '(encoded_event) and no plaintext is available. The record exists '
             'so the sync state is explicit instead of silently missing.',
    )
    acked = fields.Boolean(string='Acknowledged')
    delivered = fields.Boolean(string='Delivered')
    participant_joined = fields.Boolean(string='Participant Joined')
    participant_left = fields.Boolean(string='Participant Left')
    mail_message_id = fields.Many2one(
        'mail.message',
        string='Mail Message',
        index=True,
        ondelete='set null',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='account_id.company_id',
        store=True,
        index=True,
    )

    _external_id_uniq = models.Constraint(
        'UNIQUE(channel_id, external_id)',
        'An external X message id must be unique per channel.',
    )

    def _get_company_x_account(self):
        """Get the X account for the current company."""
        self.ensure_one()
        return self.env['social.account'].sudo().search([
            ('company_id', '=', self.env.company.id),
            ('x_provider', '!=', False),
            ('active', '=', True),
            ('x_connection_status', 'not in', ('disabled', 'new')),
        ], limit=1)

    def _extract_tweet_ids(self):
        """Extract tweet IDs from message body."""
        self.ensure_one()
        if not self.body_plain:
            return []
        tweet_ids = []
        body = self.body_plain
        for domain in ['twitter.com', 'x.com']:
            if domain in body:
                for part in body.split():
                    if domain in part and '/status/' in part:
                        url_parts = part.split('/status/')
                        if len(url_parts) > 1:
                            tweet_id = url_parts[1].split('?')[0].split('/')[0]
                            if tweet_id.isdigit() and tweet_id not in tweet_ids:
                                tweet_ids.append(tweet_id)
        return tweet_ids

    def _run_channel_automation(self, operation):
        """Generic helper for channel automation.

        Executes the specified operation for the current company's X account.
        The Automation Rule domain handles all filtering (channel, time, content).
        """
        self.ensure_one()
        if not self.body_plain:
            return

        account = self.account_id.sudo()
        if not account or not account.active or account.x_connection_status in ('disabled', 'new'):
            account = self._get_company_x_account()
        if not account:
            return

        today_start = fields.Datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        existing = self.env['x.account.task'].sudo().search_count([
            ('account_id', '=', account.id),
            ('operation', '=', operation),
            ('create_date', '>=', today_start),
            ('task_context', 'ilike', self.author_x_id or ''),
        ])
        if existing:
            return

        if operation in ('like', 'repost', 'comment'):
            tweet_ids = self._extract_tweet_ids()
            if not tweet_ids:
                return

            for tweet_id in tweet_ids:
                task_ctx = {
                    'post': {'post_id': tweet_id},
                    'channel_id': self.channel_id.id,
                    'author_x_id': self.author_x_id,
                    'source': 'channel_automation',
                }
                self.env['x.account.task'].sudo().create({
                    'account_id': account.id,
                    'operation': operation,
                    'priority': 1,
                    'task_context': json.dumps(task_ctx),
                })
        elif operation == 'follow':
            if not self.author_x_username:
                return
            task_ctx = {
                'screen_name': self.author_x_username,
                'channel_id': self.channel_id.id,
                'author_x_id': self.author_x_id,
                'source': 'channel_automation',
            }
            self.env['x.account.task'].sudo().create({
                'account_id': account.id,
                'operation': 'follow',
                'priority': 1,
                'task_context': json.dumps(task_ctx),
            })

    def _run_channel_like(self):
        """Execute like automation for this message."""
        return self._run_channel_automation('like')

    def _run_channel_repost(self):
        """Execute repost automation for this message."""
        return self._run_channel_automation('repost')

    def _run_channel_comment(self):
        """Execute comment automation for this message."""
        return self._run_channel_automation('comment')

    def _run_channel_follow(self):
        """Execute follow automation for this message."""
        return self._run_channel_automation('follow')
