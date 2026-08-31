# -*- coding: utf-8 -*-
import json
import logging
import requests

from odoo import _, fields, models
from odoo.tools.urls import urljoin as url_join

_logger = logging.getLogger(__name__)


class SocialLivePost(models.Model):
    _inherit = 'social.live.post'

    tiktok_video_id = fields.Char('TikTok Video ID')
    tiktok_publish_id = fields.Char('TikTok Publish ID',
        help="Returned by TikTok after video upload initialisation.")

    def _compute_live_post_link(self):
        tiktok_live_posts = self._filter_by_media_types(['tiktok']).filtered(
            lambda post: post.state == 'posted'
        )
        super(SocialLivePost, (self - tiktok_live_posts))._compute_live_post_link()

        for post in tiktok_live_posts:
            post.live_post_link = 'https://www.tiktok.com/@%s/video/%s' % (
                post.account_id.name, post.tiktok_video_id
            ) if post.tiktok_video_id else 'https://www.tiktok.com/'

    def _post(self):
        tiktok_live_posts = self._filter_by_media_types(['tiktok'])
        super(SocialLivePost, (self - tiktok_live_posts))._post()

        for live_post in tiktok_live_posts:
            live_post._post_tiktok()

    def _post_tiktok(self):
        """
        Publish a video to TikTok using the Content Posting API (FILE_UPLOAD).

        Flow:
          1. POST /v2/post/publish/video/init/  →  get upload_url + publish_id
          2. PUT <upload_url>                   →  upload the video binary
          3. TikTok processes the video asynchronously; publish_id is stored for reference.

        Docs: https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
        """
        self.ensure_one()
        account = self.account_id
        post = self.post_id

        # TikTok requires a video – abort early with a clear message if none attached
        video_ids = getattr(post, 'tiktok_video_ids', False)
        if not video_ids:
            reason = _(
                'TikTok posts require a video file. '
                'Please attach a video in the TikTok tab of your post.'
            )
            _logger.warning("Social TikTok: post failed (no video) — account=%s", account.name)
            self.write({'state': 'failed', 'failure_reason': reason})
            return

        video = video_ids[0]
        video_data = video.with_context(bin_size=False).raw
        video_size = len(video_data)

        title = (self.message or '')[:150]
        privacy_level = getattr(post, 'tiktok_privacy_level', None) or 'PUBLIC_TO_EVERYONE'

        # ── Step 1: initialise the upload ────────────────────────────────────
        # brand_content_toggle and brand_organic_toggle are REQUIRED by the API.
        init_payload = {
            'post_info': {
                'title': title,
                'privacy_level': privacy_level,
                'disable_duet': False,
                'disable_comment': False,
                'disable_stitch': False,
                'brand_content_toggle': False,
                'brand_organic_toggle': False,
                'is_aigc': False,
            },
            'source_info': {
                'source': 'FILE_UPLOAD',
                'video_size': video_size,
                'chunk_size': video_size,
                'total_chunk_count': 1,
            },
        }

        init_url = url_join(self.env['social.media']._TIKTOK_API_ENDPOINT, 'post/publish/video/init/')
        access_token = account.tiktok_access_token

        try:
            init_response = self._tiktok_api_post(
                init_url, init_payload, access_token, account)
        except Exception as e:
            _logger.warning(
                "Social TikTok: init exception — account=%s, url=%s, error=%s",
                account.name, init_url, e,
            )
            self.write({'state': 'failed', 'failure_reason': str(e)})
            return

        if not init_response.ok:
            error_detail = self._parse_tiktok_error(init_response)
            reason = _('TikTok publish initialisation failed: %s', error_detail)
            error_code = self._parse_tiktok_error_code(init_response)

            # ── Auto-retry: expired token ──────────────────────────────────
            if init_response.status_code == 401 and account.tiktok_refresh_token:
                _logger.info(
                    "Social TikTok: init got 401, attempting token refresh — account=%s",
                    account.name,
                )
                if account._tiktok_refresh_access_token():
                    try:
                        init_response = self._tiktok_api_post(
                            init_url, init_payload, account.tiktok_access_token, account)
                        if init_response.ok:
                            error_detail = None
                    except Exception as e:
                        _logger.warning(
                            "Social TikTok: init retry exception — account=%s, error=%s",
                            account.name, e,
                        )
                        self.write({'state': 'failed', 'failure_reason': str(e)})
                        return

            # ── Auto-retry: unaudited client, fallback to SELF_ONLY ─────────
            if (error_code == 'unaudited_client_can_only_post_to_private_accounts'
                    and privacy_level != 'SELF_ONLY'):
                _logger.info(
                    "Social TikTok: unaudited client, retrying with SELF_ONLY — "
                    "account=%s", account.name,
                )
                init_payload['post_info']['privacy_level'] = 'SELF_ONLY'
                try:
                    init_response = self._tiktok_api_post(
                        init_url, init_payload, account.tiktok_access_token, account)
                    if init_response.ok:
                        _logger.info(
                            "Social TikTok: retry with SELF_ONLY succeeded — account=%s",
                            account.name,
                        )
                        error_detail = None
                except Exception as e:
                    _logger.warning(
                        "Social TikTok: retry exception (SELF_ONLY) — account=%s, error=%s",
                        account.name, e,
                    )
                    self.write({'state': 'failed', 'failure_reason': str(e)})
                    return

            if error_detail is not None:
                _logger.warning(
                    "Social TikTok: init failed — account=%s, status=%s, privacy=%s, "
                    "response=%s",
                    account.name, init_response.status_code, privacy_level,
                    init_response.text[:500],
                )
                self.write({'state': 'failed', 'failure_reason': reason})
                return

        init_data = init_response.json().get('data', {})
        publish_id = init_data.get('publish_id')
        upload_url = init_data.get('upload_url')

        if not upload_url or not publish_id:
            _logger.warning(
                "Social TikTok: init OK but missing upload_url/publish_id — "
                "account=%s, response=%s",
                account.name, init_response.text[:500],
            )
            self.write({
                'state': 'failed',
                'failure_reason': _('TikTok did not return a valid upload URL.'),
            })
            return

        _logger.info(
            "Social TikTok: init succeeded — account=%s, publish_id=%s",
            account.name, publish_id,
        )

        # ── Step 2: upload the video binary ──────────────────────────────────
        content_type = video.mimetype or 'video/mp4'
        try:
            upload_response = requests.put(
                upload_url,
                data=video_data,
                headers={
                    'Content-Type': content_type,
                    'Content-Length': str(video_size),
                    'Content-Range': 'bytes 0-%d/%d' % (video_size - 1, video_size),
                },
                timeout=180,
            )
        except Exception as e:
            _logger.warning(
                "Social TikTok: upload exception — account=%s, publish_id=%s, error=%s",
                account.name, publish_id, e,
            )
            self.write({'state': 'failed', 'failure_reason': str(e)})
            return

        if upload_response.status_code not in (200, 201, 206):
            _logger.warning(
                "Social TikTok: upload failed — account=%s, publish_id=%s, "
                "status=%s, response=%s",
                account.name, publish_id, upload_response.status_code,
                upload_response.text[:500] if hasattr(upload_response, 'text') else '',
            )
            self.write({
                'state': 'failed',
                'failure_reason': _(
                    'TikTok video upload failed (HTTP %s).', upload_response.status_code
                ),
            })
            return

        _logger.info(
            "Social TikTok: upload succeeded — account=%s, publish_id=%s, state=posted",
            account.name, publish_id,
        )

        # TikTok processes the video asynchronously – mark as posted and store publish_id
        self.write({
            'tiktok_publish_id': publish_id,
            'state': 'posted',
            'failure_reason': False,
        })

    def _tiktok_api_post(self, url, payload, access_token, account):
        """Wrapper around requests.post for the TikTok init endpoint."""
        return requests.post(
            url,
            json=payload,
            headers={
                'Authorization': 'Bearer %s' % access_token,
                'Content-Type': 'application/json; charset=UTF-8',
            },
            timeout=30,
        )

    @staticmethod
    def _parse_tiktok_error_code(response):
        """Extract just the error code from a TikTok API error response."""
        try:
            return response.json().get('error', {}).get('code', '')
        except Exception:
            return ''

    @staticmethod
    def _parse_tiktok_error(response):
        """Extract a human-readable error string from a TikTok API error response.

        TikTok error format: {"error": {"code": "...", "message": "...", "log_id": "..."}}
        """
        try:
            error = response.json().get('error', {})
            code = error.get('code', '')
            message = error.get('message', '')
            log_id = error.get('log_id', '')
            parts = [p for p in (code, message) if p]
            detail = ' | '.join(parts)
            if log_id:
                detail = '%s (log_id: %s)' % (detail, log_id)
            return detail or response.text
        except Exception:
            return response.text or 'Unknown error (HTTP %s)' % response.status_code

    def _refresh_statistics(self):
        super()._refresh_statistics()
        # Per-post engagement stats are refreshed through the stream fetch (_fetch_stream_data)
        # rather than a separate call here, as TikTok's standard API does not expose a
        # single-video metrics endpoint for non-Research API credentials.
