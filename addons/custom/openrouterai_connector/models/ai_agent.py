import json
import logging as _logging

from odoo import _, api, models
from odoo.exceptions import UserError

from odoo.addons.ai.models.ai_agent import TEMPERATURE_MAP

from ..services.openrouter_client import OpenRouterClient

_logger = _logging.getLogger(__name__)


class AIAgent(models.Model):
    _inherit = 'ai.agent'

    @api.model
    def _get_llm_model_selection(self):
        selection = super()._get_llm_model_selection()
        openrouter_models = self.env['ai.openrouter.model'].search([('active', '=', True)])
        for model in openrouter_models:
            selection.append((model.external_id, f"[OpenRouter] {model.name}"))
        return selection

    def _register_hook(self):
        super()._register_hook()
        if 'llm_model' in self._fields:
            self._fields['llm_model'].selection = type(self)._get_llm_model_selection

    def _get_embedding_model(self):
        self.ensure_one()
        provider = self._get_provider()
        if provider == "openrouter":
            openrouter_provider = self.env['ai.openrouter.provider'].sudo().search(
                [('active', '=', True)], limit=1
            )
            if openrouter_provider and openrouter_provider.embedding_model_id:
                return openrouter_provider.embedding_model_id.external_id
        return super()._get_embedding_model()

    def _log_openrouter_request(self, llm_model, request_body, response_json=None, error_message=None):
        provider = self.env["ai.openrouter.provider"].sudo().search([("active", "=", True)], limit=1)
        model = self.env["ai.openrouter.model"].sudo().search([("external_id", "=", llm_model)], limit=1)
        usage = OpenRouterClient.extract_usage(response_json or {})
        response_text = None
        if isinstance(response_json, dict):
            choices = response_json.get("choices") or []
            if choices:
                response_text = (choices[0].get("message") or {}).get("content")
        self.env["ai.openrouter.request.log"].sudo().create({
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

    def _request_llm_openrouter(self, llm_model, system_prompts, user_prompts, tools=None,
                                files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False):
        if files:
            raise NotImplementedError("OpenRouter chat completions does not support file uploads here.")
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

        provider = self.env["ai.openrouter.provider"].sudo().search([("active", "=", True)], limit=1)
        if not provider or not provider.api_key:
            raise UserError(_("No active OpenRouter provider with API key configured."))

        client = provider._get_client()
        try:
            llm_response = client.chat_completions(body)
        except Exception as exc:
            try:
                self._log_openrouter_request(llm_model, body, response_json={}, error_message=str(exc))
            except Exception:
                _logger.exception("OpenRouter: failed to log error request")
            raise

        if isinstance(llm_response, dict) and llm_response.get("error"):
            error_detail = llm_response["error"]
            error_msg = error_detail.get("message") or str(error_detail)
            _logger.error("OpenRouter API error: %s", error_msg)
            raise UserError(_("OpenRouter API error: %s") % error_msg)

        if not llm_response.get("choices"):
            _logger.warning("OpenRouter: no choices in response for model %s — full response: %s",
                            llm_model, json.dumps(llm_response)[:1000])

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

        try:
            self._log_openrouter_request(llm_model, body, response_json=llm_response)
        except Exception:
            _logger.exception("OpenRouter: failed to log successful request")

        return response_texts, to_call, next_inputs

    def _generate_response(self, prompt, chat_history=None, extra_system_context=""):
        self.ensure_one()
        provider = self._get_provider()
        if provider == "openrouter":
            import logging
            _logger = logging.getLogger(__name__)
            _logger.debug("[AI Prompt] %s", prompt)
            system_messages = self._build_system_context(extra_system_context=extra_system_context)
            if rag_context := self._build_rag_context(prompt):
                system_messages.extend(rag_context)

            AI_MAX_SUCCESSIVE_CALLS = int(self.env["ir.config_parameter"].sudo()
                .get_param("ai.max_successive_calls", "20"))

            all_responses = []
            inputs = (chat_history or []) + [{"role": "user", "content": prompt}]

            for api_call in range(AI_MAX_SUCCESSIVE_CALLS):
                responses, next_actions, inputs = self._request_llm_openrouter(
                    self.llm_model,
                    system_messages,
                    [],
                    inputs=inputs,
                    tools=self.sudo().topic_ids.tool_ids._get_ai_tools(),
                    temperature=TEMPERATURE_MAP[self.response_style],
                )
                all_responses.extend(responses)

                if not next_actions:
                    break

                for tool_name, call_id, arguments in next_actions:
                    tool = (self.sudo().topic_ids.tool_ids._get_ai_tools() or {}).get(tool_name)
                    if tool:
                        _, _, func, _ = tool
                        result, error = func(arguments=arguments)
                        inputs.append({"role": "tool", "tool_call_id": call_id, "content": str(result or error or "")})
                    else:
                        inputs.append({"role": "tool", "tool_call_id": call_id, "content": f"Error: unknown tool '{tool_name}'"})

            if not all_responses:
                raise ValueError("Processing loop ended with no response.")

            _logger.info("AI: API calls %s", api_call + 1)

            if rag_context:
                all_responses = self._get_llm_response_with_sources(all_responses)

            return all_responses

        return super()._generate_response(
            prompt, chat_history=chat_history, extra_system_context=extra_system_context
        )
