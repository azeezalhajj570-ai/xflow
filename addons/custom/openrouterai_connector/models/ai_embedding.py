import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AIEmbedding(models.Model):
    _inherit = 'ai.embedding'

    def _get_active_openrouter_embedding_model(self):
        provider = self.env["ai.openrouter.provider"].sudo().search(
            [("active", "=", True), ("embedding_model_id", "!=", False)], limit=1
        )
        return provider.embedding_model_id.external_id if provider and provider.embedding_model_id else None

    def _ensure_embedding_model_in_selection(self):
        provider = self.env["ai.openrouter.provider"].sudo().search(
            [("active", "=", True), ("embedding_model_id", "!=", False)], limit=1
        )
        if not provider or not provider.embedding_model_id:
            _logger.info("OpenRouter: no embedding model configured on provider")
            return
        model_id = provider.embedding_model_id.external_id
        display = f"[OpenRouter] {provider.embedding_model_id.name}"
        _logger.info("OpenRouter: ensuring embedding model %s is in selection", model_id)
        try:
            from odoo.addons.ai.utils import llm_providers
            import odoo.addons.ai.models.ai_embedding as embedding_module

            for selection_list in [llm_providers.EMBEDDING_MODELS_SELECTION, embedding_module.EMBEDDING_MODELS_SELECTION]:
                if model_id not in [s[0] for s in selection_list]:
                    selection_list.append((model_id, display))
                    _logger.info("OpenRouter: added %s to EMBEDDING_MODELS_SELECTION", model_id)
        except ImportError as e:
            _logger.warning("OpenRouter: failed to patch EMBEDDING_MODELS_SELECTION: %s", e)

        field = self._fields.get('embedding_model')
        if field:
            if isinstance(field._selection, dict) and model_id not in field._selection:
                field._selection[model_id] = display
                _logger.info("OpenRouter: added %s to field._selection dict directly", model_id)
            if isinstance(field.selection, list) and model_id not in [s[0] for s in field.selection]:
                field.selection.append((model_id, display))
                _logger.info("OpenRouter: added %s to field.selection list directly", model_id)

    @api.model
    def _cron_generate_embedding(self, batch_size=100):
        self._ensure_embedding_model_in_selection()
        openrouter_embedding_model = self._get_active_openrouter_embedding_model()
        try:
            from odoo.addons.ai.utils import llm_providers
            import odoo.addons.ai.models.ai_embedding as embedding_module

            _original_fn = getattr(llm_providers, 'get_provider_for_embedding_model', None)

            def _patched_get_provider_for_embedding_model(env, embedding_model):
                if openrouter_embedding_model and embedding_model == openrouter_embedding_model:
                    return "openrouter"
                if _original_fn:
                    return _original_fn(env, embedding_model)
                raise UserError("No provider found for embedding model")

            llm_providers.get_provider_for_embedding_model = _patched_get_provider_for_embedding_model
            embedding_module.get_provider_for_embedding_model = _patched_get_provider_for_embedding_model

            _original_config_fn = getattr(llm_providers, 'get_embedding_config', None)

            def _patched_get_embedding_config(env, provider):
                if provider == "openrouter":
                    return {"max_batch_size": 2048, "max_tokens_per_request": 8000}
                if _original_config_fn:
                    return _original_config_fn(env, provider)
                return {"max_batch_size": 256, "max_tokens_per_request": 8000}

            llm_providers.get_embedding_config = _patched_get_embedding_config
            embedding_module.get_embedding_config = _patched_get_embedding_config
        except ImportError:
            _logger.warning("OpenRouter: could not patch embedding provider lookup")

        return super()._cron_generate_embedding(batch_size=batch_size)
