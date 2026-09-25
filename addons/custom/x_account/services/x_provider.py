# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""X provider interface and registry.

Every X HTTP-capable provider (SessionWebProvider, XOfficialPublishAdapter,
and the optional OmniXProvider from x_account_omnix) implements the same
minimal surface:

    validate_session() -> {'valid': bool, 'user': dict|None, 'reason': str, ...}

and any account-scoped operations the module needs (get_conversations, get_dms,
send_dm, like, comment, repost, follow, post_tweet, get_account_stats, ...).

XService is the only dispatch point: it resolves a provider either from the
built-in map below or from the REGISTRY, so new providers can be added without
modifying XService. OmniX is an external optional provider (registered by the
x_account_omnix module); the system must never depend on it.
"""

import logging
import re

_logger = logging.getLogger(__name__)

# XChat (X Groups product) conversation ids are ``g``-prefixed. Providers
# (OmniX, GetXAPI, ...) sometimes deliver these conversations without a
# ``group`` flag or a meaningful ``type`` field, so the id shape is the
# reliable, provider-agnostic group signal.
_CHAT_GROUP_ID_RE = re.compile(r'^g[0-9]+$')


def x_conversation_is_group(conversation_id, group=False, conv_type=''):
    """Whether an X conversation is a group conversation.

    Returns True when the provider payload already says so (a ``group`` flag
    or a group ``type`` value) or when the conversation id follows X's XChat
    ``g``-prefix convention — which providers sometimes omit from their
    payload (e.g. ``g2032517...`` coming through without a type). Centralizing
    the rule keeps every provider's group sync consistent.
    """
    if group:
        return True
    conv_type = str(conv_type or '').strip().lower()
    if conv_type in ('group', 'group_dm', 'group_dm_thread', 'chat'):
        return True
    return bool(_CHAT_GROUP_ID_RE.match(str(conversation_id or '')))


class XProvider:
    """Base class / contract for X providers.

    Subclasses are instantiated as ``cls(env, account)``. They must implement
    at least ``validate_session``; everything else is provider-specific.
    """

    # Provider code registered in REGISTRY (e.g. 'session_web',
    # 'official_publish', or an external one like 'omnix').
    _provider_code = None

    # The read capabilities this provider implements (a subset of the read
    # methods below). Empty means "no timeline/interaction reads". Declared on
    # the class so consumers can detect support *without instantiating* the
    # provider or binding to a provider code, e.g.:
    #   _read_operations = frozenset({'fetch_user_posts', 'fetch_post_comments'})
    # `x_account_social_posts` uses this to decide which accounts it can fetch
    # posts for.
    _read_operations = frozenset()

    def __init__(self, env, account):
        self.env = env
        self.account = account

    def validate_session(self):
        raise NotImplementedError

    # --- Read capabilities (timeline + interactions) ---------------------
    #
    # Providers that can read an account's own posts and the interactions on
    # a post implement these. The defaults report the capability as
    # unsupported (instead of raising) so a caller can degrade gracefully:
    # e.g. `x_account_social_posts` still stores the post counters even when
    # the provider cannot list individual engagers.
    #
    # Contract return shapes:
    #   fetch_user_posts      -> {'posts': [post DTO], 'cursor': str, 'has_more': bool}
    #   fetch_post_comments   -> {'comments': [post DTO], 'cursor': str, 'has_more': bool}
    #   fetch_post_retweeters -> {'users': [user DTO], 'cursor': str, 'has_more': bool}
    #   fetch_post_likers     -> {'users': [user DTO], 'cursor': str, 'has_more': bool}
    # Each may additionally carry {'unsupported': True, 'reason': str}.
    #
    # post DTO:  {id, text, author_id, author_username, author_name,
    #             created_at, favorite_count, retweet_count, reply_count,
    #             quote_count, conversation_id, in_reply_to_tweet_id, raw,
    #             audience?}
    # user DTO:  {id, username, name, profile_image_url}
    # comment DTO: a post DTO, or a person-shaped {id, author_id,
    #             author_username, author_name, text?}
    #
    # A post DTO MAY carry `audience` — {'likers': [user DTO],
    # 'commenters': [comment DTO], 'retweeters': [user DTO]} — for providers
    # whose timeline read returns the engagers inline (e.g. XActions
    # /api/posts/report). The sync stores these without a second round trip;
    # providers that don't return them leave the key absent and the sync calls
    # the fetch_post_* methods instead.

    def fetch_user_posts(self, screen_name, limit=20, cursor=None,
                         per_post_limit=None):
        """Return the account owner's own posts (timeline).

        ``per_post_limit`` caps the users read per interaction type per post
        for providers that return engagers inline; providers that don't may
        ignore it.
        """
        return self._unsupported('fetch_user_posts')

    def fetch_post_comments(self, tweet_id, limit=50, cursor=None):
        """Return the replies (comments) to a post."""
        return self._unsupported('fetch_post_comments')

    def fetch_post_retweeters(self, tweet_id, limit=100, cursor=None):
        """Return the users who retweeted a post."""
        return self._unsupported('fetch_post_retweeters')

    def fetch_post_likers(self, tweet_id, limit=100, cursor=None):
        """Return the users who liked a post.

        X exposes no public 'who liked' endpoint (this is a platform
        limitation, not a provider gap). Providers should keep this
        unsupported until a source exists."""
        return self._unsupported('fetch_post_likers')

    @staticmethod
    def _unsupported(operation, reason='provider_does_not_support_read'):
        return {
            'posts': [], 'comments': [], 'users': [],
            'cursor': '', 'has_more': False,
            'unsupported': True,
            'reason': '%s:%s' % (operation, reason),
        }


# Built-in providers, keyed by the social.account.x_provider selection value.
# 'omnix' is NOT built-in: it is provided by the optional x_account_omnix
# module, which registers itself with XProviderRegistry at import time (OCP).
_BUILTIN_PROVIDERS = {
    'session_web': 'odoo.addons.x_account.services.providers.session_web.SessionWebProvider',
    'official_publish': 'odoo.addons.x_account.services.providers.official_publish.XOfficialPublishAdapter',
}

class XProviderRegistry:
    """Simple registry so future providers can plug in.

    Adding a provider is a one-line registration; no change to XService or the
    provider interface is required. This is the only extension point for new
    providers. OmniX is an external optional provider (registered by the
    x_account_omnix module) — accounts using SessionWebProvider never import or
    depend on it.
    """

    # Extension point for future providers. Maps a provider code to a dotted
    # path. Registered providers take precedence over built-ins.
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
