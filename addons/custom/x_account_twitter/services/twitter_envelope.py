# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Normalization of X API v2 responses into provider DTOs.

Raw X API shapes (``{"data": {"retweeted": true, ...}}``) are translated here
(SRP) so the provider only deals with clean, provider-agnostic dicts and never
leaks raw API responses to x_account.
"""


class TwitterEnvelope:
    """Stateless parser for X API v2 envelopes."""

    @staticmethod
    def repost(envelope, tweet_id):
        """Return the normalized repost result DTO.

        X API v2 POST /2/users/{id}/retweets returns ``{"data": {"retweeted":
        true}}``; the created retweet id may not be echoed, so the caller's
        ``tweet_id`` is carried through as the reference.
        """
        data = (envelope or {}).get('data') or {}
        return {
            'success': bool(data.get('retweeted', True)),
            'operation': 'repost',
            'platform': 'x',
            'post_id': str(tweet_id),
            'external_id': str(data.get('id') or tweet_id),
        }

    @staticmethod
    def user(envelope):
        """Return {id, username, name} from a /2/users/me envelope, or {}."""
        data = (envelope or {}).get('data') or {}
        if not data.get('id'):
            return {}
        return {
            'id': str(data.get('id')),
            'username': data.get('username', ''),
            'name': data.get('name', ''),
        }
