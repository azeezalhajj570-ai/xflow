# -*- coding: utf-8 -*-
import json

from odoo import _, fields, models
from odoo.exceptions import UserError


class AIOpenRouterSyncWizard(models.TransientModel):
    _name = "ai.openrouter.sync.wizard"
    _description = "Sync OpenRouter Models"

    provider_id = fields.Many2one(
        comodel_name="ai.openrouter.provider",
        string="Provider",
        required=True,
        ondelete="cascade",
    )
    deactivate_missing = fields.Boolean(string="Deactivate Missing Models", default=True)

    def action_sync(self):
        self.ensure_one()
        provider = self.provider_id
        client = provider._get_client()
        payload = client.list_models()

        models_data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(models_data, list):
            raise UserError(_("Unexpected OpenRouter models response format"))

        OpenRouterModel = self.env["ai.openrouter.model"].sudo()
        OpenRouterCompany = self.env["ai.openrouter.company"].sudo()
        OpenRouterModality = self.env["ai.openrouter.modality"].sudo()
        seen_ids = set()

        for item in models_data:
            external_id = item.get("id") or item.get("model")
            if not external_id:
                continue

            pricing = item.get("pricing") or {}
            prompt_price = _safe_float(pricing.get("prompt"))
            completion_price = _safe_float(pricing.get("completion"))
            image_price = _safe_float(pricing.get("image"))
            request_price = _safe_float(pricing.get("request"))

            architecture = item.get("architecture") or {}
            modality = item.get("modality") or architecture.get("modality")
            if isinstance(modality, list):
                modality = ", ".join(modality)
            elif isinstance(modality, dict):
                modality = ", ".join([str(value) for value in modality.values()])


            input_modality_ids = []
            output_modality_ids = []
            
            # Helper to get/create modality ID
            def get_modality_id(name):
                name = name.strip()
                if not name:
                    return False
                mod = OpenRouterModality.search([("name", "=ilike", name)], limit=1)
                if not mod:
                    mod = OpenRouterModality.create({"name": name})
                return mod.id

            if modality and "->" in modality:
                parts = modality.split("->")
                if len(parts) == 2:
                    inputs = parts[0].split("+")
                    outputs = parts[1].split("+")
                    for inp in inputs:
                        if mid := get_modality_id(inp):
                            input_modality_ids.append(mid)
                    for out in outputs:
                        if mid := get_modality_id(out):
                            output_modality_ids.append(mid)
            elif modality:
                # If no arrow, assume it applies to both or just general capability?
                # Usually architecture.modality is mostly reliable. 
                # If it's a list, we might want to treat all as input/output or checks docs.
                # For now, let's treat comma separated strings as tags for both?
                # Or just ignore if not in input->output format which is cleaner.
                # However, user example `text+image` implies combined capability.
                pass

            top_provider = item.get("top_provider") or {}
            provider_name = (
                item.get("provider")
                or item.get("provider_name")
                or top_provider.get("name")
                or item.get("owned_by")
            )
            provider_code = (
                item.get("provider")
                or item.get("provider_name")
                or top_provider.get("id")
                or item.get("owned_by")
            )

            display_name = item.get("name") or ""
            if not provider_name and ":" in display_name:
                provider_name = display_name.split(":", 1)[0].strip()
            if not provider_code and external_id and "/" in external_id:
                provider_code = external_id.split("/", 1)[0].strip()

            company_provider = False
            if provider_name:
                company_provider = OpenRouterCompany.search(
                    [("external_code", "=", provider_code or provider_name)],
                    limit=1,
                )
                if not company_provider:
                    company_provider = OpenRouterCompany.create({
                        "name": provider_name,
                        "external_code": provider_code or provider_name,
                        "active": True,
                    })

            vals = {
                "name": item.get("name") or external_id,
                "external_id": external_id,
                "description": item.get("description"),
                "provider_id": provider.id,
                "company_provider_id": company_provider.id if company_provider else False,
                "context_length": item.get("context_length") or 0,
                "prompt_price": prompt_price,
                "completion_price": completion_price,
                "image_price": image_price,
                "request_price": request_price,
                "provider_name": provider_name,
                "modality": modality,
                "input_modality_ids": [(6, 0, input_modality_ids)],
                "output_modality_ids": [(6, 0, output_modality_ids)],
                "architecture_modality": architecture.get("modality"),
                "architecture_tokenizer": architecture.get("tokenizer"),
                "architecture_instruct_type": architecture.get("instruct_type"),
                "top_provider_context_length": top_provider.get("context_length"),
                "top_provider_max_completion_tokens": top_provider.get("max_completion_tokens"),
                "top_provider_is_moderated": bool(top_provider.get("is_moderated")),
                "per_request_prompt_tokens": (item.get("per_request_limits") or {}).get("prompt_tokens"),
                "per_request_completion_tokens": (item.get("per_request_limits") or {}).get("completion_tokens"),
                "is_free": bool(item.get("is_free")) or (prompt_price == 0 and completion_price == 0),
                "active": True,
                "raw_payload": item,
            }

            existing = OpenRouterModel.search(
                [
                    ("provider_id", "=", provider.id),
                    ("external_id", "=", external_id),
                ],
                limit=1,
            )
            if existing:
                existing.write(vals)
            else:
                OpenRouterModel.create(vals)
            seen_ids.add(external_id)

        if self.deactivate_missing:
            to_deactivate = OpenRouterModel.search([
                ("provider_id", "=", provider.id),
                ("external_id", "not in", list(seen_ids)),
                ("is_embedding_model", "!=", True),
            ])
            if to_deactivate:
                to_deactivate.write({"active": False})

        # Sync embedding models
        try:
            embedding_payload = client.list_embedding_models()
            embedding_data = embedding_payload.get("data") if isinstance(embedding_payload, dict) else None
            if isinstance(embedding_data, list):
                for item in embedding_data:
                    external_id = item.get("id") or item.get("model")
                    if not external_id:
                        continue
                    arch = item.get("architecture") or {}
                    pricing = item.get("pricing") or {}
                    top_provider = item.get("top_provider") or {}

                    existing = OpenRouterModel.search([
                        ("provider_id", "=", provider.id),
                        ("external_id", "=", external_id),
                    ], limit=1)
                    vals = {
                        "name": item.get("name") or external_id,
                        "external_id": external_id,
                        "description": item.get("description"),
                        "provider_id": provider.id,
                        "context_length": item.get("context_length") or 0,
                        "prompt_price": _safe_float(pricing.get("prompt")),
                        "completion_price": _safe_float(pricing.get("completion")),
                        "image_price": _safe_float(pricing.get("image")),
                        "request_price": _safe_float(pricing.get("request")),
                        "is_free": bool(item.get("is_free")),
                        "is_embedding_model": True,
                        "active": True,
                        "architecture_modality": arch.get("modality"),
                        "architecture_tokenizer": arch.get("tokenizer"),
                        "description": item.get("description"),
                        "raw_payload": item,
                    }
                    if existing:
                        existing.write(vals)
                    else:
                        OpenRouterModel.create(vals)
        except Exception as exc:
            _logger.warning("OpenRouter: failed to sync embedding models: %s", exc)

        return {"type": "ir.actions.act_window_close"}


def _safe_float(value):
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
