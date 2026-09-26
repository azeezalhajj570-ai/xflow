# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""XActionsProvider: X provider over a running XActions REST API.

Implements the XProvider read contract. The timeline read
(``POST /api/posts/report``) returns the account's own posts *and* the people
behind each engagement type in one call, so this provider returns the engagers
inline on each post DTO (see the contract note in ``x_provider.py``).

Per-post likers/retweeters are not exposed by the XActions HTTP API (only the
bundled report carries them), so those two methods report unsupported; the
timeline fetch is the path that stores all three interaction kinds.
"""

import logging

from odoo.exceptions import UserError

from odoo.addons.x_account.services.x_provider import XProviderRegistry

from .xactions_client import XActionsClient, XActionsError
from .xactions_envelope import XActionsEnvelopeParser

_LOGGER = logging.getLogger(__name__)

_REPORT_PATH = '/api/posts/report'
_COMMENTERS_PATH = '/api/commenters'
_ACCOUNTS_PATH = '/api/accounts'
_ME_PATH = '/api/user/me'

# The engagement audiences XActions reads from the report. Sending them makes
# the single report call return likers, commenters and retweeters inline.
_ENGAGEMENT_TYPES = ['likers', 'commenters', 'retweeters']
_MAX_POSTS = 100
_PER_POST_LIMIT = 100


class XActionsProvider:
    """XProvider read contract implemented over the XActions REST API."""

    _needs_cookies = False
    _needs_encryption_code = False
    _read_operations = frozenset({
        'fetch_user_posts', 'fetch_post_comments',
        'fetch_post_retweeters', 'fetch_post_likers',
    })

    def __init__(self, env, account):
        self.env = env
        self.account = account
        self._client = XActionsClient(env)

    def _account_args(self, required=False):
        """Request fields selecting the linked XActions account.

        ``accountId`` names the linked account whose session the server
        decrypts for this request. Without it the route falls back to the
        caller's own stored session — a different account — so reads pass
        ``required=True`` and stop instead of reading someone else's timeline.
        """
        account_id = self._resolve_account_id()
        if not account_id and required:
            handle = (self.account.social_account_handle or self.account.name
                      or '').strip().lstrip('@')
            raise UserError(
                'No linked XActions account for @%s. Set "Linked XActions '
                'account id" on this X account (GET /api/accounts lists the '
                'ids) so the fetch reads that account only.' % handle)
        return {'accountId': account_id} if account_id else {}

    def _resolve_account_id(self):
        """The linked XActions account id to act as for this Odoo account.

        An explicit ``x_xactions_account_id`` wins. Otherwise the account
        handle is matched against ``GET /api/accounts`` (case-insensitive,
        leading ``@`` stripped) and the match is remembered on the account so
        later runs skip the lookup and the operator can see which XActions
        account is in use.
        """
        explicit = getattr(self.account, 'x_xactions_account_id', '') or ''
        if explicit:
            return explicit
        handle = (self.account.social_account_handle or '').strip().lstrip('@')
        if not handle:
            return ''
        try:
            payload = self._client.get(_ACCOUNTS_PATH)
        except XActionsError as exc:
            _LOGGER.warning(
                'XActions: could not list accounts to resolve handle %r: %s',
                handle, exc.message)
            return ''
        accounts = (
            payload.get('accounts') if isinstance(payload, dict) else payload
        ) or []
        for candidate in accounts:
            if (candidate.get('username') or '').lower() == handle.lower():
                found = candidate.get('id') or ''
                if found:
                    self.account.sudo().x_xactions_account_id = found
                    return found
        _LOGGER.warning(
            'XActions: no linked account matches handle %r; set the linked '
            'XActions account on the X account to fetch its posts.', handle)
        return ''

    def validate_session(self):
        """Verify the token and reachability via a cheap DB-only endpoint."""
        try:
            me = self._client.get(_ME_PATH)
        except XActionsError as exc:
            return {'valid': False, 'user': None,
                    'reason': '%s: %s' % (exc.code or exc.status_code,
                                          exc.message)}
        if not me or not me.get('id'):
            return {'valid': False, 'user': None,
                    'reason': 'xactions_unexpected_response'}
        return {
            'valid': True, 'user': me, 'reason': 'xactions',
            'has_session': bool(me.get('hasSession')),
        }

    def fetch_user_posts(self, screen_name, limit=20, cursor=None,
                         per_post_limit=None):
        """Read the account's own posts with their engagers inline.

        The report reads the linked account named by this X account's
        ``accountId``, so ``screen_name`` is informational only and the run
        stays scoped to the account the caller asked for. ``per_post_limit``
        maps to the report's single per-type-per-post cap (clamped 10-1000).
        """
        body = {
            'posts': max(1, min(int(limit or 20), _MAX_POSTS)),
            'perPostLimit': max(
                10, min(int(per_post_limit or _PER_POST_LIMIT), 1000)),
            'types': _ENGAGEMENT_TYPES,
            'includeReplies': False,
            'minLikes': 0,
            'delayMs': 0,
        }
        body.update(self._account_args(required=True))
        payload = self._client.post(_REPORT_PATH, json=body)
        result = XActionsEnvelopeParser.report(payload)
        if result.get('truncated'):
            _LOGGER.warning(
                'XActions report for account %s was truncated (rate limit); '
                'only the posts read so far were stored.', self.account.id)
        return result

    def fetch_post_comments(self, tweet_id, limit=50, cursor=None):
        params = {'postId': str(tweet_id), 'limit': int(limit or 50)}
        params.update(self._account_args(required=True))
        payload = self._client.get(_COMMENTERS_PATH, params=params)
        return XActionsEnvelopeParser.commenters(payload)

    def fetch_post_retweeters(self, tweet_id, limit=100, cursor=None):
        """Unsupported per-post: XActions returns retweeters inside the report."""
        return {'users': [], 'cursor': '', 'has_more': False,
                'unsupported': True,
                'reason': 'xactions_audience_only_in_report'}

    def fetch_post_likers(self, tweet_id, limit=100, cursor=None):
        """Unsupported per-post: XActions returns likers inside the report."""
        return {'users': [], 'cursor': '', 'has_more': False,
                'unsupported': True,
                'reason': 'xactions_audience_only_in_report'}

    def supported_operations(self):
        return (
            'validate_session',
            'fetch_user_posts', 'fetch_post_comments',
            'fetch_post_retweeters', 'fetch_post_likers',
        )


def _register_xactions_provider():
    try:
        XProviderRegistry.register('xactions', __name__ + '.XActionsProvider')
    except (ImportError, AttributeError):
        _LOGGER.exception('Failed to register XActions provider')


_register_xactions_provider()
