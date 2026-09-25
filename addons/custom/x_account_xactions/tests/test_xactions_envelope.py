from odoo.tests import tagged

from odoo.addons.x_account_xactions.services.xactions_envelope import (
    XActionsEnvelopeParser,
)

from .common import REPORT_PAYLOAD, XAccountXActionsTestBase


@tagged('post_install', '-at_install', 'x_account_xactions')
class TestXActionsEnvelope(XAccountXActionsTestBase):

    def test_person_accepts_user_id_or_id(self):
        self.assertEqual(
            XActionsEnvelopeParser.person({'userId': '7', 'username': 'a'})['id'],
            '7')
        self.assertEqual(
            XActionsEnvelopeParser.person({'id': '8', 'name': 'B'})['id'], '8')
        self.assertEqual(XActionsEnvelopeParser.person({}), {})

    def test_person_as_comment_uses_person_id(self):
        comment = XActionsEnvelopeParser.person_as_comment(
            {'userId': '88', 'username': 'carol', 'name': 'Carol'})
        self.assertEqual(comment['id'], '88')
        self.assertEqual(comment['author_id'], '88')
        self.assertEqual(comment['author_username'], 'carol')

    def test_post_maps_metrics_and_author(self):
        post = XActionsEnvelopeParser.post(REPORT_PAYLOAD['posts'][0])
        self.assertEqual(post['id'], '111')
        self.assertEqual(post['text'], 'Hello X')
        self.assertEqual(post['author_id'], '42')
        self.assertEqual(post['author_username'], 'xactions_user')
        self.assertEqual(post['favorite_count'], 7)
        self.assertEqual(post['retweet_count'], 3)
        self.assertEqual(post['reply_count'], 2)
        self.assertEqual(post['quote_count'], 1)
        self.assertEqual(post['created_at'], '2026-01-01T10:00:00.000Z')

    def test_post_inline_audience(self):
        post = XActionsEnvelopeParser.post(REPORT_PAYLOAD['posts'][0])
        audience = post['audience']
        self.assertEqual(audience['likers'][0]['id'], '77')
        self.assertEqual(audience['likers'][0]['username'], 'bob')
        self.assertEqual(audience['commenters'][0]['author_id'], '88')
        self.assertEqual(audience['retweeters'][0]['id'], '99')

    def test_post_without_audience_omits_key(self):
        post = XActionsEnvelopeParser.post({'id': '1', 'text': 'x'})
        self.assertNotIn('audience', post)

    def test_report_flattens_posts(self):
        result = XActionsEnvelopeParser.report(REPORT_PAYLOAD)
        self.assertEqual(len(result['posts']), 1)
        self.assertFalse(result['truncated'])
        self.assertEqual(result['stats'], {'posts': 1})

    def test_commenters(self):
        result = XActionsEnvelopeParser.commenters({
            'commenters': [{'userId': '5', 'username': 'eve', 'name': 'Eve'}],
            'hasMore': True,
        })
        self.assertEqual(result['comments'][0]['author_username'], 'eve')
        self.assertTrue(result['has_more'])
