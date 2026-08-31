from odoo import fields, models


class AIOpenRouterModality(models.Model):
    _name = "ai.openrouter.modality"
    _description = "OpenRouter Modality"
    _order = "name"

    name = fields.Char(string="Name", required=True, index=True)

    _check_name_unique = models.Constraint(
        "UNIQUE(name)", "Modality name must be unique."
    )
