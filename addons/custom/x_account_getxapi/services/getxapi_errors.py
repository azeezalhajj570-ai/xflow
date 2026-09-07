# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""GetXAPI error classification.

Maps HTTP status codes to the lifecycle error taxonomy used by `x_account`
(see `social.account.x_connection_status` / `last_error`). Keeping this in
one place (SRP) means the account lifecycle and the provider agree on what
a given HTTP response means without either knowing the other's internals.
"""

_RETRYABLE = frozenset({'rate_limit', 'temporary_error', 'upstream_rejection', 'timeout'})

_HTTP_ERROR_CODES = {
    400: 'bad_request',
    401: 'authentication_failure',
    403: 'authentication_failure',
    404: 'not_found',
    429: 'rate_limit',
    500: 'temporary_error',
    502: 'upstream_rejection',
    503: 'temporary_error',
    504: 'timeout',
}


class GetXAPIError(Exception):
    """Normalized error from a GetXAPI request.

    Carries the HTTP status, the endpoint that failed, a human-readable
    message, and a ``retryable`` flag derived from the status code.
    """

    def __init__(self, status_code, endpoint, message=''):
        self.status_code = status_code
        self.endpoint = endpoint
        self.message = message or str(status_code)
        self.code = _HTTP_ERROR_CODES.get(status_code, 'http_%s' % status_code)
        self.retryable = self.code in _RETRYABLE
        super().__init__(self.message)

    def to_result(self):
        return {'success': False, 'error': self.code, 'retryable': self.retryable}


class GetXAPIAuthenticationError(GetXAPIError):
    def __init__(self, endpoint, message=''):
        super().__init__(401, endpoint, message or 'authentication_failure')


class GetXAPIRateLimitError(GetXAPIError):
    def __init__(self, endpoint, message=''):
        super().__init__(429, endpoint, message or 'rate_limit')


class GetXAPINotFoundError(GetXAPIError):
    def __init__(self, endpoint, message=''):
        super().__init__(404, endpoint, message or 'not_found')


class GetXAPITemporaryError(GetXAPIError):
    def __init__(self, endpoint, message=''):
        super().__init__(500, endpoint, message or 'temporary_error')


def classify(status_code, endpoint, response_body=None):
    """Return a GetXAPIError for an HTTP status + optional response body.

    Prefers the status-code taxonomy; falls back to a generic temporary error
    for 5xx and a generic non-retryable error otherwise.
    """
    code = _HTTP_ERROR_CODES.get(status_code)
    if code:
        return _build_error(code, status_code, endpoint, response_body)
    if status_code >= 500:
        return GetXAPITemporaryError(endpoint, _detail(response_body))
    return GetXAPIError(status_code, endpoint, _detail(response_body))


def _build_error(code, status_code, endpoint, response_body):
    detail = _detail(response_body)
    if code == 'authentication_failure':
        return GetXAPIAuthenticationError(endpoint, detail)
    if code == 'rate_limit':
        return GetXAPIRateLimitError(endpoint, detail)
    if code == 'not_found':
        return GetXAPINotFoundError(endpoint, detail)
    if code == 'temporary_error':
        return GetXAPITemporaryError(endpoint, detail)
    return GetXAPIError(status_code, endpoint, detail)


def _detail(response_body):
    if isinstance(response_body, dict):
        return (
            response_body.get('error_description')
            or response_body.get('detail')
            or response_body.get('message')
            or response_body.get('title')
            or response_body.get('error')
            or ''
        )
    return ''
