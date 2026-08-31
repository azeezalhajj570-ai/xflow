"""
Telegram Scraper Microservice

Scrapes Telegram groups/channels using Telethon (MTProto) and pushes
scraped data into Odoo via JSON-RPC.

Architecture:
- Reads target groups from Odoo madarbot.scraped.group records
- Uses Telethon to connect and scrape messages
- Writes scraped messages to Odoo madarbot.scraped.message records
- Reports progress via Odoo bus.bus (websocket)
- Triggered by ir.cron or run continuously
"""

import json
import logging
import os
import sys
import time

import requests
from telethon import TelegramClient, events

_logger = logging.getLogger(__name__)

ODOO_URL = os.environ.get('ODOO_URL', 'http://odoo:8069')
ODOO_DB = os.environ.get('ODOO_DB', 'odoo')
ODOO_USER = os.environ.get('ODOO_USER', 'admin')
ODOO_PASSWORD = os.environ.get('ODOO_PASSWORD', 'admin')

TELEGRAM_API_ID = os.environ.get('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.environ.get('TELEGRAM_API_HASH')
TELEGRAM_SESSION = os.environ.get('TELEGRAM_SESSION', 'scraper_session')


class OdooClient:
    def __init__(self, url, db, user, password):
        self.url = url
        self.db = db
        self.session = requests.Session()
        self.uid = None
        self._authenticate(user, password)

    def _authenticate(self, user, password):
        resp = requests.post(
            f'{self.url}/jsonrpc',
            json={
                'jsonrpc': '2.0',
                'method': 'call',
                'params': {
                    'service': 'common',
                    'method': 'authenticate',
                    'args': [self.db, user, password, {}],
                },
                'id': 1,
            },
            timeout=30,
        )
        result = resp.json()
        self.uid = result.get('result')
        if not self.uid:
            raise Exception(f'Authentication failed: {result}')

    def call(self, model, method, args=None, kwargs=None):
        resp = requests.post(
            f'{self.url}/jsonrpc',
            json={
                'jsonrpc': '2.0',
                'method': 'call',
                'params': {
                    'service': 'object',
                    'method': 'execute_kw',
                    'args': [self.db, self.uid, ODOO_PASSWORD, model, method, args or [], kwargs or {}],
                },
                'id': 1,
            },
            timeout=60,
        )
        result = resp.json()
        if result.get('error'):
            raise Exception(f'RPC error: {result["error"]}')
        return result.get('result')

    def search_read(self, model, domain, fields_list):
        return self.call(model, 'search_read', kwargs={
            'domain': domain,
            'fields': fields_list,
        })

    def create(self, model, values):
        return self.call(model, 'create', args=[values])


class TelegramScraper:
    def __init__(self, odoo: OdooClient):
        self.odoo = odoo
        self.client = TelegramClient(
            TELEGRAM_SESSION,
            int(TELEGRAM_API_ID),
            TELEGRAM_API_HASH,
        )

    async def scrape_groups(self):
        groups = self.odoo.search_read('madarbot.scraped.group', [
            ('active', '=', True),
        ], ['id', 'telegram_chat_id', 'title', 'chat_type'])

        _logger.info('Scraping %d groups', len(groups))
        for group in groups:
            await self._scrape_group(group)

    async def _scrape_group(self, group):
        chat_id = int(group['telegram_chat_id'])
        entity = await self.client.get_entity(chat_id)

        async for message in self.client.iter_messages(entity, limit=100):
            if message.sender_id:
                try:
                    sender = await self.client.get_entity(message.sender_id)
                    sender_name = getattr(sender, 'first_name', '') + ' ' + getattr(sender, 'last_name', '')
                    sender_username = getattr(sender, 'username', '')
                except Exception:
                    sender_name = ''
                    sender_username = ''
            else:
                sender_name = ''
                sender_username = ''

            existing = self.odoo.search_read('madarbot.scraped.message', [
                ('telegram_message_id', '=', message.id),
                ('group_id', '=', group['id']),
            ], ['id'])

            if existing:
                continue

            self.odoo.create('madarbot.scraped.message', {
                'telegram_message_id': message.id,
                'group_id': group['id'],
                'sender_user_id': message.sender_id or 0,
                'sender_name': sender_name,
                'sender_username': sender_username,
                'message_text': message.text or '',
                'has_media': bool(message.media),
                'media_type': str(type(message.media).__name__) if message.media else '',
                'reply_to_message_id': message.reply_to_msg_id,
                'posted_at': message.date.isoformat() if message.date else '',
            })

            _logger.info('Scraped message %d from %s', message.id, group['title'])

        self.odoo.call('madarbot.scraped.group', 'write', args=[[group['id']], {
            'last_scraped': time.strftime('%Y-%m-%d %H:%M:%S'),
        }])

    async def run_forever(self):
        await self.client.start()
        while True:
            try:
                await self.scrape_groups()
            except Exception:
                _logger.exception('Scrape cycle failed')
            await asyncio.sleep(300)


if __name__ == '__main__':
    import asyncio
    logging.basicConfig(level=logging.INFO)
    odoo = OdooClient(ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD)
    scraper = TelegramScraper(odoo)
    asyncio.run(scraper.run_forever())
