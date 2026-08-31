from odoo import _, api, fields, models


class AIOpenRouterModel(models.Model):
    _name = "ai.openrouter.model"
    _description = "OpenRouter Model"
    _order = "name"

    name = fields.Char(string="Name", required=True)
    external_id = fields.Char(string="External ID", required=True, index=True)
    description = fields.Text(string="Description")
    provider_id = fields.Many2one(
        comodel_name="ai.openrouter.provider",
        string="Provider",
        required=True,
        ondelete="cascade",
    )
    company_provider_id = fields.Many2one(
        comodel_name="ai.openrouter.company",
        string="Company Provider",
        ondelete="set null",
    )
    context_length = fields.Integer(string="Context Length")
    prompt_price = fields.Float(string="Prompt Price", digits=(16, 6))
    completion_price = fields.Float(string="Completion Price", digits=(16, 6))
    image_price = fields.Float(string="Image Price", digits=(16, 6))
    request_price = fields.Float(string="Request Price", digits=(16, 6))
    provider_name = fields.Char(string="Provider Name")
    modality = fields.Char(string="Modality")
    input_modality_ids = fields.Many2many(
        "ai.openrouter.modality",
        "ai_openrouter_model_input_modality_rel",
        "model_id", "modality_id",
        string="Input Modalities",
    )
    output_modality_ids = fields.Many2many(
        "ai.openrouter.modality",
        "ai_openrouter_model_output_modality_rel",
        "model_id", "modality_id",
        string="Output Modalities",
    )
    architecture_modality = fields.Char(string="Architecture Modality")
    architecture_tokenizer = fields.Char(string="Architecture Tokenizer")
    architecture_instruct_type = fields.Char(string="Architecture Instruct Type")
    top_provider_context_length = fields.Integer(string="Top Provider Context Length")
    top_provider_max_completion_tokens = fields.Integer(string="Top Provider Max Completion Tokens")
    top_provider_is_moderated = fields.Boolean(string="Top Provider Moderated")
    per_request_prompt_tokens = fields.Char(string="Per-Request Prompt Tokens")
    per_request_completion_tokens = fields.Char(string="Per-Request Completion Tokens")
    is_free = fields.Boolean(string="Free")
    is_embedding_model = fields.Boolean(string="Embedding Model", help="Can be used for embeddings")
    name_with_badge = fields.Char(string="Name", compute="_compute_name_with_badge", store=False)
    active = fields.Boolean(default=True)
    raw_payload = fields.Json(string="Raw Payload")

    _check_model_unique = models.Constraint(
        "UNIQUE(provider_id, external_id)",
        "This OpenRouter model already exists for this provider.",
    )

    @api.depends('name', 'is_free')
    def _compute_name_with_badge(self):
        for rec in self:
            rec.name_with_badge = '\U0001f193 %s' % rec.name if rec.is_free else rec.name

    def action_open_chat(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Chat with OpenRouter Model'),
            'res_model': 'ai.openrouter.chat.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_openrouter_model_id': self.id,
                'default_res_model': self.env.context.get('active_model'),
                'default_res_id': self.env.context.get('active_id'),
            },
        }
