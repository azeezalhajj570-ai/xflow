# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Parse XActions API payloads into the XProvider read DTOs.

Shapes handled (from the XActions source):

- ``POST /api/posts/report`` ->
  ``{success, account, postsRead, truncated, posts[], audience[], stats}``
  where each post is ``{id, text, createdAt, metrics{likes,retweets,replies,
  quotes,views}, audience{likers[],commenters[],retweeters[]}, counts, errors}``
  and a person is ``{userId|id, username, name, verified}``.

- ``GET /api/commenters`` -> ``{rootTweet, commenters[], totalReplies, hasMore}``

- ``GET /api/user/me`` -> ``{id, username, twitterUsername, twitterConnected,
  hasSession}``
"""


class XActionsEnvelopeParser:
    """Stateless: every method takes a raw payload and returns a DTO."""

    @staticmethod
    def person(raw):
        """Return a user DTO ``{id, username, name, profile_image_url}``."""
        if not isinstance(raw, dict):
            return {}
        user_id = raw.get('userId') or raw.get('id') or raw.get('user_id')
        if not user_id:
            return {}
        return {
            'id': str(user_id),
            'username': raw.get('username') or raw.get('screen_name') or '',
            'name': raw.get('name') or '',
            'profile_image_url': raw.get('avatar')
            or raw.get('profile_image_url') or '',
            'verified': bool(raw.get('verified')),
        }

    @staticmethod
    def person_as_comment(raw):
        """Return a comment-shaped DTO for a person.

        XActions commenters carry the person but not the reply text/id, so the
        commenter's user id stands in for the interaction's external id.
        """
        person = XActionsEnvelopeParser.person(raw)
        if not person:
            return {}
        return {
            'id': person['id'],
            'author_id': person['id'],
            'author_username': person['username'],
            'author_name': person['name'],
            'text': (raw.get('text') if isinstance(raw, dict) else '') or '',
        }

    @staticmethod
    def _people(items, as_comment=False):
        out = []
        for raw in items or []:
            dto = (XActionsEnvelopeParser.person_as_comment(raw) if as_comment
                   else XActionsEnvelopeParser.person(raw))
            if dto:
                out.append(dto)
        return out

    @staticmethod
    def post(raw):
        """Return a post DTO (with inline ``audience`` when present)."""
        if not isinstance(raw, dict):
            return {}
        post_id = str(raw.get('id') or '')
        if not post_id:
            return {}
        author = raw.get('author') or {}
        metrics = raw.get('metrics') or {}
        audience_raw = raw.get('audience') or {}
        dto = {
            'id': post_id,
            'text': raw.get('text') or '',
            'author_id': str(author.get('id') or ''),
            'author_username': author.get('username') or '',
            'author_name': author.get('name') or '',
            'created_at': raw.get('createdAt') or raw.get('created_at') or '',
            'favorite_count': int(metrics.get('likes') or 0),
            'retweet_count': int(metrics.get('retweets') or 0),
            'reply_count': int(metrics.get('replies') or 0),
            'quote_count': int(metrics.get('quotes') or 0),
            'media_urls': [
                m.get('url') for m in (raw.get('media') or [])
                if isinstance(m, dict) and m.get('url')],
            'in_reply_to_tweet_id': str(
                (raw.get('inReplyTo') or {}).get('tweetId') or ''),
            'raw': raw,
        }
        audience = {
            'likers': XActionsEnvelopeParser._people(audience_raw.get('likers')),
            'commenters': XActionsEnvelopeParser._people(
                audience_raw.get('commenters'), as_comment=True),
            'retweeters': XActionsEnvelopeParser._people(
                audience_raw.get('retweeters')),
        }
        if any(audience.values()):
            dto['audience'] = audience
        return dto

    @staticmethod
    def report(payload):
        """Return ``{'posts', 'cursor', 'has_more', 'truncated', 'stats'}``."""
        data = payload or {}
        posts = [XActionsEnvelopeParser.post(p) for p in (data.get('posts') or [])]
        return {
            'posts': [p for p in posts if p],
            'cursor': '',
            'has_more': False,
            'truncated': bool(data.get('truncated')),
            'stats': data.get('stats'),
        }

    @staticmethod
    def commenters(payload):
        """Return ``{'comments', 'cursor', 'has_more'}`` for comment DTOs."""
        data = payload or {}
        people = data.get('commenters') or data.get('users') or []
        return {
            'comments': XActionsEnvelopeParser._people(people, as_comment=True),
            'cursor': '',
            'has_more': bool(data.get('hasMore')),
        }
