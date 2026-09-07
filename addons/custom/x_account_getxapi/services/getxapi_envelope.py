# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Parsing of GetXAPI API envelopes into plain, provider-agnostic data.

GetXAPI wraps every payload in ``{"success": ..., "data": ..., "error": ...}``
(or similar shapes depending on the endpoint). All normalization lives here
(SRP) so the provider only deals with clean dicts and the HTTP client only
deals with bytes.
"""


class GetXAPIEnvelopeParser:
    """Stateless parser: every method takes a raw envelope and returns a DTO."""

    @staticmethod
    def user(envelope):
        """Return {id, username, name, ...} from a user-info envelope, or {}."""
        data = (envelope or {}).get('data') or envelope or {}
        user = data.get('user') or data.get('result') or data
        user_id = (
            user.get('rest_id')
            or user.get('id')
            or user.get('id_str')
            or user.get('userId')
        )
        if not user_id:
            return {}
        legacy = user.get('legacy') or {}
        return {
            'id': str(user_id),
            'username': (
                user.get('screen_name')
                or legacy.get('screen_name')
                or user.get('userName')
                or user.get('username')
                or ''
            ),
            'name': (
                user.get('name')
                or legacy.get('name')
                or ''
            ),
            'description': legacy.get('description') or user.get('description') or '',
            'followers_count': legacy.get('followers_count') or user.get('followers_count') or 0,
            'following_count': legacy.get('friends_count') or user.get('following_count') or 0,
            'statuses_count': legacy.get('statuses_count') or user.get('statuses_count') or 0,
            'verified': bool(legacy.get('verified') or user.get('verified')),
            'is_blue_verified': bool(
                user.get('is_blue_verified')
                or user.get('verified_type') == 'Blue'
            ),
            'profile_image_url': legacy.get('profile_image_url_https') or user.get('profile_image_url') or '',
        }

    @staticmethod
    def tweet(envelope):
        """Return a normalized tweet DTO from an envelope, or {}."""
        data = (envelope or {}).get('data') or envelope or {}
        tweet = data.get('tweet') or data.get('result') or data
        tweet_id = (
            tweet.get('rest_id')
            or tweet.get('id')
            or tweet.get('id_str')
            or tweet.get('tweet_id')
        )
        if not tweet_id:
            return {}
        legacy = tweet.get('legacy') or tweet.get('core') or {}
        return {
            'id': str(tweet_id),
            'text': (
                tweet.get('text')
                or legacy.get('full_text')
                or legacy.get('text')
                or ''
            ),
            'author_id': str(
                tweet.get('author_id')
                or legacy.get('user_id_str')
                or (legacy.get('user_id') and str(legacy['user_id']))
                or ''
            ),
            'created_at': tweet.get('created_at') or legacy.get('created_at') or '',
            'favorite_count': legacy.get('favorite_count') or tweet.get('favorite_count') or 0,
            'retweet_count': legacy.get('retweet_count') or tweet.get('retweet_count') or 0,
            'reply_count': legacy.get('reply_count') or tweet.get('reply_count') or 0,
            'quote_count': legacy.get('quote_count') or tweet.get('quote_count') or 0,
            'conversation_id': str(
                tweet.get('conversation_id_str')
                or legacy.get('conversation_id_str')
                or ''
            ),
            'in_reply_to_tweet_id': str(
                tweet.get('in_reply_to_status_id_str')
                or legacy.get('in_reply_to_status_id_str')
                or ''
            ),
        }

    @staticmethod
    def search_results(envelope):
        """Return {tweets: [...], cursor, has_more} from a search envelope."""
        data = (envelope or {}).get('data') or envelope or {}
        entries = data.get('entries') or data.get('tweets') or data.get('results') or []
        if isinstance(entries, dict):
            entries = list(entries.values())
        tweets = []
        for entry in entries:
            t = GetXAPIEnvelopeParser.tweet({'data': entry})
            if t.get('id'):
                tweets.append(t)
        cursor_info = data.get('cursor') or data.get('next_cursor') or {}
        cursor = ''
        if isinstance(cursor_info, dict):
            cursor = (
                cursor_info.get('cursor_id')
                or cursor_info.get('value')
                or cursor_info.get('cursor')
                or ''
            )
        else:
            cursor = str(cursor_info) if cursor_info else ''
        has_more = bool(data.get('has_more', bool(cursor)))
        return {
            'tweets': tweets,
            'cursor': cursor,
            'has_more': has_more,
        }

    @staticmethod
    def thread(envelope):
        """Return {tweets: [...]} from a thread envelope."""
        data = (envelope or {}).get('data') or envelope or {}
        entries = data.get('entries') or data.get('tweets') or []
        if isinstance(entries, dict):
            entries = list(entries.values())
        tweets = []
        for entry in entries:
            t = GetXAPIEnvelopeParser.tweet({'data': entry})
            if t.get('id'):
                tweets.append(t)
        return {'tweets': tweets}

    @staticmethod
    def dm_conversations(envelope, limit=50):
        """Return {conversations: [...], cursor} from a DM list envelope."""
        data = (envelope or {}).get('data') or envelope or {}
        conversations = data.get('conversations') or data.get('entries') or []
        if isinstance(conversations, dict):
            conversations = list(conversations.values())
        return {
            'conversations': [
                {
                    'conversation_id': (
                        conv.get('conversation_id')
                        or conv.get('id')
                        or conv.get('conversationId')
                    ),
                    'type': conv.get('type', 'one_to_one'),
                    'participants': conv.get('participants') or [],
                    'participant_count': conv.get('participant_count', 0),
                    'last_message': conv.get('last_message'),
                    'group': conv.get('type') == 'group',
                }
                for conv in conversations[:limit]
            ],
            'cursor': (
                (data.get('next_cursor') or {}).get('cursor_id')
                or data.get('cursor')
                or data.get('next_cursor')
                or ''
            ) if isinstance(data.get('next_cursor'), dict) else (
                data.get('next_cursor') or data.get('cursor') or ''
            ),
        }

    @staticmethod
    def dm_messages(envelope, conversation_id, limit=100):
        """Return {messages: [...], cursor} from a DM conversation envelope."""
        data = (envelope or {}).get('data') or envelope or {}
        messages = data.get('messages') or data.get('entries') or []
        return {
            'messages': [
                {
                    'id': msg.get('id') or msg.get('message_id') or msg.get('messageId'),
                    'text': msg.get('text') or msg.get('message_text') or msg.get('body', ''),
                    'sender_id': (
                        msg.get('sender_id')
                        or msg.get('senderId')
                        or (msg.get('sender') or {}).get('id')
                        or msg.get('message_data', {}).get('sender_id')
                        or ''
                    ),
                    'created_at': msg.get('created_at') or msg.get('createdAt') or msg.get('time') or '',
                    'conversation_id': conversation_id,
                    'from_me': bool(msg.get('from_me') or msg.get('is_from_me')),
                }
                for msg in messages[:limit]
            ],
            'cursor': data.get('next_cursor') or data.get('cursor') or '',
        }

    @staticmethod
    def followers(envelope):
        """Return {users: [...], cursor, has_more} from a followers/following envelope."""
        data = (envelope or {}).get('data') or envelope or {}
        entries = data.get('entries') or data.get('users') or data.get('result', {}).get('users') or []
        if isinstance(entries, dict):
            entries = list(entries.values())
        users = []
        for entry in entries:
            u = GetXAPIEnvelopeParser.user({'data': entry})
            if u.get('id'):
                users.append(u)
        cursor_info = data.get('cursor') or data.get('next_cursor') or {}
        cursor = ''
        if isinstance(cursor_info, dict):
            cursor = cursor_info.get('value') or cursor_info.get('cursor') or ''
        else:
            cursor = str(cursor_info) if cursor_info else ''
        has_more = bool(data.get('has_more', bool(cursor)))
        return {
            'users': users,
            'cursor': cursor,
            'has_more': has_more,
        }

    @staticmethod
    def follow_result(envelope):
        """Return the normalized follow/unfollow result DTO."""
        data = (envelope or {}).get('data') or envelope or {}
        result = data.get('result') or data
        return {
            'success': bool(result.get('following') or result.get('followed') or data.get('success', True)),
            'operation': 'follow',
            'platform': 'x',
            'user_id': str(result.get('user_id') or result.get('id') or ''),
        }

    @staticmethod
    def unfollow_result(envelope):
        """Return the normalized unfollow result DTO."""
        data = (envelope or {}).get('data') or envelope or {}
        result = data.get('result') or data
        return {
            'success': bool(not result.get('following', True) or data.get('success', True)),
            'operation': 'unfollow',
            'platform': 'x',
            'user_id': str(result.get('user_id') or result.get('id') or ''),
        }

    @staticmethod
    def retweet_result(envelope, tweet_id):
        """Return the normalized retweet result DTO."""
        data = (envelope or {}).get('data') or envelope or {}
        result = data.get('result') or data
        return {
            'success': bool(result.get('retweeted') or data.get('success', True)),
            'operation': 'repost',
            'platform': 'x',
            'post_id': str(tweet_id),
            'external_id': str(result.get('id') or tweet_id),
        }

    @staticmethod
    def like_result(envelope, tweet_id):
        """Return the normalized like result DTO."""
        data = (envelope or {}).get('data') or envelope or {}
        result = data.get('result') or data
        return {
            'success': bool(result.get('favorited') or result.get('liked') or data.get('success', True)),
            'operation': 'like',
            'platform': 'x',
            'post_id': str(tweet_id),
            'external_id': str(result.get('id') or tweet_id),
        }

    @staticmethod
    def create_tweet_result(envelope):
        """Return the normalized tweet creation result DTO."""
        data = (envelope or {}).get('data') or envelope or {}
        result = data.get('result') or data.get('tweet') or data
        tweet_id = (
            result.get('rest_id')
            or result.get('id')
            or result.get('id_str')
            or result.get('tweet_id')
            or ''
        )
        return {
            'success': bool(tweet_id),
            'operation': 'create_tweet',
            'platform': 'x',
            'tweet_id': str(tweet_id),
            'text': result.get('text') or '',
        }

    @staticmethod
    def media_upload_result(envelope):
        """Return the normalized media upload result DTO."""
        data = (envelope or {}).get('data') or envelope or {}
        result = data.get('result') or data
        media_id = (
            result.get('media_id')
            or result.get('media_id_string')
            or result.get('id')
            or ''
        )
        return {
            'success': bool(media_id),
            'media_id': str(media_id),
        }

    @staticmethod
    def bookmark_result(envelope, tweet_id):
        """Return the normalized bookmark result DTO."""
        data = (envelope or {}).get('data') or envelope or {}
        result = data.get('result') or data
        return {
            'success': bool(result.get('bookmarked') or data.get('success', True)),
            'operation': 'bookmark',
            'platform': 'x',
            'post_id': str(tweet_id),
            'external_id': str(result.get('id') or tweet_id),
        }

    @staticmethod
    def unbookmark_result(envelope, tweet_id):
        """Return the normalized unbookmark result DTO."""
        data = (envelope or {}).get('data') or envelope or {}
        result = data.get('result') or data
        return {
            'success': bool(result.get('unbookmarked') or data.get('success', True)),
            'operation': 'unbookmark',
            'platform': 'x',
            'post_id': str(tweet_id),
            'external_id': str(result.get('id') or tweet_id),
        }
