"""Patch ai.utils.llm_api_service to add OpenRouter support.

Loaded by openrouterai_connector to avoid editing core ai module files.
Resilient against Odoo 19 API changes via try/except guards.
"""
import json
import logging

from odoo import _
from odoo.exceptions import UserError

from ..services.openrouter_client import OpenRouterClient

_logger = logging.getLogger(__name__)

try:
    from odoo.addons.ai.utils import llm_api_service
    LLMApiService = llm_api_service.LLMApiService
    PATCH_AI_SERVICE = True
except (ImportError, AttributeError):
    LLMApiService = None
    llm_api_service = None
    PATCH_AI_SERVICE = False
    _logger.warning("OpenRouter: ai.utils.llm_api_service not found — patches disabled")


class ImageString(str):
    def __new__(cls, content, image_data):
        obj = str.__new__(cls, content)
        obj.image_data = image_data
        obj.type = 'image'
        return obj


def _log_openrouter_request(env, llm_model, request_body, response_json=None, error_message=None):
    provider = env["ai.openrouter.provider"].sudo().search([("active", "=", True)], limit=1)
    model = env["ai.openrouter.model"].sudo().search([("external_id", "=", llm_model)], limit=1)
    usage = OpenRouterClient.extract_usage(response_json or {})

    response_text = None
    if isinstance(response_json, dict):
        choices = response_json.get("choices") or []
        if choices:
            response_text = (choices[0].get("message") or {}).get("content")

    env["ai.openrouter.request.log"].sudo().create({
        "provider_id": provider.id if provider else False,
        "model_id": model.id if model else False,
        "generation_id": (response_json or {}).get("id"),
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
        "response_text": response_text if not error_message else error_message,
        "request_payload": json.dumps(request_body, ensure_ascii=False),
        "response_payload": json.dumps(response_json, ensure_ascii=False) if response_json else False,
        "state": "error" if error_message else "success",
    })


def _init(self, env, provider="openai"):
    if provider == "openrouter":
        self.provider = provider
        self.base_url = "https://openrouter.ai/api/v1"
        self.env = env
        return
    return _original_init(self, env, provider)


def _get_api_token(self):
    if self.provider == "openrouter":
        provider = self.env["ai.openrouter.provider"].sudo().search([("active", "=", True)], limit=1)
        if provider and provider.api_key:
            return provider.api_key
        param = self.env["ir.config_parameter"].sudo().get_param("ai.openrouter_key")
        if param:
            return param
        if llm_api_service:
            return llm_api_service.os.getenv("OPENROUTER_API_KEY") or ""
        return ""
    return _original_get_api_token(self)


def _request_llm(self, *args, **kwargs):
    if self.provider == "openrouter":
        return _request_llm_openrouter(self, *args, **kwargs)
    return _original_request_llm(self, *args, **kwargs)


def _build_tool_call_response(self, tool_call_id, return_value):
    if self.provider == "openrouter":
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": str(return_value),
        }
    return _original_build_tool_call_response(self, tool_call_id, return_value)


def _request_llm_openrouter(
    self, llm_model, system_prompts, user_prompts, tools=None,
    files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False
):
    if web_grounding:
        raise NotImplementedError("OpenRouter web grounding is not supported in this integration.")
    if schema:
        raise NotImplementedError("OpenRouter does not support structured output in this integration.")

    messages = []
    for prompt in system_prompts or []:
        messages.append({"role": "system", "content": prompt})
    for prompt in user_prompts or []:
        messages.append({"role": "user", "content": prompt})
    if inputs:
        messages.extend(inputs)

    if files:
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("role") == "user":
                content = messages[idx].get("content", "")
                parts = []
                if content and isinstance(content, str):
                    parts.append({"type": "text", "text": content})
                elif content and isinstance(content, list):
                    parts = content
                else:
                    parts.append({"type": "text", "text": ""})
                for f in files:
                    if f["mimetype"].startswith("image/"):
                        parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{f['mimetype']};base64,{f['value']}"
                            }
                        })
                    elif f["mimetype"] == "text/plain":
                        parts.append({"type": "text", "text": f["value"]})
                messages[idx]["content"] = parts
                break

    body = {
        "model": llm_model,
        "messages": messages,
        "temperature": temperature,
    }

    if tools:
        body["tools"] = [{
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_description,
                "parameters": tool_parameter_schema,
            },
        } for tool_name, (tool_description, __, __, tool_parameter_schema) in tools.items()]

    try:
        llm_response = self._request(
            "post",
            "/chat/completions",
            self._get_base_headers(),
            body,
        )
    except Exception as exc:
        try:
            _log_openrouter_request(
                self.env,
                llm_model,
                body,
                response_json={},
                error_message=str(exc),
            )
        except Exception:
            _logger.exception("OpenRouter: failed to log error request")
        raise

    if isinstance(llm_response, dict) and llm_response.get("error"):
        error_detail = llm_response["error"]
        error_msg = error_detail.get("message") or str(error_detail)
        _logger.error("OpenRouter API error: %s", error_msg)
        raise UserError(_("OpenRouter API error: %s") % error_msg)

    if not llm_response.get("choices"):
        _logger.warning("OpenRouter: no choices in response for model %s — full response: %s", llm_model, json.dumps(llm_response)[:1000])

    to_call = []
    response_texts = []
    next_inputs = list(inputs or ())

    choices = llm_response.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        if tool_calls := message.get("tool_calls"):
            for call in tool_calls:
                func = call.get("function") or {}
                try:
                    arguments = json.loads(func.get("arguments") or "{}")
                except json.decoder.JSONDecodeError:
                    _logger.warning("OpenRouter: Malformed arguments: %s", call)
                    continue
                to_call.append((func.get("name"), call.get("id"), arguments))
            next_inputs.append({"role": "assistant", "tool_calls": tool_calls})
        msg_content = message.get("content")
        if msg_content:
            if isinstance(msg_content, str):
                response_texts.append(msg_content)
            elif isinstance(msg_content, list):
                for part in msg_content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            response_texts.append(part.get("text"))
                            url = part.get("image_url", {}).get("url", "")
                            if url.startswith("data:image"):
                                try:
                                    base64_data = url.split(",", 1)[1]
                                    response_texts.append(ImageString("[IMAGE GENERATED]", base64_data))
                                except IndexError:
                                    pass
                            else:
                                response_texts.append(f"![Image]({url})")

    try:
        _log_openrouter_request(self.env, llm_model, body, response_json=llm_response)
    except Exception:
        _logger.exception("OpenRouter: failed to log successful request")

    return response_texts, to_call, next_inputs


if PATCH_AI_SERVICE:
    _original_init = LLMApiService.__init__
    _original_get_api_token = LLMApiService._get_api_token
    _original_request_llm = LLMApiService._request_llm
    _original_build_tool_call_response = LLMApiService._build_tool_call_response

    LLMApiService.__init__ = _init
    LLMApiService._get_api_token = _get_api_token
    LLMApiService._request_llm = _request_llm
    LLMApiService._build_tool_call_response = _build_tool_call_response
    LLMApiService._request_llm_openrouter = _request_llm_openrouter
