# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""GetXAPI media service: upload media for use in tweets.

Supports the existing Odoo attachment workflow: accepts an ir.attachment id,
reads the binary data, sends to GetXAPI, returns media_id.
"""

import base64
import logging

from . import getxapi_envelope

_LOGGER = logging.getLogger(__name__)


class GetXAPIMediaService:
    """Media upload operations over the GetXAPI REST API."""

    def __init__(self, client):
        self._client = client

    def upload(self, file_data, media_type='image/jpeg', filename=None):
        """Upload media to GetXAPI.

        :param file_data: Base64-encoded file data or raw bytes.
        :param media_type: MIME type of the media.
        :param filename: Optional filename.
        :returns: {success, media_id}
        """
        if isinstance(file_data, bytes):
            file_data = base64.b64encode(file_data).decode('ascii')
        body = {
            'media_data': file_data,
            'media_type': media_type,
        }
        if filename:
            body['filename'] = filename
        data = self._client.post('/twitter/media/upload', json=body)
        return getxapi_envelope.GetXAPIEnvelopeParser.media_upload_result(data)

    def upload_attachment(self, env, attachment_id):
        """Upload an Odoo ir.attachment as media.

        :param env: Odoo environment.
        :param attachment_id: ir.attachment record id.
        :returns: {success, media_id}
        """
        attachment = env['ir.attachment'].sudo().browse(attachment_id)
        if not attachment.exists():
            raise ValueError('Attachment %s not found' % attachment_id)
        return self.upload(
            attachment.datas,
            media_type=attachment.mimetype or 'application/octet-stream',
            filename=attachment.name,
        )
