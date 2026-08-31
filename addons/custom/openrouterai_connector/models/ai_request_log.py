# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import api, fields, models

from ..services.openrouter_client import OpenRouterClient

_logger = logging.getLogger(__name__)


class AIOpenRouterRequestLog(models.Model):
    _name = "ai.openrouter.request.log"
    _description = "OpenRouter Request Log"
    _order = "request_ts desc"

    provider_id = fields.Many2one("ai.openrouter.provider", string="Provider", index=True)
    model_id = fields.Many2one("ai.openrouter.model", string="Model", index=True)
    generation_id = fields.Char(string="Generation ID", index=True)

    response_attachment_id = fields.Many2one(
        "ir.attachment",
        string="Response Payload (File)",
        ondelete="set null",
    )
    request_attachment_id = fields.Many2one(
        "ir.attachment",
        string="Request Payload (File)",
        ondelete="set null",
    )
    response_payload_preview = fields.Text(
        string="Response Payload (Preview)",
        compute="_compute_response_preview",
        store=False,
    )
    request_payload_preview = fields.Text(
        string="Request Payload (Preview)",
        compute="_compute_request_preview",
        store=False,
    )
    request_ts = fields.Datetime(string="Request Time", default=fields.Datetime.now, index=True)
    prompt_tokens = fields.Integer(string="Prompt Tokens")
    completion_tokens = fields.Integer(string="Completion Tokens")
    total_tokens = fields.Integer(string="Total Tokens")
    reasoning_tokens = fields.Integer(string="Reasoning Tokens")
    cached_tokens = fields.Integer(string="Cached Tokens")
    cache_write_tokens = fields.Integer(string="Cache Write Tokens")
    audio_tokens = fields.Integer(string="Audio Tokens")
    total_cost = fields.Float(string="Total Cost", digits=(16, 6))
    upstream_inference_cost = fields.Float(string="Upstream Inference Cost", digits=(16, 6))
    usage_payload = fields.Json(string="Usage Payload")
    response_text = fields.Text(string="Response Text")
    request_payload = fields.Text(string="Request Payload", groups="base.group_system")
    response_payload = fields.Text(string="Response Payload", groups="base.group_system")
    state = fields.Selection(
        selection=[
            ("success", "Success"),
            ("error", "Error"),
        ],
        string="State",
        default="success",
        index=True,
    )

    def _compute_response_preview(self):
        for rec in self:
            if rec.response_attachment_id:
                rec.response_payload_preview = "Payload stored as attachment (%s bytes)" % rec.response_attachment_id.file_size
            else:
                rec.response_payload_preview = rec.response_payload

    def _compute_request_preview(self):
        for rec in self:
            if rec.request_attachment_id:
                rec.request_payload_preview = "Payload stored as attachment (%s bytes)" % rec.request_attachment_id.file_size
            else:
                rec.request_payload_preview = rec.request_payload

    @api.model
    def cron_reconcile_usage(self, limit=200, hours_back=48):
        """Backfill usage from OpenRouter generation endpoint for incomplete rows."""
        import base64
        cutoff = fields.Datetime.now() - timedelta(hours=hours_back)
        domain = [
            ("state", "=", "success"),
            ("generation_id", "!=", False),
            ("request_ts", ">=", cutoff),
            "|",
            ("usage_payload", "=", False),
            ("total_tokens", "=", False),
        ]
        rows = self.search(domain, order="request_ts desc", limit=limit)
        if not rows:
            return 0

        reconciled = 0
        for row in rows:
            provider = row.provider_id or self.env["ai.openrouter.provider"].sudo().search(
                [("active", "=", True)], limit=1
            )
            if not provider:
                _logger.warning("OpenRouter reconcile: no active provider for log %s", row.id)
                continue

            try:
                payload = provider._get_client().get_generation(row.generation_id)
            except Exception as exc:
                _logger.warning(
                    "OpenRouter reconcile: generation lookup failed for %s (%s): %s",
                    row.id,
                    row.generation_id,
                    exc,
                )
                continue

            source = payload
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                source = payload["data"]
            usage = OpenRouterClient.extract_usage(source)
            if not usage.get("usage_payload"):
                _logger.info(
                    "OpenRouter reconcile: no usage payload for log %s (generation=%s)",
                    row.id,
                    row.generation_id,
                )
                continue

            # Optimize payload storage
            payload_json = json.dumps(payload, ensure_ascii=False)
            vals = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "reasoning_tokens": usage.get("reasoning_tokens"),
                "cached_tokens": usage.get("cached_tokens"),
                "cache_write_tokens": usage.get("cache_write_tokens"),
                "audio_tokens": usage.get("audio_tokens"),
                "total_cost": usage.get("cost") or row.total_cost,
                "upstream_inference_cost": usage.get("upstream_inference_cost") or row.upstream_inference_cost,
                "usage_payload": usage.get("usage_payload"),
            }

            if len(payload_json) > 50000:
                attachment = self.env["ir.attachment"].create({
                    "name": f"openrouter_response_{row.generation_id}.json",
                    "type": "binary",
                    "datas": base64.b64encode(payload_json.encode("utf-8")),
                    "res_model": self._name,
                    "res_id": row.id,
                    "mimetype": "application/json",
                })
                vals["response_attachment_id"] = attachment.id
                vals["response_payload"] = False # Clear text field to save space if it had anything, or just keep it clean
            else:
                 vals["response_payload"] = row.response_payload or payload_json

            row.write(vals)
            reconciled += 1

        _logger.info(
            "OpenRouter reconcile: checked=%s reconciled=%s window_hours=%s",
            len(rows),
            reconciled,
            hours_back,
        )
        return reconciled
