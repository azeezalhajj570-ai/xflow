# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Persist the X webhook registration + X Activity API subscriptions.

The official X V2 Webhooks API exposes one webhook registration per app; the X
Activity API (XAA) exposes one-or-more subscriptions per (app, user, event).
These models record that state so registration/subscription lifecycle calls are
safe to run repeatedly (idempotent: never duplicate what already exists).
"""

import json
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class XTwitterWebhook(models.Model):
    """Singleton mirror of the X app's registered webhook (one per app)."""

    _name = 'x.twitter.webhook'
    _description = 'X Webhook Registration'

    name = fields.Char(string='Webhook URL', required=True)
    webhook_id = fields.Char(
        string='X Webhook ID',
        help='The webhook_id returned by X when the webhook was registered.',
        index=True,
    )
    valid = fields.Boolean(
        string='Valid',
        help='True while X considers the webhook endpoint valid (passed CRC).',
    )
    app_consumer_secret_set = fields.Boolean(
        string='Signing Secret Set',
        compute='_compute_app_consumer_secret_set',
    )
    registered_at = fields.Datetime(string='Registered At', readonly=True)

    _webhook_id_uniq = models.Constraint(
        'UNIQUE(webhook_id)',
        'An X webhook id must be unique.',
    )

    def _compute_app_consumer_secret_set(self):
        configured = bool(self.env['ir.config_parameter'].sudo().get_param(
            'x_account_twitter.app_consumer_secret', ''))
        for record in self:
            record.app_consumer_secret_set = configured


class XTwitterSubscription(models.Model):
    """X Activity API subscription for one event type on one X account."""

    _name = 'x.twitter.subscription'
    _description = 'X Activity API Subscription'

    account_id = fields.Many2one(
        'social.account',
        string='X Account',
        required=True,
        index=True,
        ondelete='cascade',
    )
    webhook_id = fields.Many2one(
        'x.twitter.webhook',
        string='Webhook',
        ondelete='set null',
    )
    event_type = fields.Char(string='Event Type', required=True)
    subscription_id = fields.Char(
        string='Subscription ID',
        help='The subscription_id returned by X.',
        index=True,
    )
    state = fields.Selection(
        [
            ('active', 'Active'),
            ('pending', 'Pending'),
            ('failed', 'Failed'),
        ],
        string='State',
        default='pending',
    )
    error = fields.Text(string='Error', readonly=True)
    created_at = fields.Datetime(string='Created At', readonly=True)

    _subscription_uniq = models.Constraint(
        'UNIQUE(account_id, event_type)',
        'Only one subscription per event type on an X account.',
    )

    def init(self):
        # A *partial* unique index cannot be expressed with models.Constraint:
        # Odoo emits it as a table constraint (UNIQUE(...) WHERE ...), which is
        # invalid SQL and made every upgrade log a syntax error. Create the
        # index directly instead — live subscription ids stay unique while the
        # '' placeholder for an id X did not echo stays unconstrained.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                x_twitter_subscription__subscription_id_uniq
            ON x_twitter_subscription (subscription_id)
            WHERE subscription_id IS NOT NULL AND subscription_id != ''
        """)


class XTwitterEvent(models.Model):
    """Tracks inbound webhook deliveries for dedup + idempotent processing.

    ``event_uuid`` is the X Activity API per-delivery id; delivering the same
    event again (X can resend) is ignored both at enqueue time and during
    processing, so handlers are safe to run repeatedly.
    """

    _name = 'x.twitter.event'
    _description = 'X Webhook Event'
    _order = 'create_date asc'

    event_uuid = fields.Char(string='Event UUID', index=True)
    account_id = fields.Many2one(
        'social.account',
        string='X Account',
        index=True,
        ondelete='set null',
    )
    event_type = fields.Char(string='Event Type')
    state = fields.Selection(
        [
            ('queued', 'Queued'),
            ('processing', 'Processing'),
            ('done', 'Processed'),
            ('failed', 'Failed'),
            ('skipped', 'Skipped'),
        ],
        string='State',
        default='queued',
        index=True,
    )
    error = fields.Text(string='Error', readonly=True)
    task_id = fields.Many2one(
        'x.account.task',
        string='Task',
        readonly=True,
        ondelete='set null',
    )
    payload = fields.Text(
        string='Payload',
        help='Normalized event payload (no OAuth credentials). Used by the task '
             'worker to reprocess on retry.',
    )
    conversation_id = fields.Char(
        string='Conversation ID',
        index=True,
        help='Conversation the delivery belongs to, extracted from the payload '
             'at create time so a conversation lookup stays indexed instead of '
             'LIKE-scanning the (large) payload text.',
    )
    has_key_change = fields.Boolean(
        string='Carries Key Change',
        index=True,
        help='True when the delivery carries a conversation_key_change_event '
             'blob.',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='account_id.company_id',
        store=True,
        index=True,
    )

    _event_uuid_uniq = models.Constraint(
        'UNIQUE(account_id, event_uuid)',
        'An X event uuid may only be processed once per account.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        key_changes = []
        for vals in vals_list:
            conversation_id, key_blob = \
                self._payload_conversation_fields(vals.get('payload'))
            vals.setdefault('conversation_id', conversation_id)
            vals.setdefault('has_key_change', bool(key_blob))
            account_id = vals.get('account_id')
            if account_id and conversation_id and key_blob:
                key_changes.append((account_id, conversation_id, key_blob))
        events = super().create(vals_list)
        if key_changes:
            # The blob is stored once per conversation instead of once per
            # delivery, which is what lets the event payload be dropped later.
            self.env['x.twitter.key.change'].sudo().store(key_changes)
        return events

    @api.model
    def _gc_processed_events(self, days=7, batch_size=2000):
        """Delete processed deliveries older than ``days``.

        ``event_uuid`` dedup only has to outlive X's redelivery window; keeping
        every processed row forever is what filled the database. Only terminal
        states are purged so a queued or retrying delivery is never dropped.
        """
        deadline = fields.Datetime.now() - timedelta(days=days)
        total = 0
        while True:
            self.env.cr.execute("""
                DELETE FROM x_twitter_event
                WHERE id IN (
                    SELECT id FROM x_twitter_event
                    WHERE state IN ('done', 'skipped', 'failed')
                      AND create_date < %s
                    LIMIT %s
                )
            """, (deadline, batch_size))
            deleted = self.env.cr.rowcount
            total += deleted
            if deleted < batch_size:
                break
        if total:
            _logger.info(
                'x_account_twitter: purged %s processed webhook event(s) '
                'older than %s day(s)', total, days)
        return total

    @staticmethod
    def _payload_conversation_fields(payload):
        """Extract ``(conversation_id, key_change_blob)`` from a stored payload.

        The stored payload is ``json.dumps({'payload': {...}})``. A malformed
        payload yields ``(False, False)`` instead of raising, so a bad row can
        never block ingest.
        """
        try:
            data = json.loads(payload or '{}')
        except ValueError:
            return False, False
        inner = (data or {}).get('payload') or {}
        conversation_id = inner.get('conversation_id')
        return (
            str(conversation_id) if conversation_id else False,
            inner.get('conversation_key_change_event') or False,
        )

