import hashlib
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class MadarBotWebhookController(http.Controller):

    @http.route('/madarbot/webhook/<int:account_id>/<string:token_hash>',
                type='json', auth='public', methods=['POST'], csrf=False)
    def webhook(self, account_id, token_hash, **kwargs):
        env = request.env.sudo()
        account = env['madarbot.account'].browse(account_id).exists()
        if not account or not account.active:
            _logger.warning('Webhook: account %s not found or inactive', account_id)
            return {'ok': False}

        expected_hash = hashlib.sha256(account.token.encode()).hexdigest()[:16]
        if token_hash != expected_hash:
            _logger.warning('Webhook: invalid token hash for account %s', account_id)
            return {'ok': False}

        update = request.get_json_data()
        update_id = update.get('update_id')

        if update_id and account._has_processed_update(update_id):
            return {'ok': True}

        message_data = update.get('message') or update.get('edited_message') or {}
        if not message_data:
            return {'ok': True}

        chat = message_data.get('chat', {})
        chat_id = str(chat.get('id', ''))
        tg_user = message_data.get('from', {})
        text = message_data.get('text', '')

        if not chat_id:
            return {'ok': True}

        if env['madarbot.blacklist'].is_blacklisted(str(tg_user.get('id', ''))):
            return {'ok': True}

        account._add_processed_update(update_id)

        guest = env['mail.guest']._get_or_create_telegram_guest(tg_user)

        existing_channel = env['discuss.channel'].search([
            ('telegram_chat_id', '=', chat_id),
        ], limit=1)

        env['madarbot.telegram.message'].create({
            'direction': 'incoming',
            'state': 'pending',
            'telegram_chat_id': chat_id,
            'telegram_message_id': message_data.get('message_id'),
            'update_id': update_id,
            'body': text,
            'account_id': account.id,
            'guest_id': guest.id,
            'channel_id': existing_channel.id if existing_channel else False,
        })

        return {'ok': True}

    @http.route('/madarbot/webhook/set/<int:account_id>',
                type='http', auth='user', methods=['GET'], csrf=False)
    def set_webhook(self, account_id):
        env = request.env.sudo()
        account = env['madarbot.account'].browse(account_id).exists()
        if not account:
            return 'Account not found'
        base_url = env['ir.config_parameter'].get_param('web.base.url')
        token_hash = hashlib.sha256(account.token.encode()).hexdigest()[:16]
        webhook_url = f'{base_url}/madarbot/webhook/{account_id}/{token_hash}'
        import requests
        try:
            resp = requests.post(
                f'https://api.telegram.org/bot{account.token}/setWebhook',
                json={'url': webhook_url},
                timeout=10,
            )
            result = resp.json()
            if result.get('ok'):
                return f'Webhook set to {webhook_url}'
            return f'Error: {result}'
        except Exception as e:
            return f'Failed: {e}'

    @http.route('/madarbot/webhook/delete/<int:account_id>',
                type='http', auth='user', methods=['GET'], csrf=False)
    def delete_webhook(self, account_id):
        env = request.env.sudo()
        account = env['madarbot.account'].browse(account_id).exists()
        if not account:
            return 'Account not found'
        import requests
        try:
            resp = requests.post(
                f'https://api.telegram.org/bot{account.token}/deleteWebhook',
                timeout=10,
            )
            return f'Result: {resp.json()}'
        except Exception as e:
            return f'Failed: {e}'
