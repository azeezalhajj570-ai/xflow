"""
MCP (Model Context Protocol) Server for MadarBot

Exposes Odoo data as MCP tools that AI agents can call.
Follows the Model Context Protocol specification.

Tools exposed:
- search_partners: Search res.partner records
- read_messages: Read mail.message records
- search_products: Search product.template records
- search_contacts: Search mailing.contact records
- create_record: Create any Odoo record
- read_record: Read any Odoo record by ID
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

_logger = logging.getLogger(__name__)

ODOO_URL = os.environ.get('ODOO_URL', 'http://odoo:8069')
ODOO_DB = os.environ.get('ODOO_DB', 'odoo')
ODOO_USER = os.environ.get('ODOO_USER', 'admin')
ODOO_PASSWORD = os.environ.get('ODOO_PASSWORD', 'admin')

app = FastAPI(title='MadarBot MCP Server')


class OdooClient:
    def __init__(self):
        self.session = requests.Session()
        self.uid = None
        self._authenticate()

    def _authenticate(self):
        resp = requests.post(
            f'{ODOO_URL}/jsonrpc',
            json={
                'jsonrpc': '2.0',
                'method': 'call',
                'params': {
                    'service': 'common',
                    'method': 'authenticate',
                    'args': [ODOO_DB, ODOO_USER, ODOO_PASSWORD, {}],
                },
                'id': 1,
            },
            timeout=30,
        )
        result = resp.json()
        self.uid = result.get('result')
        if not self.uid:
            raise Exception(f'Authentication failed: {result}')

    def execute(self, model: str, method: str, args: Optional[List] = None, kwargs: Optional[Dict] = None):
        resp = requests.post(
            f'{ODOO_URL}/jsonrpc',
            json={
                'jsonrpc': '2.0',
                'method': 'call',
                'params': {
                    'service': 'object',
                    'method': 'execute_kw',
                    'args': [ODOO_DB, self.uid, ODOO_PASSWORD, model, method, args or [], kwargs or {}],
                },
                'id': 1,
            },
            timeout=60,
        )
        result = resp.json()
        if result.get('error'):
            raise HTTPException(status_code=400, detail=result['error'])
        return result.get('result')


odoo = OdooClient()


# MCP Tool definitions

class MCPTool(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]


TOOLS = [
    MCPTool(
        name='search_partners',
        description='Search business partners by name, email, or other criteria',
        parameters={
            'type': 'object',
            'properties': {
                'domain': {'type': 'array', 'items': {}, 'description': 'Search domain like [["name", "ilike", "John"]]'},
                'limit': {'type': 'integer', 'description': 'Max results', 'default': 10},
            },
            'required': ['domain'],
        },
    ),
    MCPTool(
        name='read_record',
        description='Read any Odoo record by model and ID',
        parameters={
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Odoo model name (e.g. res.partner)'},
                'record_id': {'type': 'integer', 'description': 'Record ID'},
            },
            'required': ['model', 'record_id'],
        },
    ),
    MCPTool(
        name='search_messages',
        description='Search mail messages by content, author, or date range',
        parameters={
            'type': 'object',
            'properties': {
                'domain': {'type': 'array', 'items': {}, 'description': 'Search domain'},
                'limit': {'type': 'integer', 'description': 'Max results', 'default': 20},
            },
            'required': ['domain'],
        },
    ),
    MCPTool(
        name='search_products',
        description='Search products by name, reference, or category',
        parameters={
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': 'Search query'},
                'limit': {'type': 'integer', 'description': 'Max results', 'default': 10},
            },
            'required': ['query'],
        },
    ),
    MCPTool(
        name='search_contacts',
        description='Search mailing contacts by name, email, or Telegram chat ID',
        parameters={
            'type': 'object',
            'properties': {
                'domain': {'type': 'array', 'items': {}, 'description': 'Search domain'},
                'limit': {'type': 'integer', 'description': 'Max results', 'default': 10},
            },
            'required': ['domain'],
        },
    ),
    MCPTool(
        name='create_record',
        description='Create any Odoo record',
        parameters={
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Odoo model name'},
                'values': {'type': 'object', 'description': 'Field values dict'},
            },
            'required': ['model', 'values'],
        },
    ),
    MCPTool(
        name='get_scraped_data',
        description='Get scraped Telegram messages from a group',
        parameters={
            'type': 'object',
            'properties': {
                'group_id': {'type': 'integer', 'description': 'Scraped group ID'},
                'limit': {'type': 'integer', 'description': 'Max messages', 'default': 50},
            },
            'required': ['group_id'],
        },
    ),
]


@app.get('/mcp/tools')
def list_tools():
    return {'tools': [t.model_dump() for t in TOOLS]}


class ToolCallRequest(BaseModel):
    tool: str
    arguments: Dict[str, Any]


@app.post('/mcp/call')
def call_tool(req: ToolCallRequest):
    if req.tool == 'search_partners':
        result = odoo.execute('res.partner', 'search_read', kwargs={
            'domain': req.arguments['domain'],
            'fields': ['id', 'name', 'email', 'phone'],
            'limit': req.arguments.get('limit', 10),
        })
        return {'result': result}

    elif req.tool == 'read_record':
        result = odoo.execute(req.arguments['model'], 'read', args=[[req.arguments['record_id']]])
        return {'result': result}

    elif req.tool == 'search_messages':
        result = odoo.execute('mail.message', 'search_read', kwargs={
            'domain': req.arguments['domain'],
            'fields': ['id', 'subject', 'body', 'author_id', 'date'],
            'limit': req.arguments.get('limit', 20),
        })
        return {'result': result}

    elif req.tool == 'search_products':
        result = odoo.execute('product.template', 'search_read', kwargs={
            'domain': [['name', 'ilike', req.arguments['query']]],
            'fields': ['id', 'name', 'list_price', 'default_code'],
            'limit': req.arguments.get('limit', 10),
        })
        return {'result': result}

    elif req.tool == 'search_contacts':
        result = odoo.execute('mailing.contact', 'search_read', kwargs={
            'domain': req.arguments['domain'],
            'fields': ['id', 'name', 'email', 'telegram_chat_id'],
            'limit': req.arguments.get('limit', 10),
        })
        return {'result': result}

    elif req.tool == 'create_record':
        result = odoo.execute(req.arguments['model'], 'create', args=[req.arguments['values']])
        return {'result': {'id': result}}

    elif req.tool == 'get_scraped_data':
        result = odoo.execute('madarbot.scraped.message', 'search_read', kwargs={
            'domain': [['group_id', '=', req.arguments['group_id']]],
            'fields': ['id', 'telegram_message_id', 'sender_name', 'message_text', 'posted_at'],
            'limit': req.arguments.get('limit', 50),
        })
        return {'result': result}

    raise HTTPException(status_code=400, detail=f'Unknown tool: {req.tool}')


@app.get('/health')
def health():
    return {'status': 'ok'}


if __name__ == '__main__':
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host='0.0.0.0', port=8000)
