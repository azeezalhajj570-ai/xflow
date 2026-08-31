# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AIOpenRouterChatWizard(models.TransientModel):
    _name = "ai.openrouter.chat.wizard"
    _description = "OpenRouter Chat Wizard"

    openrouter_model_id = fields.Many2one(
        comodel_name="ai.openrouter.model",
        string="OpenRouter Model",
        required=True,
        domain=[("active", "=", True)],
    )
    user_prompt = fields.Text(string="Prompt")
    res_model = fields.Char(string="Record Model")
    res_id = fields.Integer(string="Record ID")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        res.setdefault("res_model", self.env.context.get("active_model"))
        res.setdefault("res_id", self.env.context.get("active_id"))
        return res

    def action_open_chat(self):
        self.ensure_one()
        if not self.openrouter_model_id:
            raise UserError(_("Please select an OpenRouter model."))

        provider = self.env["ai.openrouter.provider"].sudo().search([
            ("active", "=", True),
        ], limit=1)
        if not provider:
            raise UserError(_(
                "No active OpenRouter provider found. "
                "Please configure one in General Settings > AI > OpenRouter."
            ))
        if not provider.api_key:
            raise UserError(_(
                "OpenRouter API key is not configured. "
                "Please set it in General Settings > AI > OpenRouter."
            ))

        agent = self.env["ai.agent"].search([
            ("llm_model", "=", self.openrouter_model_id.external_id),
        ], limit=1)
        if not agent:
            agent = self.env["ai.agent"].create({
                "name": f"OpenRouter: {self.openrouter_model_id.name}",
                "llm_model": self.openrouter_model_id.external_id,
                "subtitle": "OpenRouter Chat",
            })
            if agent.partner_id:
                agent.partner_id.write({
                    "name": f"OpenRouter: {self.openrouter_model_id.name}",
                })

        action = agent.open_agent_chat()
        if self.user_prompt:
            action.setdefault("params", {})
            action["params"]["user_prompt"] = self.user_prompt

        if self.res_model and self.res_id:
            record = self.env[self.res_model].browse(self.res_id)
            if record.exists():
                channel = self.env["discuss.channel"].sudo().search([
                    ("id", "=", action.get("params", {}).get("channelId"))
                ], limit=1)
                if channel:
                    channel.ai_env_context = [
                        self.env._(
                            "Record context: model=%(model)s, id=%(id)s, name=%(name)s",
                            model=record._name,
                            id=record.id,
                            name=record.display_name,
                        )
                    ]

        return action
