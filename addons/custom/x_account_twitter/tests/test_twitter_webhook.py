import hashlib
import hmac
import json
from base64 import b64encode
from unittest.mock import Mock, patch

from odoo.tests import tagged

from odoo.addons.x_account_twitter.services import twitter_errors
from odoo.addons.x_account_twitter.services.twitter_activity import TwitterActivity
from odoo.addons.x_account_twitter.services.twitter_webhook import TwitterWebhook
from odoo.addons.x_account_twitter.services.xchat_decryptor import XChatDecryptor
from odoo.addons.x_account_twitter.services.twitter_provider import TwitterProvider

from .common import XAccountTwitterTestBase

OWNER_ID = '999000999000999000'
CONSUMER_SECRET = 'test-consumer-secret'
APP_BEARER = 'test-app-bearer'


def _envelope(event_type, event_uuid, user_id=OWNER_ID, payload=None):
    return {
        'data': {
            'event_uuid': event_uuid,
            'event_type': event_type,
            'filter': {'user_id': user_id},
            'payload': payload or {},
            'includes': {},
        },
    }


def _dm_payload(sender_id, recipient_id, message_id, text, outbound=False):
    return {
        'direct_message_events': [
            {
                'type': 'message_create',
                'id': message_id,
                'created_timestamp': '1725200000000',
                'message_create': {
                    'sender_id': sender_id,
                    'target': {'recipient_id': recipient_id},
                    'message_data': {'text': text},
                },
            },
        ],
        'users': {
            sender_id: {'data': {'name': 'Alice', 'username': 'alice'}},
            recipient_id: {'data': {'name': 'Owner', 'username': 'owner'}},
        },
    }


def _chat_payload(conv_id, sender_id, message_id, group=False):
    return {
        'conversation_id': conv_id,
        'sender_id': sender_id,
        'id': message_id,
        'created_at_msec': '1725200000000',
        'encoded_event': 'encrypted-ish',
        'group': group,
    }


def _sign(secret, raw):
    return 'sha256=%s' % b64encode(
        hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterWebhookService(XAccountTwitterTestBase):

    def _service(self):
        return TwitterWebhook(
            self.env, consumer_secret=CONSUMER_SECRET,
            app_bearer_token=APP_BEARER)

    def test_crc_response_hmac(self):
        resp = self._service().crc_response('the-crc-token')
        expected = _sign(CONSUMER_SECRET, b'the-crc-token')
        self.assertIn('response_token', resp)
        self.assertEqual(resp['response_token'], expected)

    def test_crc_response_without_secret_raises(self):
        service = TwitterWebhook(self.env, consumer_secret='')
        with self.assertRaises(twitter_errors.TwitterAuthenticationError):
            service.crc_response('token')

    def test_verify_signature_valid(self):
        raw = b'{"data":{"event_type":"dm.received"}}'
        sig = _sign(CONSUMER_SECRET, raw)
        self.assertTrue(self._service().verify_signature(raw, sig))

    def test_verify_signature_invalid(self):
        raw = b'{"data":{"event_type":"dm.received"}}'
        self.assertFalse(self._service().verify_signature(raw, 'sha256=wrong'))

    def test_verify_signature_missing(self):
        self.assertFalse(self._service().verify_signature(b'body', None))

    def test_register_webhook_posts_url(self):
        service = self._service()
        self.env['ir.config_parameter'].sudo().set_param(
            'x_account_twitter.webhook_base_url', 'https://x.example.com')
        with patch('odoo.addons.x_account_twitter.services.twitter_webhook.requests.request',
                   return_value=Mock(ok=True, status_code=201, content=b'{}',
                                     json=lambda: {'data': {'webhook_id': 'wh1',
                                                            'url': 'https://x.example.com/x_account/twitter/webhook'}})) as mocked:
            result = service.register_webhook()
        self.assertEqual(result.get('webhook_id'), 'wh1')
        self.assertEqual(mocked.call_args.args[0], 'POST')
        self.assertTrue(mocked.call_args.args[1].endswith('/2/webhooks'))

    def test_register_webhook_without_base_url_raises(self):
        with patch('odoo.addons.x_account_twitter.services.twitter_webhook.requests.request',
                   return_value=Mock(ok=True, content=b'{}', json=lambda: {})):
            with self.assertRaises(twitter_errors.TwitterError):
                self._service().register_webhook()

    def test_register_webhook_safe_treats_http_400_as_already_registered(self):
        """X rejects duplicate webhook registration with 400
        (WebhookLimitExceeded), not a permission error. safe=True must fall back
        to listing and returning the existing webhook for that URL."""
        service = self._service()
        self.env['ir.config_parameter'].sudo().set_param(
            'x_account_twitter.webhook_base_url', 'https://x.example.com')
        registered_url = 'https://x.example.com/x_account/twitter/webhook'
        dup_resp = Mock(ok=False, status_code=400, content=b'{}', json=lambda: {
            'detail': 'One or more parameters to your request was invalid.',
            'errors': [{'message': 'WebhookLimitExceeded: '}],
        })
        list_resp = Mock(ok=True, status_code=200, content=b'{}', json=lambda: {
            'data': [{'id': 'wh-existing', 'url': registered_url, 'valid': True}],
        })
        with patch('odoo.addons.x_account_twitter.services.twitter_webhook.requests.request',
                   side_effect=[dup_resp, list_resp]):
            result = service.register_webhook(safe=True)
        self.assertEqual(result.get('id'), 'wh-existing')

    def test_register_webhook_unsafe_re_raises_http_400(self):
        """safe=False must not swallow the 400 (caller wants to know)."""
        service = self._service()
        self.env['ir.config_parameter'].sudo().set_param(
            'x_account_twitter.webhook_base_url', 'https://x.example.com')
        dup_resp = Mock(ok=False, status_code=400, content=b'{}', json=lambda: {
            'detail': 'invalid', 'errors': [{'message': 'WebhookLimitExceeded: '}],
        })
        with patch('odoo.addons.x_account_twitter.services.twitter_webhook.requests.request',
                   return_value=dup_resp):
            with self.assertRaises(twitter_errors.TwitterError) as ctx:
                service.register_webhook(safe=False)
        self.assertEqual(ctx.exception.code, 'http_400')

    def test_create_subscription_sends_event_type(self):
        service = self._service()
        with patch('odoo.addons.x_account_twitter.services.twitter_webhook.requests.request',
                   return_value=Mock(ok=True, status_code=200, content=b'{}',
                                     json=lambda: {'data': {'subscription_id': 'sub1'}})) as mocked:
            result = service.create_subscription('123', 'dm.received', webhook_id='wh1')
        self.assertEqual(result.get('subscription_id'), 'sub1')
        body = mocked.call_args.kwargs['json']
        self.assertEqual(body['event_type'], 'dm.received')
        self.assertEqual(body['filter']['user_id'], '123')

    def test_create_subscription_uses_user_access_token(self):
        """Subscription create must authenticate with the account's OAuth 2.0
        user-context access token (app bearer is rejected by X)."""
        service = self._service()
        with patch('odoo.addons.x_account_twitter.services.twitter_webhook.requests.request',
                   return_value=Mock(ok=True, status_code=200, content=b'{}',
                                     json=lambda: {'data': {'subscription_id': 'sub-u'}})) as mocked:
            result = service.create_subscription(
                '123', 'chat.received', webhook_id='wh1',
                access_token='user-access-token')
        self.assertEqual(result.get('subscription_id'), 'sub-u')
        headers = mocked.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer user-access-token')


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterActivityIngest(XAccountTwitterTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Webhook Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'owner',
            'twitter_user_id': OWNER_ID,
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_oauth2_access_token': 'fake-at',
            'x_oauth2_refresh_token': 'fake-rt',
        })

    def _activity(self):
        return TwitterActivity(self.env)

    def test_ingest_dm_creates_event_and_task(self):
        payload = _dm_payload(OWNER_ID, '111', 'dm-1', 'hello', outbound=True)
        result = self._activity().ingest_webhook(
            _envelope('dm.sent', 'uuid-1', payload=payload))
        self.assertEqual(result['status'], 'accepted')
        event = self.env['x.twitter.event'].sudo().search(
            [('event_uuid', '=', 'uuid-1')])
        self.assertTrue(event)
        self.assertEqual(event.account_id.id, self.account.id)
        task = self.env['x.account.task'].sudo().search(
            [('id', '=', event.task_id.id)])
        self.assertTrue(task)
        self.assertEqual(task.operation, 'process_webhook_event')

    def test_ingest_duplicate_event_skipped(self):
        payload = _dm_payload(OWNER_ID, '111', 'dm-1', 'hi')
        self._activity().ingest_webhook(_envelope('dm.sent', 'uuid-dup', payload=payload))
        result = self._activity().ingest_webhook(
            _envelope('dm.sent', 'uuid-dup', payload=payload))
        self.assertEqual(result['status'], 'skipped')
        count = self.env['x.twitter.event'].sudo().search_count(
            [('event_uuid', '=', 'uuid-dup')])
        self.assertEqual(count, 1)

    def test_ingest_concurrent_duplicate_race_skipped(self):
        """A UNIQUE(event_uuid) violation from a concurrent delivery of the
        same event must be treated as a duplicate skip, not a 500."""
        import psycopg2
        payload = _dm_payload(OWNER_ID, '111', 'dm-race', 'hi')
        # Simulate the TOCTOU race: both requests pass the pre-search, then the
        # second insert hits the unique constraint.
        from odoo.addons.x_account_twitter.services.twitter_activity import TwitterActivity
        real_create = self.env['x.twitter.event'].sudo().create
        calls = {'n': 0}

        def flaky_create(vals_list):
            calls['n'] += 1
            if calls['n'] == 2:
                raise psycopg2.IntegrityError(
                    'duplicate key value violates unique constraint '
                    '"x_twitter_event_event_uuid_uniq"')
            return real_create(vals_list)

        with patch.object(type(self.env['x.twitter.event'].sudo()), 'create',
                          side_effect=flaky_create):
            first = self._activity().ingest_webhook(
                _envelope('dm.sent', 'uuid-race', payload=payload))
            second = self._activity().ingest_webhook(
                _envelope('dm.sent', 'uuid-race', payload=payload))
        self.assertEqual(first['status'], 'accepted')
        self.assertEqual(second['status'], 'skipped')
        self.assertEqual(second['reason'], 'duplicate')
        count = self.env['x.twitter.event'].sudo().search_count(
            [('event_uuid', '=', 'uuid-race')])
        self.assertEqual(count, 1)

    def test_ensure_webhook_subscriptions_manual_mode_without_bearer(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('x_account_twitter.webhook_enabled', 'True')
        icp.set_param('x_account_twitter.app_bearer_token', '')
        icp.set_param('x_account_twitter.webhook_base_url', 'https://x.example.com')
        result = self.env['social.account'].sudo()._ensure_x_webhook_subscriptions()
        self.assertEqual(result.get('managed'), 'manual')

    def test_ingest_unknown_event_ignored(self):
        result = self._activity().ingest_webhook(
            _envelope('post.create', 'uuid-post'))
        self.assertEqual(result['status'], 'ignored')
        self.assertEqual(result['reason'], 'unknown_event')

    def test_ingest_subscription_lifecycle_events_not_decrypted(self):
        """Subscription/control notifications are plain control events: they
        must be acknowledged and ignored, never routed into the (encrypted)
        chat/DM pipeline. X's XAA delivers activity events only — subscription
        lifecycle is managed through the REST API, not webhooks — but any
        subscription-* delivery must stay out of the decryptor regardless."""
        for event_type in ('subscription.created', 'subscription.updated',
                           'subscription.deleted'):
            result = self._activity().ingest_webhook(
                _envelope(event_type, 'uuid-%s' % event_type))
            self.assertEqual(result['status'], 'ignored', event_type)
            self.assertEqual(result['reason'], 'unknown_event', event_type)
        count = self.env['x.twitter.event'].sudo().search_count(
            [('event_uuid', 'like', 'uuid-subscription%')])
        self.assertEqual(count, 0)

    def test_ingest_control_notification_acknowledged(self):
        result = self._activity().ingest_webhook({'replay_job_status': {}})
        self.assertEqual(result['status'], 'ignored')
        self.assertEqual(result['reason'], 'control_notification')

    def test_ingest_no_account_ignored(self):
        result = self._activity().ingest_webhook(
            _envelope('dm.received', 'uuid-none', user_id='7777777777777777'))
        self.assertEqual(result['status'], 'ignored')
        self.assertEqual(result['reason'], 'no_account')

    def test_ingest_missing_ids_ignored(self):
        result = self._activity().ingest_webhook({'data': {
            'event_type': 'dm.received', 'payload': {}}})
        self.assertIn(result['status'], ('ignored',))
        result2 = self._activity().ingest_webhook({'data': {
            'event_uuid': 'u1', 'filter': {'user_id': OWNER_ID},
            'payload': {}}})
        self.assertIn(result2['status'], ('ignored',))


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterActivityProcess(XAccountTwitterTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Process Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'owner',
            'twitter_user_id': OWNER_ID,
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_oauth2_access_token': 'fake-at',
            'x_oauth2_refresh_token': 'fake-rt',
        })

    def _process(self, event_type, event_uuid, payload):
        event = self.env['x.twitter.event'].sudo().create({
            'event_uuid': event_uuid,
            'account_id': self.account.id,
            'event_type': event_type,
            'state': 'queued',
            'payload': json.dumps({
                'event_uuid': event_uuid,
                'event_type': event_type,
                'user_id': OWNER_ID,
                'payload': payload,
            }),
        })
        return TwitterActivity(self.env).process_event(event)

    def test_process_dm_inbound_saves_message(self):
        payload = _dm_payload(
            '111', OWNER_ID, 'dm-in-1', 'inbound hello')
        result = self._process('dm.received', 'uuid-in', payload)
        self.assertTrue(result['processed'])
        xm = self.env['x.message'].sudo().search(
            [('external_id', '=', 'dm-in-1')], limit=1)
        self.assertTrue(xm)
        self.assertEqual(xm.direction, 'inbound')
        self.assertEqual(xm.body_plain, 'inbound hello')

    def test_process_dm_outbound_saves_message(self):
        payload = _dm_payload(
            OWNER_ID, '222', 'dm-out-1', 'outbound hi')
        result = self._process('dm.sent', 'uuid-out', payload)
        self.assertTrue(result['processed'])
        xm = self.env['x.message'].sudo().search(
            [('external_id', '=', 'dm-out-1')], limit=1)
        self.assertTrue(xm)
        self.assertEqual(xm.direction, 'outbound')

    def test_process_chat_event_marks_encrypted(self):
        payload = _chat_payload('g111222333', '111', 'chat-1', group=True)
        result = self._process('chat.received', 'uuid-chat', payload)
        self.assertTrue(result['processed'])
        xm = self.env['x.message'].sudo().search(
            [('external_id', '=', 'chat-1')], limit=1)
        self.assertTrue(xm)
        self.assertTrue(xm.encrypted)

    def test_process_chat_event_adds_members_to_channel(self):
        """A webhook chat event must add the owner + counterparty as channel
        members so the conversation surfaces in the right Discuss view."""
        other = '222333444555666777'
        conv_id = '%s-%s' % (min(OWNER_ID, other), max(OWNER_ID, other))
        payload = _chat_payload(conv_id, other, 'chat-mem-1')
        result = self._process('chat.received', 'uuid-mem', payload)
        self.assertTrue(result['processed'])
        channel = self.env['discuss.channel'].sudo().search([
            ('x_conversation_id', '=', conv_id)], limit=1)
        self.assertTrue(channel)
        member_uids = set(channel.channel_member_ids.partner_id.mapped('x_user_id'))
        self.assertIn(OWNER_ID, member_uids)
        self.assertIn(other, member_uids)

    def test_process_chat_event_decrypts_with_key_blob(self):
        """When the account has a Chat key blob, a webhook encoded_event is
        decrypted and stored as plaintext (encrypted=False)."""
        self.account.write({'x_chat_key_blob': 'fake-blob',
                            'x_chat_signing_key_version': '1'})
        payload = _chat_payload('g111222333', '111', 'chat-dec-1')
        fake_chat = Mock()
        fake_chat.decrypt_events.return_value = {
            'messages': [{'event': {'id': 'chat-dec-1', 'type': 'Message',
                                    'sender_id': '111',
                                    'content': {'text': 'decrypted hi'}}}],
            'errors': {},
        }
        try:
            with patch('chat_xdk.Chat', return_value=fake_chat):
                result = self._process('chat.received', 'uuid-dec', payload)
            self.assertTrue(result['processed'])
            self.assertFalse(result.get('encrypted'))
            xm = self.env['x.message'].sudo().search(
                [('external_id', '=', 'chat-dec-1')], limit=1)
            self.assertTrue(xm)
            self.assertFalse(xm.encrypted)
            self.assertEqual(xm.body_plain, 'decrypted hi')
        finally:
            self.account.write({'x_chat_key_blob': False,
                                'x_chat_signing_key_version': False})

    def test_process_chat_event_decrypt_failure_keeps_encrypted(self):
        """Decrypt failure (no key blob) keeps the encrypted marker, no crash."""
        payload = _chat_payload('g111222333', '111', 'chat-nokey-1')
        result = self._process('chat.received', 'uuid-nokey', payload)
        self.assertTrue(result['processed'])
        xm = self.env['x.message'].sudo().search(
            [('external_id', '=', 'chat-nokey-1')], limit=1)
        self.assertTrue(xm)
        self.assertTrue(xm.encrypted)

    def test_process_chat_event_xdk_crypto_error_keeps_encrypted(self):
        """Corrupted ciphertext / failed signature verification: the XDK
        collects per-event crypto failures in ``errors`` instead of raising;
        the message must be stored with the encrypted marker and no body."""
        self.account.write({'x_chat_key_blob': b64encode(bytes(range(64))).decode(),
                            'x_chat_signing_key_version': '1'})
        payload = _chat_payload('g111222333', '111', 'chat-badct-1')
        fake_chat = Mock()
        fake_chat.decrypt_events.return_value = {
            'messages': [],
            'errors': {0: 'signature missing or no matching signing key'},
        }
        try:
            with patch('chat_xdk.Chat', return_value=fake_chat):
                result = self._process('chat.received', 'uuid-badct', payload)
            self.assertTrue(result['processed'])
            self.assertTrue(result['encrypted'])
            xm = self.env['x.message'].sudo().search(
                [('external_id', '=', 'chat-badct-1')], limit=1)
            self.assertTrue(xm)
            self.assertTrue(xm.encrypted)
            self.assertFalse(xm.body_plain)
        finally:
            self.account.write({'x_chat_key_blob': False,
                                'x_chat_signing_key_version': False})

    def test_process_chat_event_uses_only_its_own_account_keys(self):
        """Key resolution must never borrow another X account's key material:
        an event routed to a key-less account stays encrypted even when a
        different account on the same database holds a valid key blob."""
        other = self.env['social.account'].create({
            'name': 'Other Keyed Account',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'keyed',
            'twitter_user_id': '555000555000555000',
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_oauth2_access_token': 'fake-at',
            'x_oauth2_refresh_token': 'fake-rt',
            'x_chat_key_blob': b64encode(bytes(range(64))).decode(),
            'x_chat_signing_key_version': '7',
        })
        payload = _chat_payload('g111222333', '111', 'chat-iso-1')
        with patch('odoo.addons.x_account_twitter.services.xchat_decryptor.'
                   'XChatDecryptor') as dec:
            dec.return_value.available = False
            dec.return_value.decrypt_events.return_value = {
                'messages': [], 'errors': {}}
            result = self._process('chat.received', 'uuid-iso', payload)
        # The decryptor was built for THIS (key-less) account, not `other`.
        ctor_account = dec.call_args.args[1]
        self.assertEqual(ctor_account.id, self.account.id)
        self.assertNotEqual(ctor_account.id, other.id)
        self.assertTrue(result['encrypted'])
        xm = self.env['x.message'].sudo().search(
            [('external_id', '=', 'chat-iso-1')], limit=1)
        self.assertTrue(xm)
        self.assertTrue(xm.encrypted)

    def test_process_event_does_not_duplicate_on_second_run(self):
        payload = _dm_payload('111', OWNER_ID, 'dm-idem-1', 'once')
        event = self.env['x.twitter.event'].sudo().create({
            'event_uuid': 'uuid-idem',
            'account_id': self.account.id,
            'event_type': 'dm.received',
            'state': 'queued',
            'payload': json.dumps({
                'event_uuid': 'uuid-idem',
                'event_type': 'dm.received',
                'user_id': OWNER_ID,
                'payload': payload,
            }),
        })
        first = TwitterActivity(self.env).process_event(event)
        self.assertTrue(first['processed'])
        # Re-processing the SAME event (as a queue retry does) re-runs the
        # handler, which is idempotent by external message id — it must not
        # create a second x.message.
        second = TwitterActivity(self.env).process_event(event)
        self.assertTrue(second['processed'])
        count = self.env['x.message'].sudo().search_count(
            [('external_id', '=', 'dm-idem-1')])
        self.assertEqual(count, 1)

    def test_process_event_retryable_failure_marks_failed(self):
        payload = _dm_payload('111', OWNER_ID, 'dm-fail-1', 'boom')
        event = self.env['x.twitter.event'].sudo().create({
            'event_uuid': 'uuid-fail',
            'account_id': self.account.id,
            'event_type': 'dm.received',
            'state': 'queued',
            'payload': json.dumps({
                'event_uuid': 'uuid-fail',
                'event_type': 'dm.received',
                'user_id': OWNER_ID,
                'payload': payload,
            }),
        })
        with patch.object(TwitterActivity, '_handle_dm',
                          side_effect=twitter_errors.TwitterTemporaryError('rate')):
            with self.assertRaises(twitter_errors.TwitterTemporaryError):
                TwitterActivity(self.env).process_event(event)

    def test_process_event_non_retryable_error_marks_done(self):
        payload = _dm_payload('111', OWNER_ID, 'dm-fail-2', 'boom')
        event = self.env['x.twitter.event'].sudo().create({
            'event_uuid': 'uuid-fail2',
            'account_id': self.account.id,
            'event_type': 'dm.received',
            'state': 'queued',
            'payload': json.dumps({
                'event_uuid': 'uuid-fail2',
                'event_type': 'dm.received',
                'user_id': OWNER_ID,
                'payload': payload,
            }),
        })
        with patch.object(TwitterActivity, '_handle_dm',
                          side_effect=ValueError('bad payload')):
            # Non-retryable errors are swallowed and marked done-with-error.
            result = TwitterActivity(self.env).process_event(event)
        self.assertFalse(result.get('processed'))
        self.assertTrue(result.get('error'))
        self.assertEqual(event.state, 'done')


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestXChatKeyModes(XAccountTwitterTestBase):
    """XChatDecryptor honors the account's key mode (key_blob / juicebox)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Key Mode Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'owner',
            'twitter_user_id': OWNER_ID,
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_oauth2_access_token': 'fake-at',
            'x_oauth2_refresh_token': 'fake-rt',
        })

    def _decryptor(self, juicebox=False, key_version='1'):
        """Build a decryptor with a fake API client returning a public-key
        record (optionally carrying a juicebox_config for PIN recovery)."""
        client = Mock()
        record = {
            'user_id': OWNER_ID,
            'public_key_version': key_version,
            'public_key': 'identity-pub',
            'signing_public_key': 'signing-pub',
            'identity_public_key_signature': 'sig',
        }
        if juicebox:
            record['juicebox_config'] = {'realm_id': 'test-realm',
                                         'juicebox_service_url': 'https://j.example'}
        client.request.return_value = {'data': [record]}
        return XChatDecryptor(self.env, self.account, client=client)

    def test_available_key_blob_mode_requires_blob(self):
        self.account.write({'x_chat_key_mode': 'key_blob',
                            'x_chat_key_blob': ''})
        self.assertFalse(XChatDecryptor(self.env, self.account).available)
        self.account.write({'x_chat_key_blob': 'blob-xyz'})
        self.assertTrue(XChatDecryptor(self.env, self.account).available)

    def test_available_juicebox_mode_requires_pin(self):
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': ''})
        self.assertFalse(XChatDecryptor(self.env, self.account).available)
        self.account.write({'x_encryption_code': '1234'})
        self.assertTrue(XChatDecryptor(self.env, self.account).available)
        self.account.write({'x_encryption_code': False})

    def test_initialize_juicebox_uses_unlock(self):
        """juicebox mode must build Chat with the account's juicebox_config and
        recover keys via Chat.unlock(pin), not import a stored blob — no private
        key blob is stored server-side."""
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': '9999',
                            'x_chat_key_blob': False})
        fake_chat = Mock()
        with patch('chat_xdk.Chat', return_value=fake_chat) as chat_ctor:
            returned = self._decryptor(juicebox=True).initialize()
        # Chat must be constructed with the juicebox_config JSON (required for
        # PIN recovery), not the empty manual-management form.
        chat_ctor.assert_called()
        self.assertTrue(chat_ctor.call_args.args
                        and 'juicebox_service_url' in chat_ctor.call_args.args[0])
        fake_chat.unlock.assert_called_once_with('9999')
        fake_chat.import_keys.assert_not_called()
        fake_chat.set_identity.assert_called_once_with(OWNER_ID, '1')
        self.assertIs(returned, fake_chat)

    def test_initialize_juicebox_without_config_raises(self):
        """Without a juicebox_config (account never backed up keys), PIN unlock
        is impossible and must fail fast with a clear message."""
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': '9999',
                            'x_chat_key_blob': False})
        fake_chat = Mock()
        with patch('chat_xdk.Chat', return_value=fake_chat):
            with self.assertRaises(ValueError):
                self._decryptor(juicebox=False).initialize()

    def test_public_keys_fetch_uses_public_key_fields_param(self):
        """The public-keys request must pass the API's ``public_key.fields``
        param (not ``public_key_fields``) or X returns a 400 and no config is
        ever fetched, breaking PIN unlock."""
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': '9999',
                            'x_chat_key_blob': False})
        dec = self._decryptor(juicebox=True)
        fake_chat = Mock()
        with patch('chat_xdk.Chat', return_value=fake_chat):
            dec.initialize()
        req = dec.client.request
        req.assert_called_once()
        args, kwargs = req.call_args
        self.assertIn('public_keys', args[1])
        self.assertEqual(kwargs['params']['public_key.fields'],
                         'public_key_version,public_key,signing_public_key,'
                         'identity_public_key_signature,juicebox_config')

    def test_initialize_key_blob_mode_uses_import(self):
        """key_blob mode must decode the stored Text blob to raw bytes before
        import_keys — the native XDK rejects str with
        ``TypeError: argument 'keys': 'str' object cannot be cast as bytes``."""
        raw = bytes(range(64))
        self.account.write({'x_chat_key_mode': 'key_blob',
                            'x_chat_key_blob': b64encode(raw).decode(),
                            'x_chat_signing_key_version': '3'})
        fake_chat = Mock()
        with patch('chat_xdk.Chat', return_value=fake_chat):
            XChatDecryptor(self.env, self.account).initialize()
        fake_chat.import_keys.assert_called_once_with(raw, version='3')
        fake_chat.unlock.assert_not_called()
        fake_chat.set_identity.assert_called_once_with(OWNER_ID, '3')

    def test_initialize_juicebox_missing_pin_raises(self):
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': False})
        with self.assertRaises(ValueError):
            self._decryptor(juicebox=True).initialize()

    def test_initialize_juicebox_retries_transient_then_succeeds(self):
        """A transient Juicebox error must be retried, not surfaced immediately."""
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': '1234'})
        fake_chat = Mock()
        fake_chat.unlock.side_effect = [
            Exception('Juicebox error: Transient error - retry'),
            None,
        ]
        with patch('chat_xdk.Chat', return_value=fake_chat):
            with patch('odoo.addons.x_account_twitter.services.xchat_decryptor.time.sleep'):
                self._decryptor(juicebox=True).initialize()
        # _unlock_with_retry retried the transient error, then succeeded.
        self.assertEqual(fake_chat.unlock.call_count, 2)
        fake_chat.set_identity.assert_called_once_with(OWNER_ID, '1')

    def test_retry_gives_up_after_final_transient(self):
        """Persistent transient errors must eventually re-raise."""
        fake_chat = Mock()
        fake_chat.unlock.side_effect = [
            Exception('Juicebox error: Transient error - retry')] * 4 + [None]
        with patch('odoo.addons.x_account_twitter.services.xchat_decryptor.time.sleep'):
            with self.assertRaises(Exception) as ctx:
                XChatDecryptor._unlock_with_retry(fake_chat, '1234', tries=4,
                                                  base_delay=0.01)
        self.assertIn('Transient error - retry', str(ctx.exception))
        self.assertEqual(fake_chat.unlock.call_count, 4)

    def test_retry_does_not_retry_non_transient_error(self):
        """Wrong-PIN / non-transient errors must raise immediately, no retry."""
        fake_chat = Mock()
        fake_chat.unlock.side_effect = Exception('Incorrect PIN')
        with self.assertRaises(Exception) as ctx:
            XChatDecryptor._unlock_with_retry(fake_chat, 'bad', tries=4,
                                              base_delay=0.01)
        self.assertIn('Incorrect PIN', str(ctx.exception))
        self.assertEqual(fake_chat.unlock.call_count, 1)

    def test_initialize_key_blob_missing_blob_raises(self):
        self.account.write({'x_chat_key_mode': 'key_blob',
                            'x_chat_key_blob': False})
        with self.assertRaises(ValueError):
            XChatDecryptor(self.env, self.account).initialize()

    def test_decrypt_events_fetches_foreign_sender_signing_key(self):
        """A message from a *different* X user (group chat) must pull that
        sender's public keys into the signing-key store so the SDK can verify
        the sender's message signature. Regression: only the account's own
        signing key was passed, so foreign senders failed verification."""
        self.account.write({'x_chat_key_mode': 'key_blob',
                            'x_chat_key_blob': 'blob-raw',
                            'x_chat_signing_key_version': '1'})
        foreign_sender = '555666777888999000'
        client = Mock()
        # First call: the account's own public-key record.
        own = {'user_id': OWNER_ID, 'public_key_version': '1',
               'public_key': 'own-identity', 'signing_public_key': 'own-signing',
               'identity_public_key_signature': 'own-sig'}
        foreign = {'user_id': foreign_sender, 'public_key_version': '1',
                   'public_key': 'foreign-identity',
                   'signing_public_key': 'foreign-signing',
                   'identity_public_key_signature': 'foreign-sig'}
        client.request.side_effect = [{'data': [own]}, {'data': [foreign]}]
        fake_chat = Mock()
        fake_chat.decrypt_events.return_value = {
            'messages': [{'event': {'id': 'gm1', 'type': 'Message',
                                    'sender_id': foreign_sender,
                                    'content': {'text': 'group hi'}}}],
            'errors': {},
        }
        with patch('chat_xdk.Chat', return_value=fake_chat):
            dec = XChatDecryptor(self.env, self.account, client=client)
            result = dec.decrypt_events(['blob-1'], sender_ids=[foreign_sender])
        # The SDK must receive both the account's key AND the foreign sender's.
        _, kwargs = fake_chat.decrypt_events.call_args
        received_keys = kwargs['signing_keys'] if 'signing_keys' in kwargs \
            else fake_chat.decrypt_events.call_args.args[1]
        user_ids = {entry['user_id'] for entry in received_keys}
        self.assertIn(str(OWNER_ID), user_ids)
        self.assertIn(foreign_sender, user_ids)
        self.assertIn('group hi',
                      result['messages'][0]['event']['content']['text'])

    def test_decrypt_events_retains_all_sender_key_versions(self):
        """A webhook can be signed by a non-first key version after rotation.

        Regression: key resolution retained only ``data[0]`` from
        ``/public_keys``; Chat XDK consequently reported no matching signing
        key for deliveries carrying another public_key_version.
        """
        self.account.write({'x_chat_key_mode': 'key_blob',
                            'x_chat_key_blob': 'blob-raw',
                            'x_chat_signing_key_version': '1'})
        sender = '555666777888999000'
        own = {'user_id': OWNER_ID, 'public_key_version': '1',
               'public_key': 'own-identity', 'signing_public_key': 'own-signing',
               'identity_public_key_signature': 'own-sig'}
        sender_v1 = {'user_id': sender, 'public_key_version': '1',
                     'public_key': 'sender-identity-1',
                     'signing_public_key': 'sender-signing-1',
                     'identity_public_key_signature': 'sender-sig-1'}
        sender_v2 = {'user_id': sender, 'public_key_version': '2',
                     'public_key': 'sender-identity-2',
                     'signing_public_key': 'sender-signing-2',
                     'identity_public_key_signature': 'sender-sig-2'}
        client = Mock()
        client.request.side_effect = [{'data': [own]},
                                      {'data': [sender_v1, sender_v2]}]
        fake_chat = Mock()
        fake_chat.decrypt_events.return_value = {'messages': [], 'errors': {}}
        with patch('chat_xdk.Chat', return_value=fake_chat):
            XChatDecryptor(self.env, self.account, client=client).decrypt_events(
                ['blob-1'], sender_ids=[sender])
        signing_keys = fake_chat.decrypt_events.call_args.args[1]
        self.assertEqual(
            {key['public_key_version'] for key in signing_keys
             if key['user_id'] == sender}, {'1', '2'})

    def test_key_change_cache_keeps_version_to_raw_key_mapping(self):
        """The single-event XDK API accepts {version: raw_key}, not its inverse."""
        decryptor = XChatDecryptor(self.env, self.account)
        fake_chat = Mock()
        raw_key = b'k' * 32
        fake_chat.extract_conversation_keys.return_value = {
            'keys': {'rotation-2': raw_key}, 'latest_version': 'rotation-2'}
        decryptor._absorb_key_changes(fake_chat, [], ['key-change-event'])
        self.assertEqual(decryptor._conversation_keys, {'rotation-2': raw_key})

    def test_decode_key_blob_storage_variants(self):
        """Every documented storage encoding must decode to the same raw key
        bytes: base64 (canonical), line-wrapped base64, hex, URL-safe base64
        and a Python bytes/bytearray repr."""
        raw = bytes(range(64))
        variants = {
            'base64': b64encode(raw).decode(),
            'base64-wrapped': b64encode(raw).decode()[:40] + '\n'
                              + b64encode(raw).decode()[40:],
            'hex': raw.hex(),
            'urlsafe': b64encode(raw, altchars=b'-_').decode().rstrip('='),
            'repr-str': str(bytearray(raw)),
            'repr-bytes': repr(bytes(raw)),
        }
        for label, stored in variants.items():
            self.assertEqual(
                XChatDecryptor._decode_key_blob(stored), raw,
                'storage variant %r did not round-trip' % label)

    def test_decode_key_blob_empty_raises(self):
        with self.assertRaises(ValueError):
            XChatDecryptor._decode_key_blob('')
        with self.assertRaises(ValueError):
            XChatDecryptor._decode_key_blob('   ')

    def test_real_xdk_export_import_roundtrip(self):
        """Proves the str->bytes fix against the real native chatxdk package
        (a declared dependency of the module): export_keys() -> store
        base64 on the account -> initialize() -> import_keys accepts the
        decoded bytes and the identity key is usable. Before the fix this
        raised TypeError on every key_blob-mode decryption."""
        try:
            from chat_xdk import Chat as RealChat
        except ImportError:
            self.skipTest('chatxdk native package not installed')
        seed = RealChat()
        payload = seed.generate_keypairs()
        version = str(payload.version)
        seed.set_identity('1', version)
        blob = bytes(seed.export_keys())
        self.account.write({'x_chat_key_mode': 'key_blob',
                            'x_chat_key_blob': b64encode(blob).decode(),
                            'x_chat_signing_key_version': version})
        client = Mock()
        client.request.return_value = {'data': []}
        decryptor = XChatDecryptor(self.env, self.account, client=client)
        chat = decryptor.initialize()
        self.assertTrue(chat.has_identity_key())
        chat.set_identity(str(self.account.twitter_user_id), version)
        self.assertEqual(chat.get_public_key_fingerprint(),
                         seed.get_public_key_fingerprint())

    def test_invalid_pin_sets_lock_flag(self):
        """An Invalid PIN rejection must stamp x_chat_pin_locked so further
        attempts short-circuit without hitting X (each wrong attempt consumes
        one of the limited guesses before the secure backup is permanently
        locked).

        Uses a plain try/except instead of ``assertRaises``: Odoo's
        ``_assertRaises`` wraps the block in a savepoint that is rolled back
        when the expected exception fires, which would undo the status write
        this test verifies.
        """
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': 'wrong-pin'})
        fake_chat = Mock()
        fake_chat.unlock.side_effect = Exception(
            'Juicebox error: Invalid PIN: guesses_remaining=0')
        caught = None
        with patch('chat_xdk.Chat', return_value=fake_chat):
            try:
                self._decryptor(juicebox=True).initialize()
            except Exception as exc:
                caught = exc
        self.assertIsNotNone(caught)
        self.assertIn('Invalid PIN', str(caught))
        self.assertTrue(self.account.x_chat_pin_locked)

    def test_pin_locked_short_circuits_without_chat_call(self):
        """Once locked, initialize must raise without calling Chat.unlock —
        prevents further guess consumption on X."""
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': 'any-pin',
                            'x_chat_pin_locked': True})
        fake_chat = Mock()
        with patch('chat_xdk.Chat', return_value=fake_chat):
            with self.assertRaises(ValueError) as ctx:
                self._decryptor(juicebox=True).initialize()
        self.assertIn('PIN previously rejected', str(ctx.exception))
        fake_chat.unlock.assert_not_called()

    def test_pin_change_clears_lock_flag(self):
        """Changing the PIN must clear the lock flag so the operator can retry
        with a corrected code."""
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': 'wrong',
                            'x_chat_pin_locked': True})
        self.assertTrue(self.account.x_chat_pin_locked)
        self.account.write({'x_encryption_code': 'new-pin'})
        self.assertFalse(self.account.x_chat_pin_locked)

    def test_available_false_when_pin_locked(self):
        """The automatic path (per-event decryption) must skip the account
        entirely when the PIN is locked — no X calls, no queued errors."""
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': '1234',
                            'x_chat_pin_locked': False})
        self.assertTrue(XChatDecryptor(self.env, self.account).available)
        self.account.write({'x_chat_pin_locked': True})
        self.assertFalse(XChatDecryptor(self.env, self.account).available)

    def test_successful_unlock_clears_lock_flag(self):
        """A successful Juicebox unlock must ensure x_chat_pin_locked is False.
        If the lock was previously set (e.g. from a transient error that was
        misidentified), a successful unlock clears it."""
        self.account.write({'x_chat_key_mode': 'juicebox',
                            'x_encryption_code': 'correct-pin',
                            'x_chat_pin_locked': False})
        self.assertFalse(self.account.x_chat_pin_locked)
        fake_chat = Mock()
        fake_chat.unlock.return_value = None
        fake_chat.set_identity = Mock()
        fake_chat.set_cache_keys = Mock()
        with patch('chat_xdk.Chat', return_value=fake_chat):
            decryptor = self._decryptor(juicebox=True)
            decryptor._public_keys_cache = {'juicebox_config': {'some': 'config'}}
            decryptor.initialize()
        self.assertFalse(self.account.x_chat_pin_locked)
        fake_chat.unlock.assert_called_once_with('correct-pin')


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterBackslashGuard(XAccountTwitterTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Backslash Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'owner',
            'twitter_user_id': OWNER_ID,
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_oauth2_access_token': 'fake-at',
            'x_oauth2_refresh_token': 'fake-rt',
        })

    def _write_event(self, event_type, event_uuid, text, message_id):
        payload = _dm_payload('111', OWNER_ID, message_id, text)
        event = self.env['x.twitter.event'].sudo().create({
            'event_uuid': event_uuid,
            'account_id': self.account.id,
            'event_type': event_type,
            'state': 'queued',
            'payload': json.dumps({
                'event_uuid': event_uuid,
                'event_type': event_type,
                'user_id': OWNER_ID,
                'payload': payload,
            }),
        })
        return TwitterActivity(self.env).process_event(event)

    def test_pure_backslash_artifact_skipped(self):
        """A DM whose text is only backslashes (escaping/payload artifact) must
        not be surfaced as a message body."""
        run = '\\\\' * 100
        self._write_event('dm.received', 'uuid-slash', run, 'dm-slash-1')
        count = self.env['x.message'].sudo().search_count(
            [('external_id', '=', 'dm-slash-1')])
        self.assertEqual(count, 0)

    def test_normal_dm_still_saved(self):
        self._write_event('dm.received', 'uuid-ok', 'normal hello', 'dm-ok-1')
        xm = self.env['x.message'].sudo().search(
            [('external_id', '=', 'dm-ok-1')], limit=1)
        self.assertTrue(xm)
        self.assertEqual(xm.body_plain, 'normal hello')


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterSubscriptionAuto(XAccountTwitterTestBase):
    """Programmatic per-account subscription creation after linking."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        from datetime import timedelta
        from odoo import fields
        cls.account = cls.env['social.account'].create({
            'name': 'Auto Sub Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'customer',
            'twitter_user_id': OWNER_ID,
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_oauth2_access_token': 'fake-at',
            'x_oauth2_refresh_token': 'fake-rt',
            'x_oauth2_token_expires_at': fields.Datetime.now() + timedelta(hours=1),
        })

    def test_subscribe_account_creates_missing_subscriptions(self):
        hook = self.env['x.twitter.webhook'].sudo().create({
            'name': 'https://x.example.com/x_account/twitter/webhook',
            'webhook_id': 'wh-sub-auto',
            'valid': True,
        })
        provider = TwitterProvider(self.env, self.account)
        # X returns a distinct subscription id per (account, event type); the
        # create path enforces UNIQUE(subscription_id) so each call must differ.
        counter = {'n': 0}

        def _json():
            counter['n'] += 1
            return {'data': {'subscription_id': 'sub-new-%d' % counter['n']}}

        with patch('odoo.addons.x_account_twitter.services.twitter_webhook.requests.request',
                   return_value=Mock(ok=True, status_code=200, content=b'{}',
                                     json=_json)):
            summary = provider.subscribe_account(self.account)
        self.assertEqual(summary['created'], 2)
        self.assertEqual(summary['existing'], 0)
        subs = self.env['x.twitter.subscription'].sudo().search(
            [('account_id', '=', self.account.id)])
        self.assertEqual(len(subs), 2)
        self.assertTrue(all(s.state == 'active' for s in subs))

    def test_subscribe_account_skips_existing(self):
        hook = self.env['x.twitter.webhook'].sudo().create({
            'name': 'https://x.example.com/x_account/twitter/webhook',
            'webhook_id': 'wh-sub-ex',
            'valid': True,
        })
        provider = TwitterProvider(self.env, self.account)
        counter = {'n': 0}

        def _json():
            counter['n'] += 1
            return {'data': {'subscription_id': 'sub-a-%d' % counter['n']}}

        with patch('odoo.addons.x_account_twitter.services.twitter_webhook.requests.request',
                   return_value=Mock(ok=True, status_code=200, content=b'{}',
                                     json=_json)):
            provider.subscribe_account(self.account)
            summary2 = provider.subscribe_account(self.account)
        self.assertEqual(summary2['created'], 0)
        self.assertEqual(summary2['existing'], 2)

    def test_subscribe_account_rejection_does_not_abort_remaining(self):
        """Regression: X rejecting one event type (e.g. dm.received for a user)
        must not abort creation of the other subscriptions — previously the
        raise left the account with zero live subscriptions."""
        hook = self.env['x.twitter.webhook'].sudo().create({
            'name': 'https://x.example.com/x_account/twitter/webhook',
            'webhook_id': 'wh-sub-fail',
            'valid': True,
        })
        provider = TwitterProvider(self.env, self.account)
        calls = {'n': 0}

        def _fake_request(method, url, **kwargs):
            body = kwargs.get('json') or {}
            calls['n'] += 1
            # The first event type in SUPPORTED_EVENT_TYPES is dm.received;
            # reject it exactly like X does for account 72, then let the rest
            # through.
            if body.get('event_type') == 'dm.received':
                response = Mock(ok=False, status_code=400, content=b'{}')
                response.json.return_value = {
                    'title': 'Invalid Request',
                    'status': 400,
                    'detail': 'One or more parameters to your request was invalid.',
                }
                return response
            return Mock(ok=True, status_code=200, content=b'{}',
                        json=lambda: {'data': {
                            'subscription_id': 'sub-ok-%d' % calls['n']}})

        with patch('odoo.addons.x_account_twitter.services.twitter_webhook.requests.request',
                   side_effect=_fake_request):
            summary = provider.subscribe_account(self.account)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(summary['failed'], 1)
        subs = self.env['x.twitter.subscription'].sudo().search(
            [('account_id', '=', self.account.id)])
        self.assertEqual(len(subs), 2)
        self.assertEqual(
            subs.filtered(lambda s: s.state == 'failed').event_type,
            'dm.received')
        self.assertEqual(
            len(subs.filtered(lambda s: s.state == 'active')), 1)

    def test_account_ensure_subscriptions_dispatches(self):
        with patch.object(TwitterProvider, 'subscribe_account',
                           return_value={'account_id': self.account.id,
                                         'created': 2}) as mocked:
            result = self.account._ensure_x_account_subscriptions()
        mocked.assert_called_once()
        self.assertEqual(result['created'], 2)


@tagged('post_install', '-at_install', 'x_account_twitter')
class TestTwitterOAuthHeaderFallback(XAccountTwitterTestBase):
    """Regression: an OAuth 2.0 account whose refresh token is dead must raise
    a typed authentication error instead of falling through to social_twitter's
    OAuth 1.0a signing with ``twitter_oauth_token_secret=False`` (which crashed
    with ``TypeError: sequence item 1: expected str instance, bool found`` —
    the RPC_ERROR seen on fetch-group-messages and inside every XChat
    public-key fetch)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.twitter_media = cls.env.ref('social_twitter.social_media_twitter')
        cls.account = cls.env['social.account'].create({
            'name': 'Reauth Account',
            'media_id': cls.twitter_media.id,
            'social_account_handle': 'reauth',
            'twitter_user_id': OWNER_ID,
            'x_provider': 'twitter',
            'x_auth_method': 'oauth2',
            'x_oauth2_access_token': 'stale-at',
            'x_oauth2_refresh_token': 'revoked-rt',
        })

    @classmethod
    def tearDownClass(cls):
        # Reset the config parameters so later suites in the same test DB do
        # not suddenly take the real-network OAuth refresh / signing paths.
        icp = cls.env['ir.config_parameter'].sudo()
        for param in ('social.twitter_oauth2_client_id',
                      'social.twitter_oauth2_client_secret',
                      'social.twitter_consumer_key',
                      'social.twitter_consumer_secret_key'):
            icp.set_param(param, '')
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('social.twitter_oauth2_client_id', 'test-client-id')
        icp.set_param('social.twitter_oauth2_client_secret', 'test-client-secret')
        # Force the expired-token branch (expires_at unset -> refresh).
        self.account.write({'x_oauth2_token_expires_at': False})

    def test_oauth2_dead_refresh_raises_typed_error(self):
        """A revoked refresh token (generic token-endpoint error) makes
        ``_x_oauth2_ensure_access_token`` return None; the header helper must
        raise TwitterAuthenticationError, not fall into OAuth 1.0a signing."""
        from datetime import timedelta
        from odoo import fields
        self.account.write({'x_oauth2_token_expires_at':
                            fields.Datetime.now() - timedelta(hours=1)})
        with patch('odoo.addons.x_account_twitter.models.social_account.'
                   'TwitterOAuth2Client.refresh',
                   side_effect=twitter_errors.TwitterError('http_400')):
            with self.assertRaises(
                    twitter_errors.TwitterAuthenticationError) as ctx:
                self.account._get_twitter_oauth_header(
                    'https://api.x.com/2/users/me')
        self.assertEqual(ctx.exception.code, 'authentication_failure')
        self.assertEqual(str(ctx.exception), 'oauth2_access_token_unavailable')

    def test_oauth2_dead_refresh_marks_account_reauth_required(self):
        """Going through the header helper, a dead refresh token both marks
        the account ``reauth_required`` and raises.

        Deliberately uses a plain try/except instead of ``assertRaises``:
        Odoo's ``_assertRaises`` wraps the block in a savepoint that is rolled
        back when the expected exception fires, which would undo the status
        write this test verifies.
        """
        from datetime import timedelta
        from odoo import fields
        self.account.write({'x_oauth2_token_expires_at':
                            fields.Datetime.now() - timedelta(hours=1)})
        caught = None
        with patch('odoo.addons.x_account_twitter.models.social_account.'
                   'TwitterOAuth2Client.refresh',
                   side_effect=twitter_errors.TwitterError('http_400')):
            try:
                self.account._get_twitter_oauth_header(
                    'https://api.x.com/2/users/me')
            except twitter_errors.TwitterAuthenticationError as exc:
                caught = exc
        self.assertIsNotNone(caught)
        self.assertEqual(self.account.x_connection_status, 'reauth_required')
        self.assertEqual(self.account.last_error, 'http_400')

    def test_oauth1_credentials_still_use_legacy_signing(self):
        """Accounts that do carry OAuth 1.0a credentials keep the legacy
        social_twitter signing path (locally signed when the consumer key
        and secret are configured)."""
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('social.twitter_consumer_key', 'test-consumer-key')
        icp.set_param('social.twitter_consumer_secret_key',
                      'test-consumer-secret')
        account = self.env['social.account'].create({
            'name': 'OAuth1 Account',
            'media_id': self.twitter_media.id,
            'social_account_handle': 'legacy',
            'twitter_user_id': '1234567890123456789',
            'twitter_oauth_token': 'legacy-token',
            'twitter_oauth_token_secret': 'legacy-secret',
        })
        header = account._get_twitter_oauth_header(
            'https://api.x.com/2/users/me', method='GET')
        self.assertIn('Authorization', header)
        self.assertTrue(header['Authorization'].startswith('OAuth '))
