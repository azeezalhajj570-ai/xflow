# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Parsing of OmniX API envelopes into plain, provider-agnostic data.

The OmniX API wraps every payload in ``{"status": ..., "data": ..., "error": ...}``
and uses many aliases for the same field (``id``/``rest_id``, ``conversation_id``/
``id``, ``created_at``/``createdAt``, ...). All of that normalization lives here
(SRP) so the provider only deals with clean dicts and the HTTP client only
deals with bytes.
"""


class OmniXEnvelopeParser:
    """Stateless parser: every method takes a raw envelope and returns a DTO."""

    # -------------------------------------------------------------- accounts
    @staticmethod
    def user(envelope):
        """Return {id, username, name} from a user-info envelope, or {}."""
        user = (envelope or {}).get('data') or {}
        user_id = user.get('id') or user.get('rest_id')
        if not user_id:
            return {}
        return {
            'id': str(user_id),
            'username': user.get('userName', '') or user.get('username', ''),
            'name': user.get('name', ''),
        }

    @staticmethod
    def home_user_id(envelope):
        """Return the authed userId from a home_timeline envelope, or None."""
        data = (envelope or {}).get('data') or {}
        return data.get('userId')

    # ---------------------------------------------------------- conversations
    @staticmethod
    def conversations(envelope, limit=50):
        """Return {conversations: [...], cursor} from a /dm/list envelope."""
        inbox = (envelope or {}).get('data') or {}
        conversations = inbox.get('conversations') or []
        if isinstance(conversations, dict):
            conversations = list(conversations.values())
        return {
            'conversations': [
                {
                    'conversation_id': conv.get('conversation_id') or conv.get('id'),
                    'type': conv.get('type', 'one_to_one'),
                    'participants': conv.get('participants') or [],
                    'participant_count': conv.get('participant_count', 0),
                    'last_message': conv.get('last_message'),
                    'group': conv.get('type') == 'group',
                }
                for conv in conversations[:limit]
            ],
            'cursor': (inbox.get('next_cursor') or {}).get('cursor_id') or inbox.get('cursor'),
        }

    @staticmethod
    def messages(envelope, conversation_id, limit=100):
        """Return {messages: [...], cursor} from a /dm/conversation envelope."""
        conv = (envelope or {}).get('data') or {}
        messages = conv.get('messages') or []
        return {
            'messages': [
                {
                    'id': msg.get('id') or msg.get('seq_id'),
                    'text': msg.get('text') or msg.get('body', ''),
                    'sender_id': msg.get('sender_id') or (msg.get('sender') or {}).get('id'),
                    'created_at': msg.get('created_at') or msg.get('createdAt'),
                    'conversation_id': conversation_id,
                    'from_me': bool(msg.get('from_me')),
                }
                for msg in messages[:limit]
            ],
            'cursor': conv.get('next_cursor'),
        }

    # -------------------------------------------------------------- webhooks
    @staticmethod
    def webhook(envelope, fallback_url=''):
        """Return {id, url, valid, secret} from a webhook envelope."""
        wh = (envelope or {}).get('data') or {}
        return {
            'id': str(wh.get('id', '')),
            'url': wh.get('url', fallback_url),
            'valid': bool(wh.get('valid')),
            'secret': wh.get('secret') or '',
        }

    @staticmethod
    def webhook_list(envelope):
        return ((envelope or {}).get('data') or {}).get('webhooks') or []
