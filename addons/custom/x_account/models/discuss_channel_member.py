from odoo import fields, models


class DiscussChannelMember(models.Model):
    _inherit = 'discuss.channel.member'

    x_partner_name = fields.Char(
        string='Member Name',
        related='partner_id.name',
        readonly=True,
    )
    x_partner_username = fields.Char(
        string='Username',
        related='partner_id.x_username',
        readonly=True,
    )
    x_partner_verified = fields.Boolean(
        string='Verified',
        related='partner_id.x_is_verified',
        readonly=True,
    )
    x_partner_blue_verified = fields.Boolean(
        string='Blue Verified',
        related='partner_id.x_is_blue_verified',
        readonly=True,
    )