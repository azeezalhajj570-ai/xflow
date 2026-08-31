# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import json
import base64
from datetime import timedelta
from odoo import http
from odoo import fields
from odoo.http import request, Response
from odoo.addons.whatsapp_evaluation.tools.whatsapp_api import WhatsAppApi

_logger = logging.getLogger(__name__)

class WebhookEvaluation(http.Controller):

    @http.route(['/whatsapp_evaluation/webhook', '/whatsapp_evaluation/webhook/'], methods=['POST'], type="http", auth="public", csrf=False)
    def webhookpost(self):
        """
        Handler for Evolution API Webhooks.
        """
        _logger.info("WhatsApp Webhook: Received request from Evolution API")
        try:
            data = json.loads(request.httprequest.data)
            _logger.info("WhatsApp Webhook: Parsed JSON data, event=%s, instance=%s", data.get('event'), data.get('instance'))
        except (json.JSONDecodeError, ValueError) as e:
            _logger.error("Webhook: Failed to parse JSON: %s", str(e))
            return Response('{"error": "Invalid JSON"}', status=400, content_type='application/json')

        try:
            event_type = data.get('event')
            instance_name = data.get('instance')

            if not instance_name:
                return Response('OK', status=200, content_type='text/plain')

            account = request.env['whatsapp.account'].sudo().search(
                [('instance_name', '=', instance_name)], limit=1
            )
            
            if not account:
                _logger.warning("No WhatsApp Evaluation Account found for instance: %s", instance_name)
                return Response('OK', status=200, content_type='text/plain')
            
            _logger.info("WhatsApp Webhook: Found account %s (ID: %d) for instance %s", account.name, account.id, instance_name)

            if event_type:
                event_type = event_type.upper().replace('.', '_')
            
            if event_type == 'MESSAGES_UPSERT':
                _logger.info("WhatsApp Webhook: Processing MESSAGES_UPSERT event")
                self._handle_messages_upsert(account, data.get('data', {}))
            elif event_type == 'MESSAGES_UPDATE':
                _logger.info("WhatsApp Webhook: Processing MESSAGES_UPDATE event")
                self._handle_messages_update(account, data.get('data', {}))
            elif event_type == 'SEND_MESSAGE':
                pass
            
            return Response('OK', status=200, content_type='text/plain')
        except Exception as e:
            _logger.exception("Webhook processing error: %s", str(e))
            return Response('{"error": "Internal error"}', status=500, content_type='application/json')

    def _handle_messages_upsert(self, account, data):
        """
        Process incoming messages.
        """
        _logger.info("WhatsApp Upsert: Starting to process messages upsert event")
        try:
            messages = data.get('messages', [])
            if not messages and 'key' in data:
                messages = [data]
            
            _logger.info("WhatsApp Upsert: Found %s messages to process", len(messages))

            for msg in messages:
                try:
                    # Check if this message has a status field (delivery status)
                    msg_status = msg.get('status')
                    
                    # Process the message normally (create whatsapp.message record)
                    self._process_single_message(account, msg)
                    
                    # If the message has a status, update it after creation
                    if msg_status:
                        msg_uid = msg.get('key', {}).get('id')
                        if msg_uid:
                            odoo_state = False
                            if msg_status == 'SERVER_ACK':
                                odoo_state = 'sent'
                            elif msg_status == 'DELIVERY_ACK':
                                odoo_state = 'delivered'
                            elif msg_status in ['READ', 'PLAYED']:
                                odoo_state = 'read'
                            
                            if odoo_state:
                                wa_message = request.env['whatsapp.message'].sudo().search([
                                    ('msg_uid', '=', msg_uid)
                                ], limit=1)
                                if wa_message:
                                    wa_message.write({'state': odoo_state})
                                    _logger.info("WhatsApp Upsert: Set message %s state to %s", msg_uid, odoo_state)
                except Exception as e:
                    _logger.exception("Error processing message: %s", str(e))
                    continue
        except Exception as e:
            _logger.exception("Error in _handle_messages_upsert: %s", str(e))

    def _process_single_message(self, account, msg):
        """Process a single incoming or outgoing message."""
        _logger.info("WhatsApp Process Message: Starting to process message")
        key = msg.get('key', {})
        from_me = key.get('fromMe', False)
        remote_jid = key.get('remoteJid')
        msg_uid = key.get('id')
        if not remote_jid or not msg_uid:
            _logger.info("WhatsApp Process Message: Missing remote_jid or msg_uid, skipping")
            return
        
        _logger.info("WhatsApp Process Message: Processing message from %s, msg_uid=%s (fromMe=%s)", remote_jid, msg_uid, from_me)
        
        existing_msg = request.env['whatsapp.message'].sudo().search([
            ('msg_uid', '=', msg_uid)
        ], limit=1)
        
        if existing_msg:
            _logger.info("WhatsApp Upsert: Duplicate message ID %s (State: %s), skipping.", msg_uid, existing_msg.state)
            return

        mobile_number = remote_jid.split('@')[0]

        message_content = msg.get('message', {})
        body = (
            message_content.get('conversation') or 
            message_content.get('extendedTextMessage', {}).get('text') or
            message_content.get('imageMessage', {}).get('caption') or
            message_content.get('videoMessage', {}).get('caption') or
            message_content.get('documentMessage', {}).get('caption') or
            message_content.get('templateMessage', {}).get('hydratedTemplate', {}).get('hydratedContentText') or
            ('🎥 Video Message' if 'videoMessage' in message_content else '') or
            ('🎤 Audio Message' if 'audioMessage' in message_content else '') or
            ('📄 Document Message' if 'documentMessage' in message_content else '') or
            ('📷 Image Message' if 'imageMessage' in message_content else '') or
            ''
        )
        
        _logger.info("WhatsApp Upsert: Extracted Body length: %s", len(body) if body else 0)

        file_content = msg.get('base64') or msg.get('message', {}).get('base64')

        if not body and not file_content:
            _logger.info("WhatsApp Upsert: No body and no base64. Skipping.")
            return

        if body:
            since = fields.Datetime.now() - timedelta(minutes=2)
            echo_count = request.env['whatsapp.message'].sudo().search_count([
                ('mobile_number', '=', mobile_number),
                ('message_type', '=', 'outbound'),
                ('create_date', '>=', since),
                ('body', '=', body),
            ])
            if echo_count:
                _logger.info(
                    "WhatsApp Upsert: Skipping likely outbound echo for %s (msg_uid=%s)",
                    mobile_number,
                    msg_uid,
                )
                return

        # Find/create the customer partner (for fromMe, remoteJid is the recipient)
        domain = ['|',
            ('phone', '=', mobile_number),
            ('phone', '=', '+' + mobile_number)
        ]
        customer_partner = request.env['res.partner'].sudo().search(domain, limit=1)
        if not customer_partner:
            customer_partner = request.env['res.partner'].sudo().create({
                'name': mobile_number,
                'phone': '+' + mobile_number if not mobile_number.startswith('+') else mobile_number,
            })
            _logger.info("WhatsApp Inbound: Created new partner for %s", mobile_number)

        _logger.info("WhatsApp Inbound: Finding Channel for %s", mobile_number)
        
        channel = request.env['discuss.channel'].sudo()._get_whatsapp_channel(
            mobile_number, account, partner=customer_partner, create_if_not_found=True
        )
        
        if not channel:
            _logger.error("WhatsApp Inbound: Failed to create/find channel for %s", mobile_number)
            return

        _logger.info("WhatsApp Inbound: Posting to Channel %s (ID: %s)", channel.name, channel.id)
        
        attachments_list = []
        if file_content:
            if ',' in file_content and ';base64' in file_content[:50]:
                file_content = file_content.split(',')[1]

            filename = "whatsapp_media"
            mimetype = "application/octet-stream"
            
            msg_type = msg.get('messageType')
            
            if 'audioMessage' in message_content or msg_type == 'audioMessage':
                mimetype = message_content.get('audioMessage', {}).get('mimetype', 'audio/ogg')
                filename = "voice_message.ogg"
            elif 'imageMessage' in message_content or msg_type == 'imageMessage':
                mimetype = message_content.get('imageMessage', {}).get('mimetype', 'image/jpeg')
                filename = "image.jpg"
            elif 'videoMessage' in message_content or msg_type == 'videoMessage':
                mimetype = message_content.get('videoMessage', {}).get('mimetype', 'video/mp4')
                filename = "video.mp4"
            elif 'documentMessage' in message_content or msg_type == 'documentMessage':
                mimetype = message_content.get('documentMessage', {}).get('mimetype', 'application/pdf')
                filename = message_content.get('documentMessage', {}).get('fileName', 'document')

            decoded_content = base64.b64decode(file_content)
            attachments_list.append((filename, decoded_content))
            _logger.info("WhatsApp Inbound: Prepared attachment %s", filename)

        from markupsafe import Markup
        formatted_body = Markup(WhatsAppApi.format_whatsapp_to_html(body))

        _logger.info("WhatsApp: Posting message to channel %s (ID: %d) with %d members", 
                     channel.name, channel.id, len(channel.sudo().channel_member_ids))

        if from_me:
            # Message sent from the business user's phone → sync to channel as context
            # Author is the business user (first notify user's partner)
            author_partner = account.notify_user_ids[:1].partner_id
            channel.sudo().with_context(wa_from_me_sync=True).message_post(
                body=formatted_body,
                author_id=author_partner.id,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
                attachments=attachments_list,
            )
            _logger.info("WhatsApp: fromMe message synced to channel %s (ID: %d)", channel.name, channel.id)
        else:
            new_msg = channel.sudo().with_context(whatsapp_inbound_msg_uid=key.get('id')).message_post(
                body=formatted_body,
                author_id=customer_partner.id,
                message_type='whatsapp_message',
                subtype_xmlid='mail.mt_comment',
                attachments=attachments_list
            )
            _logger.info("WhatsApp: Message posted successfully (ID: %d)", new_msg.id)
             
            # Notify users explicitly (Toast) - REMOVED per user request
            # for user in account.notify_user_ids:
            #     _logger.info("WhatsApp Inbound: Sending Toast notification to User %s", user.name)
            #     user.partner_id._bus_send('simple_notification', {
            #        'type': 'info',
            #        'title': f"New WhatsApp from {mobile_number}",
            #        'message': body[:100] + ("..." if len(body) > 100 else ""),
            #        'sticky': False
            #     }) 

    def _handle_messages_update(self, account, data):
        """
        Handle message status updates (e.g. READ, DELIVERED)
        """
        _logger.info("WhatsApp Update: Starting to process messages update event")
        try:
            msg_uid = data.get('keyId') or data.get('key', {}).get('id')
            status = data.get('status')
            
            if not msg_uid or not status:
                _logger.debug("MESSAGES_UPDATE: Missing msg_uid or status, skipping")
                return

            # Check if this is a bot message (from @bot JID)
            key = data.get('key', {})
            remote_jid = key.get('remoteJid', '')
            if '@bot' in remote_jid:
                _logger.debug("MESSAGES_UPDATE: Skipping bot message update for %s", remote_jid)
                return

            odoo_state = False
            if status == 'SERVER_ACK':
                odoo_state = 'sent'
            elif status == 'DELIVERY_ACK':
                odoo_state = 'delivered'
            elif status in ['READ', 'PLAYED']:
                odoo_state = 'read'
                
            if odoo_state:
                message = request.env['whatsapp.message'].sudo().search([
                    ('msg_uid', '=', msg_uid)
                ], limit=1)
                
                if message:
                    message.write({'state': odoo_state})
                    _logger.info("Updated message %s status to %s", msg_uid, odoo_state)
                else:
                    _logger.debug("MESSAGES_UPDATE: Message with msg_uid %s not found in Odoo", msg_uid)
        except Exception as e:
            _logger.exception("Error in _handle_messages_update: %s", str(e))

