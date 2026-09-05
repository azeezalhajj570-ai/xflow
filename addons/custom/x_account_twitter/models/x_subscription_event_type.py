# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class XSubscriptionEventType(models.Model):
    _name = 'x.subscription.event.type'
    _description = 'X Subscription Event Type'
    _order = 'sequence, name'

    name = fields.Char(
        string='Event Type',
        required=True,
        help='Technical event type identifier (e.g., dm.received, chat.received).',
    )
    description = fields.Char(
        string='Description',
        help='Human-readable description of the event type.',
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order in selection lists.',
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Inactive event types are not available for subscription.',
    )

    _sql_constraints = [
        ('name_unique', 'UNIQUE(name)',
         'Event type name must be unique.'),
    ]
