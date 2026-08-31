from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    openrouter_key_enabled = fields.Boolean(
        string="Enable OpenRouter account",
        compute='_compute_openrouter_key_enabled',
        readonly=False,
        inverse='_inverse_openrouter_key_enabled',
        groups='base.group_system',
    )
    openrouter_key = fields.Char(
        string="OpenRouter API key",
        config_parameter='ai.openrouter_key',
        readonly=False,
        groups='base.group_system',
    )

    openai_key_enabled = fields.Boolean(
        string="Enable custom OpenAI API key",
        compute='_compute_openai_key_enabled',
        readonly=False,
        inverse='_inverse_openai_key_enabled',
        groups='base.group_system',
    )
    google_key_enabled = fields.Boolean(
        string="Enable custom Google API key",
        compute='_compute_google_key_enabled',
        readonly=False,
        inverse='_inverse_google_key_enabled',
        groups='base.group_system',
    )

    @api.depends('openrouter_key')
    def _compute_openrouter_key_enabled(self):
        for record in self:
            record.openrouter_key_enabled = bool(record.openrouter_key)

    def _inverse_openrouter_key_enabled(self):
        for record in self:
            if not record.openrouter_key_enabled:
                record.openrouter_key = False

    @api.depends('openai_key')
    def _compute_openai_key_enabled(self):
        for record in self:
            record.openai_key_enabled = bool(record.openai_key)

    def _inverse_openai_key_enabled(self):
        for record in self:
            if not record.openai_key_enabled:
                self.env['ir.config_parameter'].sudo().set_param('ai.openai_key', False)

    @api.depends('google_key')
    def _compute_google_key_enabled(self):
        for record in self:
            record.google_key_enabled = bool(record.google_key)

    def _inverse_google_key_enabled(self):
        for record in self:
            if not record.google_key_enabled:
                self.env['ir.config_parameter'].sudo().set_param('ai.google_key', False)

    @api.model
    def set_values(self):
        super().set_values()
        for record in self:
            if record.openrouter_key_enabled and record.openrouter_key:
                provider = self.env['ai.openrouter.provider'].sudo().search([], limit=1)
                if not provider:
                    self.env['ai.openrouter.provider'].sudo().create({
                        'name': 'OpenRouter',
                        'api_key': record.openrouter_key,
                        'code': 'openrouter',
                        'active': True,
                        'base_url': 'https://openrouter.ai/api/v1',
                    })
                else:
                    provider.sudo().write({
                        'name': 'OpenRouter',
                        'api_key': record.openrouter_key,
                        'code': 'openrouter',
                        'active': True,
                    })

    def action_sync_openrouter_models(self):
        self.ensure_one()
        key = self.openrouter_key or self.env['ir.config_parameter'].sudo().get_param('ai.openrouter_key')
        provider = self.env['ai.openrouter.provider'].sudo().search([], limit=1)
        if not provider:
            provider = self.env['ai.openrouter.provider'].sudo().create({
                'name': 'OpenRouter',
                'api_key': key or '',
                'code': 'openrouter',
                'active': True,
                'base_url': 'https://openrouter.ai/api/v1',
            })
        elif key:
            provider.sudo().write({
                'name': 'OpenRouter',
                'api_key': key,
                'code': 'openrouter',
                'active': True,
            })
        return provider.action_open_sync_wizard()
