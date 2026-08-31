import json
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..services.openrouter_client import OpenRouterClient

_logger = logging.getLogger(__name__)


class AIOpenRouterProvider(models.Model):
    _name = "ai.openrouter.provider"
    _description = "OpenRouter AI Provider"

    name = fields.Char(string="Name", default="OpenRouter", required=True)
    active = fields.Boolean(default=True)
    code = fields.Selection(
        selection=[("openrouter", "OpenRouter")],
        string="Provider Code",
        default="openrouter",
        required=True,
    )
    api_key = fields.Char(string="API Key")
    base_url = fields.Char(string="Base URL", default="https://openrouter.ai/api/v1", required=True)
    app_url = fields.Char(string="App URL")
    app_name = fields.Char(string="App Name")
    embedding_model_id = fields.Many2one(
        comodel_name="ai.openrouter.model",
        string="Embedding Model",
        domain=[("active", "=", True), ("is_embedding_model", "=", True)],
        help="Model used for generating embeddings. Synced from OpenRouter's embeddings endpoint.",
    )
    default_route = fields.Selection(
        selection=[
            ("auto", "Auto"),
            ("low_cost", "Lowest Cost"),
            ("low_latency", "Lowest Latency"),
        ],
        string="Default Route",
        default="auto",
    )

    def _get_client(self):
        self.ensure_one()
        if not self.api_key:
            raise UserError(_("OpenRouter API key is required"))
        return OpenRouterClient(
            api_key=self.api_key,
            base_url=self.base_url,
            app_url=self.app_url,
            app_name=self.app_name,
        )

    def request_chat_completion(self, payload, model_id=None):
        self.ensure_one()
        client = self._get_client()
        response = client.chat_completions(payload)

        usage = OpenRouterClient.extract_usage(response)
        response_text = None
        choices = (response or {}).get("choices") or []
        if choices:
            response_text = (choices[0].get("message") or {}).get("content")

        self.env["ai.openrouter.request.log"].sudo().create({
            "provider_id": self.id,
            "model_id": model_id,
            "generation_id": (response or {}).get("id"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "cached_tokens": usage.get("cached_tokens"),
            "cache_write_tokens": usage.get("cache_write_tokens"),
            "audio_tokens": usage.get("audio_tokens"),
            "total_cost": usage.get("cost") or 0.0,
            "upstream_inference_cost": usage.get("upstream_inference_cost") or 0.0,
            "usage_payload": usage.get("usage_payload"),
            "response_text": response_text,
            "request_payload": json.dumps(payload, ensure_ascii=False),
            "response_payload": json.dumps(response, ensure_ascii=False),
            "state": "success",
        })
        return response

    def action_open_sync_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sync OpenRouter Models'),
            'res_model': 'ai.openrouter.sync.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_provider_id': self.id},
        }
