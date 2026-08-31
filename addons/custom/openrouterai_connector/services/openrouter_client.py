# -*- coding: utf-8 -*-
import json
import logging

import requests

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class OpenRouterClient:
    def __init__(self, api_key, base_url="https://openrouter.ai/api/v1", app_url=None, app_name=None, timeout=30):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.app_url = app_url
        self.app_name = app_name
        self.timeout = timeout

    def _headers(self):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.app_url:
            headers["HTTP-Referer"] = self.app_url
        if self.app_name:
            headers["X-Title"] = self.app_name
        return headers

    def list_models(self):
        url = f"{self.base_url}/models"
        try:
            response = requests.get(url, headers=self._headers(), timeout=self.timeout)
        except Exception as exc:
            _logger.exception("OpenRouter models request failed")
            raise UserError(_("OpenRouter request failed: %s") % exc)

        if response.status_code >= 400:
            _logger.error("OpenRouter models error %s: %s", response.status_code, response.text)
            raise UserError(_("OpenRouter error %s: %s") % (response.status_code, response.text))

        try:
            return response.json()
        except json.JSONDecodeError:
            raise UserError(_("OpenRouter returned invalid JSON for models list"))

    def list_embedding_models(self):
        url = f"{self.base_url}/embeddings/models"
        try:
            response = requests.get(url, headers=self._headers(), timeout=self.timeout)
        except Exception as exc:
            _logger.exception("OpenRouter embedding models request failed")
            raise UserError(_("OpenRouter embedding models request failed: %s") % exc)

        if response.status_code >= 400:
            _logger.error("OpenRouter embedding models error %s: %s", response.status_code, response.text)
            raise UserError(_("OpenRouter embedding models error %s: %s") % (response.status_code, response.text))

        try:
            return response.json()
        except json.JSONDecodeError:
            raise UserError(_("OpenRouter returned invalid JSON for embedding models"))

    def chat_completions(self, payload):
        url = f"{self.base_url}/chat/completions"
        try:
            response = requests.post(
                url,
                headers=self._headers(),
                data=json.dumps(payload),
                timeout=self.timeout,
            )
        except Exception as exc:
            _logger.exception("OpenRouter chat completion request failed")
            raise UserError(_("OpenRouter request failed: %s") % exc)

        if response.status_code >= 400:
            _logger.error("OpenRouter chat completion error %s: %s", response.status_code, response.text)
            raise UserError(_("OpenRouter error %s: %s") % (response.status_code, response.text))

        try:
            return response.json()
        except json.JSONDecodeError:
            raise UserError(_("OpenRouter returned invalid JSON for chat completions"))

    def get_generation(self, generation_id):
        url = f"{self.base_url}/generation"
        try:
            response = requests.get(
                url,
                headers=self._headers(),
                params={"id": generation_id},
                timeout=self.timeout,
            )
        except Exception as exc:
            _logger.exception("OpenRouter generation request failed")
            raise UserError(_("OpenRouter request failed: %s") % exc)

        if response.status_code >= 400:
            _logger.error("OpenRouter generation error %s: %s", response.status_code, response.text)
            raise UserError(_("OpenRouter error %s: %s") % (response.status_code, response.text))

        try:
            return response.json()
        except json.JSONDecodeError:
            raise UserError(_("OpenRouter returned invalid JSON for generation"))

    @staticmethod
    def extract_usage(response_json):
        usage = (response_json or {}).get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        cost_details = usage.get("cost_details") or {}
        return {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "reasoning_tokens": completion_details.get("reasoning_tokens"),
            "cached_tokens": prompt_details.get("cached_tokens"),
            "cache_write_tokens": prompt_details.get("cache_write_tokens"),
            "audio_tokens": prompt_details.get("audio_tokens"),
            "cost": usage.get("cost"),
            "upstream_inference_cost": cost_details.get("upstream_inference_cost"),
            "usage_payload": usage,
        }
