# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_user_id = fields.Char(string='X User ID', index=True)
    x_username = fields.Char(string='X Username', index=True)
    x_following = fields.Boolean(string='X Following')
    x_blocked = fields.Boolean(string='X Blocked')
    x_is_verified = fields.Boolean(string='X Verified', help='X account is verified.')
    x_is_blue_verified = fields.Boolean(
        string='X Blue Verified',
        help='X account has a Blue (paid) verification badge.',
    )
