# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""GetXAPI error classification.

Maps HTTP status codes to the lifecycle error taxonomy used by `x_account`
(see `social.account.x_connection_status` / `last_error`). Keeping this in
one place (SRP) means the account lifecycle and the provider agree on what
a given HTTP response means without either knowing the other's internals.

Since GetXAPI is a proxy in front of X/Twitter, a response also carries
upstream context that must survive classification so the operator can tell
*which* limit was hit:

- ``twitter_error_code``: the upstream X error code embedded in GetXAPI's
  body (e.g. 502 = X's daily DM/message-request limit). This does NOT mean
  the HTTP status was 502; GetXAPI may wrap it in an HTTP 429.
- ``retry_after``: how long (seconds) to wait before retrying, when X/GetXAPI
  disclose it (may also arrive as the ``Retry-After`` header).

Classification relevant to 429s:
- ``twitter_error_code == 502`` (daily DM limit) -> ``daily_dm_limit``,
  NOT retryable (the budget only resets on X's 24h schedule).
- plain 429 -> ``rate_limit``, retryable (honor ``retry_after``).
- HTTP 402 -> ``credit_exhausted``, NOT retryable (getxapi billing balance;
  adding credit fixes it, retrying cannot).
"""

_RETRYABLE = frozenset({'rate_limit', 'temporary_error', 'upstream_rejection', 'timeout'})

_HTTP_ERROR_CODES = {
    400: 'bad_request',
    401: 'authentication_failure',
    402: 'credit_exhausted',
    403: 'authentication_failure',
    404: 'not_found',
    429: 'rate_limit',
    500: 'temporary_error',
    502: 'upstream_rejection',
    503: 'temporary_error',
    504: 'timeout',
}

# Upstream Twitter error codes surfaced by GetXAPI in the response body.
_TWITTER_DAILY_DM_LIMIT = 502


class GetXAPIError(Exception):
    """Normalized error from a GetXAPI request.

    Carries the HTTP status, the endpoint that failed, a human-readable
    message, the classified ``code``, a ``retryable`` flag, and — when the
    upstream disclosed them — ``twitter_error_code`` and ``retry_after``.
    """

    def __init__(self, status_code, endpoint, message='', code=None,
                 twitter_error_code=None, retry_after=None):
        self.status_code = status_code
        self.endpoint = endpoint
        self.message = message or str(status_code)
        self.code = code or _HTTP_ERROR_CODES.get(
            status_code, 'http_%s' % status_code)
        self.retryable = self.code in _RETRYABLE
        self.twitter_error_code = twitter_error_code
        self.retry_after = retry_after
        super().__init__(self.message)

    def to_result(self):
        return {
            'success': False,
            'error': self.code,
            'retryable': self.retryable,
            'twitter_error_code': self.twitter_error_code,
            'retry_after': self.retry_after,
        }


class GetXAPIAuthenticationError(GetXAPIError):
    def __init__(self, endpoint, message=''):
        super().__init__(401, endpoint, message or 'authentication_failure')


class GetXAPIRateLimitError(GetXAPIError):
    def __init__(self, endpoint, message='', twitter_error_code=None,
                 retry_after=None):
        super().__init__(429, endpoint, message or 'rate_limit',
                         twitter_error_code=twitter_error_code,
                         retry_after=retry_after)


class GetXAPINotFoundError(GetXAPIError):
    def __init__(self, endpoint, message=''):
        super().__init__(404, endpoint, message or 'not_found')


class GetXAPITemporaryError(GetXAPIError):
    def __init__(self, endpoint, message=''):
        super().__init__(500, endpoint, message or 'temporary_error')


def classify(status_code, endpoint, response_body=None):
    """Return a GetXAPIError for an HTTP status + optional response body.

    Prefers the status-code taxonomy; falls back to a generic temporary error
    for 5xx and a generic non-retryable error otherwise. Body-level upstream
    signals (``twitter_error_code`` / ``retry_after``) are preserved.
    """
    code = _HTTP_ERROR_CODES.get(status_code)
    if code:
        return _build_error(code, status_code, endpoint, response_body)
    if status_code >= 500:
        return GetXAPITemporaryError(endpoint, _detail(response_body))
    return GetXAPIError(status_code, endpoint, _detail(response_body))


def _build_error(code, status_code, endpoint, response_body):
    detail = _detail(response_body)
    twitter_code = _twitter_error_code(response_body)
    retry_after = _retry_after(response_body)
    if code == 'authentication_failure':
        return GetXAPIAuthenticationError(endpoint, detail)
    if code == 'rate_limit':
        if twitter_code == _TWITTER_DAILY_DM_LIMIT:
            message = ('X daily DM/message-request limit reached'
                       + _hint(twitter_code, retry_after))
            return GetXAPIError(
                429, endpoint, message, code='daily_dm_limit',
                twitter_error_code=twitter_code, retry_after=retry_after)
        return GetXAPIRateLimitError(
            endpoint, detail + _hint(twitter_code, retry_after),
            twitter_error_code=twitter_code, retry_after=retry_after)
    if code == 'not_found':
        return GetXAPINotFoundError(endpoint, detail)
    if code == 'temporary_error':
        return GetXAPITemporaryError(endpoint, detail)
    if code == 'credit_exhausted':
        return GetXAPIError(
            402, endpoint, detail or 'getxapi_credit_exhausted',
            code='credit_exhausted', twitter_error_code=twitter_code,
            retry_after=retry_after)
    return GetXAPIError(status_code, endpoint, detail,
                        twitter_error_code=twitter_code,
                        retry_after=retry_after)


def _hint(twitter_error_code, retry_after):
    parts = []
    if twitter_error_code is not None:
        parts.append('(twitter_error_code=%s)' % twitter_error_code)
    if retry_after is not None:
        try:
            ra = int(retry_after) if float(retry_after).is_integer() else retry_after
        except (TypeError, ValueError):
            ra = retry_after
        parts.append('retry_after=%ss' % ra)
    return (' ' + ' '.join(parts)) if parts else ''


def _twitter_error_code(response_body):
    if isinstance(response_body, dict):
        try:
            return int(response_body.get('twitter_error_code'))
        except (TypeError, ValueError):
            return None
    return None


def _retry_after(response_body):
    """Numeric ``retry_after`` from the response body (seconds), or None.

    GetXAPI documents ``retry_after`` as seconds in the JSON body; the
    ``Retry-After`` HTTP header is handled by the transport layer.
    """
    if isinstance(response_body, dict):
        value = response_body.get('retry_after')
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


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