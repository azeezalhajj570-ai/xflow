"""Patch ai.utils.llm_providers to add OpenRouter provider entries.

Resilient against Odoo 19 API changes via try/except guards.
"""
import logging

_logger = logging.getLogger(__name__)

try:
    from odoo.addons.ai.utils import llm_providers
    PATCH_PROVIDERS = True
except (ImportError, AttributeError):
    llm_providers = None
    PATCH_PROVIDERS = False
    _logger.warning("OpenRouter: ai.utils.llm_providers not found — provider patches disabled")


if PATCH_PROVIDERS:
    Provider = llm_providers.Provider
    _original_get_provider = llm_providers.get_provider
    _original_get_provider_for_embedding_model = llm_providers.get_provider_for_embedding_model

    if not any(p.name == "openrouter" for p in llm_providers.PROVIDERS):
        llm_providers.PROVIDERS.append(
            Provider(
                "openrouter",
                "OpenRouter",
                "",
                {"max_batch_size": 2048, "max_tokens_per_request": 8000},
                [],
            )
        )

    llm_providers.EMBEDDING_MODELS_SELECTION[:] = [
        (provider.embedding_model, provider.display_name)
        for provider in llm_providers.PROVIDERS
    ]

    def _get_provider(env, llm_model):
        try:
            return _original_get_provider(env, llm_model)
        except Exception:
            if env and env["ai.openrouter.model"].sudo().search_count(
                [("external_id", "=", llm_model)], limit=1
            ):
                return "openrouter"
            raise

    llm_providers.get_provider = _get_provider

    def _get_provider_for_embedding_model(env, embedding_model):
        if env and embedding_model:
            model = env["ai.openrouter.model"].sudo().search(
                [("external_id", "=", embedding_model), ("is_embedding_model", "=", True)], limit=1
            )
            if model:
                openrouter_provider = env["ai.openrouter.provider"].sudo().search(
                    [("active", "=", True), ("embedding_model_id", "=", model.id)], limit=1
                )
                if openrouter_provider:
                    return "openrouter"
        return _original_get_provider_for_embedding_model(env, embedding_model)

    llm_providers.get_provider_for_embedding_model = _get_provider_for_embedding_model

    # Also patch the direct import in ai_agent.py — it uses
    # `from odoo.addons.ai.utils.llm_providers import get_provider`,
    # so swapping llm_providers.get_provider isn't enough.
    try:
        import odoo.addons.ai.models.ai_agent as ai_agent_module
        ai_agent_module.get_provider = _get_provider
    except (ImportError, AttributeError):
        _logger.warning("OpenRouter: could not patch ai_agent.get_provider reference")

    # Also patch the embedding model module
    try:
        import odoo.addons.ai.models.ai_embedding as ai_embedding_module
        ai_embedding_module.get_provider_for_embedding_model = _get_provider_for_embedding_model
    except (ImportError, AttributeError):
        _logger.warning("OpenRouter: could not patch ai_embedding.get_provider_for_embedding_model")
