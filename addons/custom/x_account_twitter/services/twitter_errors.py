# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Twitter/X API error classification.

Maps HTTP status codes (and X API error bodies) to the lifecycle error
taxonomy used by `x_account` — the task worker only ever sees these
normalized codes, never raw HTTP statuses.

Taxonomy (mirrors `x_account` lifecycle):
- authentication/permission -> 'authentication_failure' (non-retryable)
- rate limit / 5xx           -> retryable (the task queue backs off)
- not found / 4xx            -> non-retryable
"""

# Retryable classifications: the task queue schedules a retry with backoff.
_RETRYABLE = frozenset({'rate_limit', 'temporary_error'})

# HTTP status -> classified error code.
_HTTP_ERROR_CODES = {
    401: 'authentication_failure',
    403: 'permission_denied',
    404: 'not_found',
    429: 'rate_limit',
}


class TwitterError(Exception):
    """Base error for the Twitter provider.

    Carries the normalized code the x_account task worker understands, plus a
    ``retryable`` flag derived from the code.
    """

    def __init__(self, code, message=''):
        self.code = code
        self.message = message or code
        self.retryable = code in _RETRYABLE
        super().__init__(self.message)

    def to_result(self):
        return {'success': False, 'error': self.code, 'retryable': self.retryable}


class TwitterAuthenticationError(TwitterError):
    def __init__(self, message=''):
        super().__init__('authentication_failure', message)


class TwitterRateLimitError(TwitterError):
    def __init__(self, message=''):
        super().__init__('rate_limit', message)


class TwitterPermissionError(TwitterError):
    def __init__(self, message=''):
        super().__init__('permission_denied', message)


class TwitterNotFoundError(TwitterError):
    def __init__(self, message=''):
        super().__init__('not_found', message)


class TwitterTemporaryError(TwitterError):
    def __init__(self, message=''):
        super().__init__('temporary_error', message)


def classify(status_code, response_body=None):
    """Return a TwitterError for an HTTP status + optional X API body.

    Prefers the status-code taxonomy; falls back to a generic temporary error
    for 5xx and a generic non-retryable error otherwise.
    """
    code = _HTTP_ERROR_CODES.get(status_code)
    if code:
        return _build_error(code, response_body)
    if status_code >= 500:
        return TwitterTemporaryError(_detail(response_body))
    return TwitterError('http_%s' % status_code, _detail(response_body))


def _build_error(code, response_body):
    detail = _detail(response_body)
    if code == 'authentication_failure':
        return TwitterAuthenticationError(detail)
    if code == 'permission_denied':
        return TwitterPermissionError(detail)
    if code == 'rate_limit':
        return TwitterRateLimitError(detail)
    if code == 'not_found':
        return TwitterNotFoundError(detail)
    return TwitterError(code, detail)


def _detail(response_body):
    if isinstance(response_body, dict):
        return response_body.get('detail') or response_body.get('title') or ''
    return ''
