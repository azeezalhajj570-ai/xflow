# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""GetXAPI DM service: send and list direct messages.

All methods return normalized DTOs via GetXAPIEnvelopeParser.
"""

from . import getxapi_envelope


class GetXAPIDMService:
    """DM operations over the GetXAPI REST API."""

    def __init__(self, client):
        self._client = client

    def send(self, recipient_id, text):
        """Send a direct message.

        :param recipient_id: X user ID of the recipient.
        :param text: Message text.
        :returns: {message_id, created_at}
        """
        if not recipient_id:
            raise ValueError('recipient_id is required')
        if not text:
            raise ValueError('text must be non-empty')
        data = self._client.post('/twitter/dm/send', json={
            'recipient_id': str(recipient_id),
            'text': text,
        })
        result = (data or {}).get('data') or data or {}
        return {
            'message_id': result.get('message_id') or result.get('id') or '',
            'created_at': result.get('created_at') or '',
        }

    def list(self, conversation_id=None, **params):
        """List DM conversations or messages in a conversation.

        :param conversation_id: If provided, list messages in this conversation.
            If omitted, list conversations.
        :returns: {conversations/messages: [...], cursor}
        """
        if conversation_id:
            body = {'conversation_id': str(conversation_id)}
            body.update(params)
            data = self._client.post('/twitter/dm/list', json=body)
            return getxapi_envelope.GetXAPIEnvelopeParser.dm_messages(
                data, conversation_id)
        data = self._client.get('/twitter/dm/list', params=params or None)
        return getxapi_envelope.GetXAPIEnvelopeParser.dm_conversations(data)
