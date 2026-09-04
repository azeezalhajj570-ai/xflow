# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Persist the X webhook registration + X Activity API subscriptions.

The official X V2 Webhooks API exposes one webhook registration per app; the X
Activity API (XAA) exposes one-or-more subscriptions per (app, user, event).
These models record that state so registration/subscription lifecycle calls are
safe to run repeatedly (idempotent: never duplicate what already exists).
"""

from odoo import fields, models


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
    _subscription_id_uniq = models.Constraint(
        'UNIQUE(subscription_id)',
        'An X subscription id must be unique.',
    )


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
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='account_id.company_id',
        store=True,
        index=True,
    )

    _event_uuid_uniq = models.Constraint(
        'UNIQUE(event_uuid)',
        'An X event uuid may only be processed once.',
    )

