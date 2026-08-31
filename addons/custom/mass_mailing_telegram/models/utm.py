from odoo import api, fields, models


class UtmCampaign(models.Model):
    _inherit = 'utm.campaign'

    mailing_telegram_ids = fields.One2many(
        'mailing.mailing', 'campaign_id',
        domain=[('mailing_type', '=', 'telegram')],
        string='Mass Telegram',
    )
    mailing_telegram_count = fields.Integer(
        'Number of Mass Telegram',
        compute='_compute_mailing_telegram_count',
    )

    @api.depends('mailing_telegram_ids')
    def _compute_mailing_telegram_count(self):
        for campaign in self:
            campaign.mailing_telegram_count = len(campaign.mailing_telegram_ids)


class UtmMedium(models.Model):
    _inherit = 'utm.medium'

    @property
    def SELF_REQUIRED_UTM_MEDIUMS_REF(self):
        return super().SELF_REQUIRED_UTM_MEDIUMS_REF | {"mass_mailing_telegram.utm_medium_telegram": "Telegram"}
