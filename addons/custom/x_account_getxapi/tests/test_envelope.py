from odoo.tests import tagged

from odoo.addons.x_account_getxapi.services.getxapi_envelope import GetXAPIEnvelopeParser

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIEnvelope(XAccountGetXAPITestBase):

    def test_parse_user(self):
        envelope = {
            'data': {
                'rest_id': '12345',
                'legacy': {
                    'screen_name': 'testuser',
                    'name': 'Test User',
                    'description': 'A test user',
                    'followers_count': 100,
                    'friends_count': 50,
                    'statuses_count': 500,
                    'verified': False,
                },
                'is_blue_verified': True,
            }
        }
        user = GetXAPIEnvelopeParser.user(envelope)
        self.assertEqual(user['id'], '12345')
        self.assertEqual(user['username'], 'testuser')
        self.assertEqual(user['name'], 'Test User')
        self.assertEqual(user['followers_count'], 100)
        self.assertTrue(user['is_blue_verified'])

    def test_parse_user_empty(self):
        user = GetXAPIEnvelopeParser.user({})
        self.assertEqual(user, {})

    def test_parse_tweet(self):
        envelope = {
            'data': {
                'rest_id': '999',
                'legacy': {
                    'full_text': 'Hello world',
                    'user_id_str': '12345',
                    'created_at': '2026-01-01T00:00:00Z',
                    'favorite_count': 10,
                    'retweet_count': 5,
                }
            }
        }
        tweet = GetXAPIEnvelopeParser.tweet(envelope)
        self.assertEqual(tweet['id'], '999')
        self.assertEqual(tweet['text'], 'Hello world')
        self.assertEqual(tweet['author_id'], '12345')
        self.assertEqual(tweet['favorite_count'], 10)

    def test_parse_tweet_empty(self):
        tweet = GetXAPIEnvelopeParser.tweet({})
        self.assertEqual(tweet, {})

    def test_parse_search_results(self):
        envelope = {
            'data': {
                'entries': [
                    {'rest_id': '1', 'legacy': {'full_text': 'tweet 1'}},
                    {'rest_id': '2', 'legacy': {'full_text': 'tweet 2'}},
                ],
                'has_more': True,
                'next_cursor': {'cursor_id': 'abc'},
            }
        }
        result = GetXAPIEnvelopeParser.search_results(envelope)
        self.assertEqual(len(result['tweets']), 2)
        self.assertTrue(result['has_more'])
        self.assertEqual(result['cursor'], 'abc')

    def test_parse_thread(self):
        envelope = {
            'data': {
                'entries': [
                    {'rest_id': '1', 'legacy': {'full_text': 'tweet 1'}},
                    {'rest_id': '2', 'legacy': {'full_text': 'tweet 2'}},
                ],
            }
        }
        result = GetXAPIEnvelopeParser.thread(envelope)
        self.assertEqual(len(result['tweets']), 2)

    def test_parse_dm_conversations(self):
        envelope = {
            'data': {
                'conversations': [
                    {'conversation_id': 'conv-1', 'type': 'one_to_one',
                     'participants': [{'id': '9'}], 'participant_count': 1},
                    {'conversation_id': 'conv-2', 'type': 'group',
                     'participants': [{'id': '1'}, {'id': '2'}],
                     'participant_count': 2},
                ],
                'next_cursor': {'cursor_id': 'abc'},
            }
        }
        result = GetXAPIEnvelopeParser.dm_conversations(envelope)
        self.assertEqual(len(result['conversations']), 2)
        self.assertEqual(result['conversations'][0]['conversation_id'], 'conv-1')
        self.assertFalse(result['conversations'][0]['group'])
        self.assertTrue(result['conversations'][1]['group'])

    def test_parse_dm_messages(self):
        envelope = {
            'data': {
                'messages': [
                    {'id': 'm1', 'text': 'hello', 'sender_id': '9',
                     'created_at': '2026-01-01T00:00:00Z'},
                ],
                'next_cursor': 'cur1',
            }
        }
        result = GetXAPIEnvelopeParser.dm_messages(envelope, 'conv-1')
        self.assertEqual(len(result['messages']), 1)
        self.assertEqual(result['messages'][0]['id'], 'm1')
        self.assertEqual(result['messages'][0]['conversation_id'], 'conv-1')

    def test_parse_followers(self):
        envelope = {
            'data': {
                'entries': [
                    {'rest_id': '100', 'legacy': {'screen_name': 'alice', 'name': 'Alice'}},
                    {'rest_id': '200', 'legacy': {'screen_name': 'bob', 'name': 'Bob'}},
                ],
                'has_more': False,
            }
        }
        result = GetXAPIEnvelopeParser.followers(envelope)
        self.assertEqual(len(result['users']), 2)
        self.assertEqual(result['users'][0]['username'], 'alice')

    def test_parse_follow_result(self):
        envelope = {'data': {'result': {'following': True, 'user_id': '123'}}}
        result = GetXAPIEnvelopeParser.follow_result(envelope)
        self.assertTrue(result['success'])
        self.assertEqual(result['operation'], 'follow')

    def test_parse_retweet_result(self):
        envelope = {'data': {'result': {'retweeted': True}}}
        result = GetXAPIEnvelopeParser.retweet_result(envelope, '456')
        self.assertTrue(result['success'])
        self.assertEqual(result['post_id'], '456')
        self.assertEqual(result['operation'], 'repost')

    def test_parse_like_result(self):
        envelope = {'data': {'result': {'favorited': True}}}
        result = GetXAPIEnvelopeParser.like_result(envelope, '789')
        self.assertTrue(result['success'])
        self.assertEqual(result['post_id'], '789')
        self.assertEqual(result['operation'], 'like')

    def test_parse_create_tweet_result(self):
        envelope = {'data': {'result': {'rest_id': '111', 'text': 'new tweet'}}}
        result = GetXAPIEnvelopeParser.create_tweet_result(envelope)
        self.assertTrue(result['success'])
        self.assertEqual(result['tweet_id'], '111')

    def test_parse_media_upload_result(self):
        envelope = {'data': {'result': {'media_id': 'media-123'}}}
        result = GetXAPIEnvelopeParser.media_upload_result(envelope)
        self.assertTrue(result['success'])
        self.assertEqual(result['media_id'], 'media-123')
