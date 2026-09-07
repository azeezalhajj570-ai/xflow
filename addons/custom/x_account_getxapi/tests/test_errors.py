from odoo.tests import tagged

from odoo.addons.x_account_getxapi.services.getxapi_errors import (
    GetXAPIError,
    GetXAPIAuthenticationError,
    GetXAPIRateLimitError,
    GetXAPINotFoundError,
    GetXAPITemporaryError,
    classify,
)

from .common import XAccountGetXAPITestBase


@tagged('post_install', '-at_install', 'x_account_getxapi')
class TestGetXAPIErrors(XAccountGetXAPITestBase):

    def test_400_is_bad_request(self):
        error = classify(400, '/test')
        self.assertEqual(error.code, 'bad_request')
        self.assertFalse(error.retryable)

    def test_401_is_authentication_failure(self):
        error = classify(401, '/test')
        self.assertIsInstance(error, GetXAPIAuthenticationError)
        self.assertEqual(error.code, 'authentication_failure')
        self.assertFalse(error.retryable)

    def test_404_is_not_found(self):
        error = classify(404, '/test')
        self.assertIsInstance(error, GetXAPINotFoundError)
        self.assertEqual(error.code, 'not_found')
        self.assertFalse(error.retryable)

    def test_429_is_rate_limit(self):
        error = classify(429, '/test')
        self.assertIsInstance(error, GetXAPIRateLimitError)
        self.assertEqual(error.code, 'rate_limit')
        self.assertTrue(error.retryable)

    def test_500_is_temporary_error(self):
        error = classify(500, '/test')
        self.assertIsInstance(error, GetXAPITemporaryError)
        self.assertEqual(error.code, 'temporary_error')
        self.assertTrue(error.retryable)

    def test_502_is_upstream_rejection(self):
        error = classify(502, '/test')
        self.assertEqual(error.code, 'upstream_rejection')
        self.assertTrue(error.retryable)

    def test_503_is_temporary_error(self):
        error = classify(503, '/test')
        self.assertEqual(error.code, 'temporary_error')
        self.assertTrue(error.retryable)

    def test_504_is_timeout(self):
        error = classify(504, '/test')
        self.assertEqual(error.code, 'timeout')
        self.assertTrue(error.retryable)

    def test_error_has_endpoint(self):
        error = GetXAPIError(401, '/twitter/tweet/retweet', 'unauthorized')
        self.assertEqual(error.endpoint, '/twitter/tweet/retweet')
        self.assertEqual(error.status_code, 401)

    def test_error_to_result(self):
        error = GetXAPIError(429, '/test', 'rate limited')
        result = error.to_result()
        self.assertFalse(result['success'])
        self.assertTrue(result['retryable'])

    def test_classify_with_response_body(self):
        error = classify(401, '/test', {'error': 'invalid_token', 'error_description': 'Token expired'})
        self.assertEqual(error.code, 'authentication_failure')
        self.assertIn('Token expired', error.message)

    def test_unknown_4xx_is_non_retryable(self):
        error = classify(418, '/test')
        self.assertFalse(error.retryable)
