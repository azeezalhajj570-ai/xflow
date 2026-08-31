# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""X provider interface and registry.

Every X HTTP-capable provider (SessionWebProvider, XOfficialPublishAdapter,
OmniXProvider) implements the same minimal surface:

    validate_session() -> {'valid': bool, 'user': dict|None, 'reason': str, ...}

and any account-scoped operations the module needs (get_conversations, get_dms,
send_dm, like, comment, repost, follow, post_tweet, get_account_stats, ...).

XService is the only dispatch point: it resolves a provider either from the
built-in map below or from the REGISTRY, so new providers can be added without
modifying XService. OmniX is a built-in optional provider (per-account
either/or with SessionWebProvider); the system must never depend on it.
"""

import logging

_logger = logging.getLogger(__name__)


class XProvider:
    """Base class / contract for X providers.

    Subclasses are instantiated as ``cls(env, account)``. They must implement
    at least ``validate_session``; everything else is provider-specific.
    """

    # Provider code registered in REGISTRY (e.g. 'session_web',
    # 'official_publish', 'omnix').
    _provider_code = None

    def __init__(self, env, account):
        self.env = env
        self.account = account

    def validate_session(self):
        raise NotImplementedError


# Built-in providers, keyed by the social.account.x_provider selection value.
_BUILTIN_PROVIDERS = {
    'session_web': 'odoo.addons.x_account.services.providers.session_web.SessionWebProvider',
    'official_publish': 'odoo.addons.x_account.services.providers.official_publish.XOfficialPublishAdapter',
    'omnix': 'odoo.addons.x_account.services.providers.omnix.OmniXProvider',
}

class XProviderRegistry:
    """Simple registry so future providers can plug in.

    Adding a provider is a one-line registration; no change to XService or the
    provider interface is required. This is the only extension point for new
    providers. OmniX is a built-in provider option (per-account either/or with
    SessionWebProvider) but remains optional: accounts using SessionWebProvider
    never import or depend on it.
    """

    # Extension point for future providers (e.g. OmniX). Maps a provider code
    # to a dotted path. Registered providers take precedence over built-ins.
    _registry = {}

    @classmethod
    def register(cls, code, dotted_path):
        """Register a provider by its x_provider selection value.

        :param code: string used as the account's x_provider value.
        :param dotted_path: importable path to the provider class, e.g.
            'my_module.services.omnix.OmniXProvider'.
        """
        cls._registry[code] = dotted_path
        _logger.info('Registered X provider %r -> %s', code, dotted_path)

    @classmethod
    def unregister(cls, code):
        cls._registry.pop(code, None)

    @classmethod
    def resolve(cls, code):
        """Return the provider class for `code`, or None when unknown."""
        dotted_path = cls._registry.get(code) or _BUILTIN_PROVIDERS.get(code)
        if not dotted_path:
            return None
        try:
            module_name, _, class_name = dotted_path.rpartition('.')
            module = __import__(module_name, fromlist=[class_name])
            return getattr(module, class_name)
        except (ImportError, AttributeError) as exc:
            _logger.exception('Failed to load X provider %r: %s', code, exc)
            return None


def get_provider_class(code):
    """Public helper: resolve a provider code to its class (or None)."""
    return XProviderRegistry.resolve(code)
