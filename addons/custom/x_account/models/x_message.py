# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


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
            _logger.info(
                'Channel automation skipped (operation=%s): empty body for x.message id=%s',
                operation, self.id,
            )
            return

        account = self.account_id.sudo()
        if not account or not account.active or account.x_connection_status in ('disabled', 'new'):
            account = self._get_company_x_account()
        if not account:
            _logger.info(
                'Channel automation skipped (operation=%s): no valid X account for x.message id=%s '
                '(account_id=%s, status=%s)',
                operation, self.id, self.account_id.id,
                self.account_id.x_connection_status if self.account_id else 'missing',
            )
            return

        if operation in ('like', 'repost', 'comment', 'bookmark', 'unbookmark'):
            tweet_ids = self._extract_tweet_ids()
            if not tweet_ids:
                _logger.info(
                    'Channel automation skipped (operation=%s): no tweet IDs found in body '
                    'for x.message id=%s, body=%r',
                    operation, self.id, self.body_plain[:200] if self.body_plain else '',
                )
                return

            for tweet_id in tweet_ids:
                task_ctx = {
                    'post': {'post_id': tweet_id},
                    'channel_id': self.channel_id.id,
                    'author_x_id': self.author_x_id,
                    'source': 'channel_automation',
                }
                task = self.env['x.account.task'].sudo().create({
                    'account_id': account.id,
                    'operation': operation,
                    'priority': 1,
                    'task_context': json.dumps(task_ctx),
                })
                _logger.info(
                    'Channel automation created task id=%s (operation=%s) for x.message id=%s, '
                    'tweet_id=%s, account_id=%s',
                    task.id, operation, self.id, tweet_id, account.id,
                )
        elif operation == 'follow':
            if not self.author_x_username:
                _logger.info(
                    'Channel automation skipped (operation=%s): missing author_x_username '
                    'for x.message id=%s, author_x_id=%s',
                    operation, self.id, self.author_x_id,
                )
                return
            task_ctx = {
                'screen_name': self.author_x_username,
                'channel_id': self.channel_id.id,
                'author_x_id': self.author_x_id,
                'source': 'channel_automation',
            }
            task = self.env['x.account.task'].sudo().create({
                'account_id': account.id,
                'operation': 'follow',
                'priority': 1,
                'task_context': json.dumps(task_ctx),
            })
            _logger.info(
                'Channel automation created task id=%s (operation=follow) for x.message id=%s, '
                'screen_name=%s, account_id=%s',
                task.id, self.id, self.author_x_username, account.id,
            )

    def _run_channel_like(self):
        """Execute like automation for this message."""
        return self._run_channel_automation('like')

    def _run_channel_repost(self):
        """Execute repost automation for this message."""
        return self._run_channel_automation('repost')

    def _run_channel_comment(self):
        """Execute comment automation for this message."""
        return self._run_channel_automation('comment')

    def _run_channel_bookmark(self):
        """Execute bookmark automation for this message."""
        return self._run_channel_automation('bookmark')

    def _run_channel_unbookmark(self):
        """Execute unbookmark automation for this message."""
        return self._run_channel_automation('unbookmark')

    def _run_channel_follow(self):
        """Execute follow automation for this message."""
        return self._run_channel_automation('follow')
