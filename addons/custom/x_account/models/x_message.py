# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class XMessage(models.Model):
    """Mirror of an external X message, mapping X external id ↔ Odoo Discuss/mail
    message. Carries external identity that mail.message alone cannot represent."""

    _name = 'x.message'
    _description = 'X Message'
    _order = 'external_created_at asc'

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

    _sql_constraints = [
        (
            'external_id_uniq',
            'UNIQUE(channel_id, external_id)',
            'An external X message id must be unique per channel.',
        ),
    ]
