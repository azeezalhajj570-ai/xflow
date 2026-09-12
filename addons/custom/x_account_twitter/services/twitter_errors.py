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
    def __init__(self, message='', reset_epoch=None):
        super().__init__('rate_limit', message)
        # Epoch (seconds, UTC) when the 24h user-limit window resets, from the
        # ``x-user-limit-24hour-reset`` response header when X returns it.
        self.reset_epoch = reset_epoch
        # The endpoint-window numbers X returned (``x-rate-limit-*``), set by
        # TwitterApiClient on a 429. These describe the limit that actually
        # throttled the request, which is usually NOT the 24h user cap.
        self.rate_limit_limit = None
        self.rate_limit_remaining = None
        self.rate_limit_reset_epoch = None


class TwitterPermissionError(TwitterError):
    def __init__(self, message=''):
        super().__init__('permission_denied', message)


class TwitterNotFoundError(TwitterError):
    def __init__(self, message=''):
        super().__init__('not_found', message)


class TwitterTemporaryError(TwitterError):
    def __init__(self, message=''):
        super().__init__('temporary_error', message)


class TwitterInvalidTokenError(TwitterError):
    """An OAuth 2.0 token sent to X is invalid, expired, or revoked.

    Raised only by the token endpoint (``/2/oauth2/token``) for the
    ``refresh_token`` / ``authorization_code`` grants.  X returns
    ``{"error": invalid_request|invalid_grant|invalid_client|...}`` when the
    token can no longer be exchanged.  This is a permanent condition: the only
    recovery is a fresh OAuth 2.0 authorization.  It is intentionally NOT a
    ``authentication_failure`` subclass so refresh callers can distinguish
    "token is dead, ask the user to re-link" from "credentials wrong".
    """

    def __init__(self, message=''):
        super().__init__('invalid_token', message)


# X OAuth 2.0 token-endpoint ``error`` values that mean the submitted
# access/refresh token can no longer be used (usually the refresh token was
# revoked, expired, or already rotated by X on the previous use).
_INVALID_TOKEN_ENDPOINT_ERRORS = frozenset({
    'invalid_request', 'invalid_grant', 'invalid_client',
    'unauthorized_client', 'invalid_token', 'expired_token',
})

# Token-endpoint errors that indicate malformed/missing request parameters
# (recoverable if the caller fixes the request), as opposed to a dead token.
_REQUEST_ERRORS = frozenset({
    'unsupported_grant_type', 'unsupported_response_type',
    'invalid_scope', 'invalid_redirect_uri', 'missing_required_parameter',
})


def classify_token_endpoint(status_code, response_body=None):
    """Classify an OAuth 2.0 token-endpoint response.

    Unlike the generic :func:`classify` (used by the REST API), the token
    endpoint returns ``{"error": <code>, "error_description": ...}`` on
    failure. Distinguishes a dead/revoked/expired token (permanent, requires
    re-authorization) from other token-endpoint failures so the caller can
    stop hammering X with the same token.
    """
    if isinstance(response_body, dict):
        error = (response_body.get('error') or '').lower()
        if error in _INVALID_TOKEN_ENDPOINT_ERRORS:
            return TwitterInvalidTokenError(_detail(response_body) or error)
        if error in _REQUEST_ERRORS:
            return TwitterError('token_request_error', _detail(response_body))
    return classify(status_code, response_body)


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


def describe(exc):
    """One-line, operator-readable description of a classified X error.

    Includes the exact rate-limit numbers X returned, so a 429 reads as
    ``rate_limit: Too Many Requests (limit=30, remaining=0, resets at
    2026-09-12 22:04 UTC)`` instead of leaving the caller to guess (or report
    an unrelated cause such as a missing encryption code).
    """
    if not exc:
        return ''
    text = str(getattr(exc, 'message', '') or '')
    details = []
    limit = getattr(exc, 'rate_limit_limit', None)
    remaining = getattr(exc, 'rate_limit_remaining', None)
    if limit is not None or remaining is not None:
        details.append('limit=%s, remaining=%s' % (limit, remaining))
    reset = _format_epoch(getattr(exc, 'rate_limit_reset_epoch', None))
    if reset:
        details.append('resets at %s UTC' % reset)
    if details:
        extra = ', '.join(details)
        text = '%s (%s)' % (text, extra) if text else extra
    code = str(getattr(exc, 'code', '') or '')
    if code and text:
        return '%s: %s' % (code, text)
    return text or code


def _format_epoch(epoch):
    """Format an epoch (seconds, UTC) as ``YYYY-MM-DD HH:MM``, or ''."""
    if not epoch:
        return ''
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(
            int(epoch), timezone.utc).strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError, OSError, OverflowError):
        return ''


def _detail(response_body):
    if isinstance(response_body, dict):
        # Twitter OAuth 2.0 token endpoint returns error/error_description
        detail = response_body.get('error_description') or response_body.get('detail') or response_body.get('title') or ''
        error = response_body.get('error')
        if error and detail:
            detail = '%s: %s' % (error, detail)
        elif error:
            detail = error
        errors = response_body.get('errors') or []
        if errors:
            error_messages = [e.get('message', '') for e in errors if isinstance(e, dict)]
            if error_messages:
                detail = '%s: %s' % (detail, '; '.join(error_messages)) if detail else '; '.join(error_messages)
        return detail
    return ''
