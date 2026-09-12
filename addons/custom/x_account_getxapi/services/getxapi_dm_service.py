# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""GetXAPI DM service: send and list direct messages.

All methods return normalized DTOs via GetXAPIEnvelopeParser.
"""

from . import getxapi_envelope


class GetXAPIDMService:
    """DM operations over the GetXAPI REST API."""

    def __init__(self, client):
        self._client = client

    def send(self, recipient_id, text, auth_token=None):
        """Send a direct message.

        GetXAPI requires the sender's ``auth_token`` so it can act as the
        account that owns the DM thread.

        :param recipient_id: X user ID of the recipient.
        :param text: Message text.
        :param auth_token: The account's GetXAPI auth token (required).
        :returns: {message_id, created_at}
        """
        if not recipient_id:
            raise ValueError('recipient_id is required')
        if not text:
            raise ValueError('text must be non-empty')
        if not auth_token:
            raise ValueError('auth_token is required to send DMs')
        data = self._client.post('/twitter/dm/send', json={
            'auth_token': auth_token,
            'recipient_id': str(recipient_id),
            'text': text,
        })
        result = (data or {}).get('data') or data or {}
        return {
            'message_id': result.get('message_id') or result.get('id') or '',
            'created_at': result.get('created_at') or '',
        }

    def list(self, auth_token=None, cursor=None, count=50, tab=None, **params):
        """List the auth_token holder's DM inbox conversations.

        GetXAPI exposes the inbox as POST /twitter/dm/list (GET 404s);
        ``auth_token`` is required.
        """
        if not auth_token:
            raise ValueError('auth_token is required to list DM conversations')
        body = {'auth_token': auth_token, 'count': min(int(count or 50), 50)}
        if cursor:
            body['cursor'] = cursor
        if tab:
            body['tab'] = tab
        body.update(params)
        data = self._client.post('/twitter/dm/list', json=body)
        return getxapi_envelope.GetXAPIEnvelopeParser.dm_conversations(
            data, limit=count)

    def conversation(self, conversation_id, auth_token=None, cursor=None,
                     count=100, **params):
        """List messages in one conversation via POST /twitter/dm/conversation."""
        if not conversation_id:
            raise ValueError('conversation_id is required')
        if not auth_token:
            raise ValueError('auth_token is required to list DM messages')
        body = {'auth_token': auth_token,
                'conversation_id': str(conversation_id),
                'count': min(int(count or 100), 50)}
        if cursor:
            body['cursor'] = cursor
        body.update(params)
        data = self._client.post('/twitter/dm/conversation', json=body)
        return getxapi_envelope.GetXAPIEnvelopeParser.dm_messages(
            data, conversation_id, limit=count)
