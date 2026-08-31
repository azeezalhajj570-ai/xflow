# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import fields

from .providers.session_web import SessionWebProvider
from .session_manager import XSessionManager
from .x_provider import XProviderRegistry

_logger = logging.getLogger(__name__)


class XService:
    """Single entry point (façade) for X operations.

    Models/tests MUST go through this service; they NEVER call X HTTP directly.
    """

    @staticmethod
    def get_provider(account):
        """Return a provider client for the account (session restored+runtime registered)."""
        account.ensure_one()
        provider_cls = XProviderRegistry.resolve(account.x_provider)
        if provider_cls is None:
            raise RuntimeError('No X provider registered for %r' % account.x_provider)
        needs_cookies = getattr(provider_cls, '_needs_cookies', True)
        if needs_cookies:
            cookies_str = XSessionManager.load(account)
            cookies = SessionWebProvider.parse_cookie_string(cookies_str or '')
            provider = provider_cls(account.env, account, cookies)
        else:
            provider = provider_cls(account.env, account)
        XSessionManager.register_runtime(account, provider)
        return provider

    @staticmethod
    def validate(account):
        provider = XService.get_provider(account)
        result = provider.validate_session()
        if result.get('valid'):
            account._transition('active')
            account.write({'last_validated': fields.Datetime.now()})
        else:
            account._set_last_error(result.get('reason'))
        return result

    @staticmethod
    def restore_and_validate(account):
        XService.get_provider(account)
        return XService.validate(account)


class XTaskService:
    """Durable task queue operations backed by x.account.task + ir.cron (MVP)."""

    _MAX_RUNNING_PER_ACCOUNT = 1

    @staticmethod
    def enqueue(account, operation, priority=0, group=None, max_attempts=3, **ctx):
        account.env['x.account.task'].create({
            'account_id': account.id,
            'group_id': group.id if group else False,
            'operation': operation,
            'priority': priority,
            'max_attempts': max_attempts,
            'next_retry_at': fields.Datetime.now(),
        })

    @staticmethod
    def claim_and_run(account, task):
        """Claim and execute a single task for an account (single-flight per account)."""
        task.ensure_one()
        running = account.env['x.account.task'].search_count([
            ('account_id', '=', account.id),
            ('status', '=', 'running'),
        ])
        if running >= XTaskService._MAX_RUNNING_PER_ACCOUNT:
            return False
        task.write({'status': 'running', 'claimed_at': fields.Datetime.now()})
        try:
            provider = XService.get_provider(account)
            result = getattr(provider, task.operation)(**task._task_call_context())
            task.write({'status': 'success', 'result': result})
            return True
        except Exception as exc:
            task._schedule_retry(str(exc))
            return False
