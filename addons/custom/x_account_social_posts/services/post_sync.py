# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Fetch an X account's posts and their interactions into Social Marketing.

The service is provider-agnostic: it asks the account for a provider
(`get_provider_for_operation`) and reads through the XProvider read contract
(`fetch_user_posts` / `fetch_post_comments` / `fetch_post_retweeters` /
`fetch_post_likers`). Upserts are keyed on the external id so repeated runs
update instead of duplicating.
"""

import logging
from datetime import timezone

from dateutil import parser as date_parser

from odoo import fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_TIMELINE_LIMIT = 20
_COMMENT_LIMIT = 50
_RETWEETER_LIMIT = 100
_LIKER_LIMIT = 50


def _parse_x_datetime(value):
    """Parse an X timestamp into a naive UTC datetime, or False."""
    if not value:
        return False
    try:
        parsed = date_parser.parse(str(value))
    except (ValueError, TypeError, OverflowError):
        return False
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


class XPostSync:
    """Upsert X posts and interactions for one social.account."""

    def __init__(self, env, account):
        self.env = env
        self.account = account

    # --- timeline --------------------------------------------------------

    def _resolve_provider(self, provider_code, operation):
        """Resolve the provider for one run.

        ``provider_code`` lets a caller pick a provider for a single operation
        (the fetch wizard's Provider field); when empty, the account's own
        provider/routing decides.
        """
        if provider_code:
            from odoo.addons.x_account.services.x_service import XService
            return XService.get_provider_by_code(self.account, provider_code)
        return self.account.get_provider_for_operation(operation)

    def sync_timeline(self, stream, limit=_TIMELINE_LIMIT, per_post_limit=None,
                      provider_code=None):
        """Fetch the account's own posts into `stream` (social.stream).

        ``per_post_limit`` caps the users read per interaction type per post;
        providers that return engagers inline use it, per-call providers ignore
        it. ``provider_code`` overrides the account's provider for this run.
        """
        stream.ensure_one()
        account = stream.account_id
        provider = self._resolve_provider(provider_code, 'fetch_user_posts')
        fetch = getattr(provider, 'fetch_user_posts', None)
        if not fetch:
            return {'created': 0, 'updated': 0, 'posts': 0, 'unsupported': True}

        handle = account.social_account_handle or account.name
        if not handle:
            raise UserError(
                'Set the X handle (@username) on the account before fetching '
                'its posts.')

        result = fetch(
            handle.lstrip('@'), limit=limit,
            per_post_limit=per_post_limit) or {}
        posts = result.get('posts') or []
        Post = self.env['social.stream.post'].sudo()
        created = updated = 0
        inline = {'comments': 0, 'retweets': 0, 'likes': 0}
        for dto in posts:
            vals = self._post_vals(dto, account)
            if not vals:
                continue
            existing = Post.search([
                ('stream_id', '=', stream.id),
                ('x_tweet_id', '=', vals['x_tweet_id']),
            ], limit=1)
            if existing:
                existing.write(vals)
                post = existing
                updated += 1
            else:
                vals['stream_id'] = stream.id
                post = Post.create(vals)
                created += 1
            # Providers whose timeline read returns engagers inline (XActions
            # /api/posts/report) hand them over here, saving a per-post round
            # trip; providers that don't leave `audience` absent.
            if dto.get('audience'):
                counts = self._store_inline_audience(post, dto['audience'])
                for key in inline:
                    inline[key] += counts[key]
        _logger.info(
            'X posts sync for account %s: %s post(s) (%s new, %s updated)',
            account.id, len(posts), created, updated)
        return {
            'created': created, 'updated': updated, 'posts': len(posts),
            'unsupported': result.get('unsupported', False),
            # True when the provider returned engagers with the timeline, so
            # the caller need not fetch each post's interactions separately.
            'inline_interactions': any(inline.values()),
            'interactions': inline,
        }

    def _post_vals(self, dto, account):
        tweet_id = str(dto.get('id') or '').strip()
        if not tweet_id:
            return {}
        username = (dto.get('author_username')
                    or account.social_account_handle or '').lstrip('@')
        return {
            'x_tweet_id': tweet_id,
            'message': dto.get('text') or '',
            'author_name': dto.get('author_name') or username or account.name,
            'published_date': _parse_x_datetime(dto.get('created_at')),
            'x_author_x_id': str(dto.get('author_id') or ''),
            'x_author_x_username': username,
            'x_favorite_count': int(dto.get('favorite_count') or 0),
            'x_retweet_count': int(dto.get('retweet_count') or 0),
            'x_reply_count': int(dto.get('reply_count') or 0),
            'x_quote_count': int(dto.get('quote_count') or 0),
            'x_source_provider': account.x_provider or '',
            'x_raw_metadata': dto.get('raw') or {},
        }

    def _store_inline_audience(self, stream_post, audience):
        """Store engagers a provider returned inline with the timeline read.

        `audience` is ``{'likers': [user DTO], 'commenters': [comment/user
        DTO], 'retweeters': [user DTO]}``. No extra provider call is made.
        Returns the stored counts per interaction kind.
        """
        counts = {'comments': 0, 'retweets': 0, 'likes': 0}
        for kind, key, bucket in (('comment', 'commenters', 'comments'),
                                  ('retweet', 'retweeters', 'retweets'),
                                  ('like', 'likers', 'likes')):
            for dto in audience.get(key) or []:
                if self._store_interaction(stream_post, kind, dto):
                    counts[bucket] += 1
        if any(counts.values()):
            stream_post.sudo().write(
                {'x_interactions_fetched_at': fields.Datetime.now()})
        return counts

    # --- interactions ----------------------------------------------------

    def sync_interactions(self, stream_post, kinds=('comment', 'retweet', 'like'),
                          max_comments=_COMMENT_LIMIT,
                          max_retweeters=_RETWEETER_LIMIT,
                          max_likers=_LIKER_LIMIT, provider_code=None):
        """Fetch and store the interactions on one fetched post.

        ``provider_code`` overrides the account's provider for this run.
        """
        stream_post.ensure_one()
        tweet_id = stream_post.x_tweet_id
        summary = {'comments': 0, 'retweets': 0, 'likes': 0,
                   'unsupported_notes': []}
        if not tweet_id:
            return summary

        provider = self._resolve_provider(provider_code, 'fetch_post_comments')
        if 'comment' in kinds:
            summary['comments'] = self._sync_kind(
                stream_post, provider, 'fetch_post_comments', 'comment',
                tweet_id, max_comments, summary['unsupported_notes'])
        if 'retweet' in kinds:
            summary['retweets'] = self._sync_kind(
                stream_post, provider, 'fetch_post_retweeters', 'retweet',
                tweet_id, max_retweeters, summary['unsupported_notes'])
        if 'like' in kinds:
            summary['likes'] = self._sync_kind(
                stream_post, provider, 'fetch_post_likers', 'like',
                tweet_id, max_likers, summary['unsupported_notes'])

        stream_post.sudo().write(
            {'x_interactions_fetched_at': fields.Datetime.now()})
        return summary

    def _sync_kind(self, stream_post, provider, method_name, kind, tweet_id,
                   limit, notes):
        fetch = getattr(provider, method_name, None)
        if not fetch:
            return 0
        result = fetch(tweet_id, limit=limit) or {}
        if result.get('unsupported'):
            notes.append(
                '%s records are not available from this provider (%s).' % (
                    kind, result.get('reason') or 'unsupported'))
            return 0
        items = result.get('comments' if kind == 'comment' else 'users') or []
        count = 0
        for dto in items:
            if self._store_interaction(stream_post, kind, dto):
                count += 1
        return count

    def _store_interaction(self, stream_post, kind, dto):
        if kind == 'comment':
            external_id = str(dto.get('id') or '').strip()
            if not external_id:
                return False
            vals = {
                'text': dto.get('text') or '',
                'external_created_at': _parse_x_datetime(dto.get('created_at')),
                'like_count': int(dto.get('favorite_count') or 0),
                'reply_count': int(dto.get('reply_count') or 0),
                'retweet_count': int(dto.get('retweet_count') or 0),
                'author_x_id': str(dto.get('author_id') or ''),
                'author_x_username': (dto.get('author_username') or '').lstrip('@'),
                'author_name': dto.get('author_name') or '',
            }
        else:
            external_id = str(dto.get('id') or '').strip()
            if not external_id:
                return False
            vals = {
                'author_x_id': external_id,
                'author_x_username': (dto.get('username') or '').lstrip('@'),
                'author_name': dto.get('name') or '',
            }

        author_x_id = vals.get('author_x_id')
        partner = self._resolve_partner(
            author_x_id, vals.get('author_x_username'), vals.get('author_name'))
        if partner:
            vals['author_partner_id'] = partner.id
        vals['raw_metadata'] = dto.get('raw') or {}
        vals['provider'] = self.account.x_provider or ''

        Interaction = self.env['x.post.interaction'].sudo()
        existing = Interaction.search([
            ('stream_post_id', '=', stream_post.id),
            ('kind', '=', kind),
            ('external_id', '=', external_id),
        ], limit=1)
        if existing:
            existing.write(vals)
            return True
        vals.update({
            'stream_post_id': stream_post.id,
            'kind': kind,
            'external_id': external_id,
        })
        Interaction.create(vals)
        return True

    def _resolve_partner(self, x_user_id, username, name):
        x_user_id = str(x_user_id or '').strip()
        if not x_user_id:
            return self.env['res.partner']
        Partner = self.env['res.partner'].sudo()
        partner = Partner.search([('x_user_id', '=', x_user_id)], limit=1)
        if partner:
            return partner
        return Partner.create({
            'name': name or username or 'X user %s' % x_user_id,
            'type': 'contact',
            'partner_share': True,
            'x_user_id': x_user_id,
            'x_username': username or '',
        })
