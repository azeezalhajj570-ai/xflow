# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import requests
import json
import threading

from odoo import _
from odoo.addons.whatsapp_evaluation.tools.whatsapp_exception import WhatsAppError

_logger = logging.getLogger(__name__)

class WhatsAppApi:
    def __init__(self, base_url, instance_name, api_key, instance_token=False):
        self.base_url = base_url.rstrip('/')
        self.instance_name = instance_name
        self.api_key = api_key
        self.instance_token = instance_token

    def __api_requests(self, request_type, endpoint, params=False, headers=None, data=False, use_global_key=False):
        if getattr(threading.current_thread(), 'testing', False):
             raise WhatsAppError(_("API requests disabled in testing."))

        # Use a copy of headers to avoid mutating the argument for retries
        request_headers = (headers or {}).copy()
        
        # Prioritize Instance Token (Authorization: Bearer <token>) unless global key is forced
        if self.instance_token and not use_global_key:
            request_headers.update({
                'Authorization': f'Bearer {self.instance_token}',
                'Content-Type': 'application/json',
            })
        else:
            # Fallback to Global Key (apikey: <key>)
            request_headers.update({
                'apikey': self.api_key,
                'Content-Type': 'application/json',
            })
        
        url = f"{self.base_url}{endpoint}"
        
        try:
            # Redact sensitive headers for logging
            log_headers = request_headers.copy()
            if 'apikey' in log_headers:
                log_headers['apikey'] = '***'
            if 'Authorization' in log_headers:
                log_headers['Authorization'] = 'Bearer ***'
            
            _logger.info("WhatsApp Evaluation Request: %s %s Headers: %s Data: %s", request_type, url, log_headers, data)
            json_data = data if data else None
            res = requests.request(request_type, url, params=params, headers=request_headers, json=json_data, timeout=(10, 30))
        except requests.exceptions.Timeout:
            _logger.error("WhatsApp Evaluation Timeout: %s", url)
            raise WhatsAppError(_("Connection timed out. Check firewall or API URL."), error_code="Timeout")
        except requests.exceptions.RequestException as e:
            _logger.error("WhatsApp Evaluation Network Error: %s", str(e))
            raise WhatsAppError(failure_type='network')

        # Retry Logic for 401 (Unauthorized)
        if res.status_code == 401 and self.instance_token and not use_global_key:
            _logger.warning("WhatsApp Instance Token rejected (401). Retrying with Global API Key...")
            try:
                # Retry with use_global_key=True, passing original parameters
                return self.__api_requests(request_type, endpoint, params=params, headers=headers, data=data, use_global_key=True)
            except WhatsAppError:
                # If retry also fails, fall through to normal error handling
                pass

        try:
            if not res.ok:
                 # Attempt to parse error message from JSON
                 error_data = res.json()
                 _logger.error("Evolution API Request Failed. Status: %s. Response: %s", res.status_code, json.dumps(error_data))
                 raise WhatsAppError(*self._prepare_error_response(error_data))
            
            # Success Log (Debug)
            response_json = res.json()
            _logger.info("Evolution API Response: %s", json.dumps(response_json))
            return response_json
            
        except ValueError:
            if not res.ok:
                raise WhatsAppError(failure_type='network')
            return {}
        
        # Unreachable code block removed
        # try:
        #      return res.json()
        # except ValueError:
        #      return {}

    def _prepare_error_response(self, response):
        if 'error' in response and isinstance(response['error'], str):
            # Formats like {"status": 404, "error": "Not Found", ...}
            return (response.get('response', {}).get('message') or response['error'], response.get('status', 'odoo'))
        if 'message' in response:
            return (str(response['message']), 'odoo')
        return (_("Unknown Evolution API Error"), -1)

    def _test_connection(self):
        """ Test connection by checking instance state """
        # Using /instance/connectionState/{instance} - Requires Global Key
        endpoint = f"/instance/connectionState/{self.instance_name}"
        response = self.__api_requests("GET", endpoint, use_global_key=True)
        
        # Adjust based on actual response structure
        # Example response: {"instance": {"state": "open"}}
        state = response.get('instance', {}).get('state') or response.get('state')
        
        if state not in ['open', 'connecting', 'connected']:
             # Fallback check if simple instance fetch works
             _logger.warning("Connection state check returned: %s", state)
             # If we got a valid JSON response without 401/403, auth is likely fine.
        return True

    def _send_whatsapp(self, number, message_body):
        """ Send a text message """
        endpoint = f"/message/sendText/{self.instance_name}"
        
        payload = {
            "number": number,
            "text": message_body,
            "options": {
                "delay": 1200,
                "presence": "composing"
            }
        }
        
        return self.__api_requests("POST", endpoint, data=payload)

    def _send_whatsapp_media(self, number, attachment, caption=None):
        """ Send a media message """
        endpoint = f"/message/sendMedia/{self.instance_name}"
        
        # Odoo stores content as base64
        # Evolution API typically expects: { "number":..., "mediaMessage": { "mediatype": "image", "caption": "...", "media": "base64..." } }
        # Or simpler top-level: { "number": ..., "mediatype": "image", "caption": "...", "media": "..." }
        
        # Helper to map mimetype to Evolution type (image, video, document, audio)
        mimetype = attachment.mimetype
        if 'image' in mimetype:
            media_type = 'image'
        elif 'video' in mimetype:
            media_type = 'video'
        elif 'audio' in mimetype:
            media_type = 'audio'
        else:
            media_type = 'document'
            
        payload = {
            "number": number,
            "mediatype": media_type,
            "mimetype": mimetype,
            "caption": caption or attachment.name,
            "media": attachment.datas.decode('utf-8'), # binary to base64 string
            "fileName": attachment.name,
            "options": {
                "caption": caption or attachment.name
            }
        }
        
        _logger.info("Sending Media to %s. Caption: %s. Type: %s", number, payload['caption'], media_type)
        return self.__api_requests("POST", endpoint, data=payload)

    def update_webhook(self, webhook_url, enabled=True):
        """ Update instance webhook configuration """
        endpoint = f"/webhook/set/{self.instance_name}"
        payload = {
            "webhook": {
                "enabled": enabled,
                "url": webhook_url,
                "webhook_by_events": False,
                "base64": True,
                "events": [
                    "MESSAGES_UPSERT",
                    "MESSAGES_UPDATE",
                    "SEND_MESSAGE",
                ]
            }
        }
        
        # Webhook configuration is an admin action, so we Force Global Key
        return self.__api_requests("POST", endpoint, data=payload, use_global_key=True)
    @staticmethod
    def format_whatsapp_to_html(text):
        """
        Convert WhatsApp Markdown to HTML for Odoo display.
        Handles: *bold*, _italics_, ~strike~, ```code```
        """
        if not text:
            return ""
            
        # 1. Escape HTML first to prevent XSS
        from odoo import tools
        text = tools.html_escape(text)
        
        # 2. Convert Newlines to <br/>
        text = text.replace('\n', '<br/>')
        
        # 3. Apply Markdown Regex replacements
        import re
        
        # Monospace: ```text``` -> <code>text</code>
        # We handle this first to avoid matching * or _ inside code blocks
        text = re.sub(r'```(.*?)```', r'<code>\1</code>', text, flags=re.DOTALL)
        
        # Bold: *text* -> <b>text</b>
        text = re.sub(r'\*(.*?)\*', r'<b>\1</b>', text)
        
        # Italics: _text_ -> <i>text</i>
        text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
        
        # Strikethrough: ~text~ -> <strike>text</strike>
        text = re.sub(r'~(.*?)~', r'<strike>\1</strike>', text)
        
        return text
