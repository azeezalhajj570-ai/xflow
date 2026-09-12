from odoo import api, fields, models


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
    x_partner_is_following = fields.Boolean(
        string='Following',
        compute='_compute_x_partner_is_following',
        readonly=True,
        help='Whether the channel X account already follows this member.',
    )

    @api.depends('partner_id', 'channel_id.x_account_id.x_following_ids')
    def _compute_x_partner_is_following(self):
        for member in self:
            following = member.channel_id.x_account_id.x_following_ids
            member.x_partner_is_following = member.partner_id in following