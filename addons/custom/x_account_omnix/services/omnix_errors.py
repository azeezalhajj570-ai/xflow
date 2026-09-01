# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""OmniX error classification.

Maps HTTP status codes to the lifecycle error taxonomy used by `x_account`
(see `social.account.x_connection_status` / `last_error`). Keeping this in one
place (SRP) means the account lifecycle and the provider agree on what a given
HTTP response means without either knowing the other's internals.
"""

# HTTP status → classified error code. Mirrors the lifecycle error taxonomy:
# transient conditions (rate_limit) keep the account ACTIVE; auth failures
# move it to ERROR / REAUTH_REQUIRED.
_HTTP_ERROR_CODES = {
    400: 'authentication_failure',   # missing/invalid auth_token
    401: 'authentication_failure',   # missing/invalid API key
    402: 'rate_limit',               # insufficient credits (transient, stays ACTIVE)
    403: 'authentication_failure',
    404: 'http_404',
    429: 'rate_limit',
}


def classify_http_status(status_code):
    """Return the classified error code for an HTTP status, or None when 2xx."""
    return _HTTP_ERROR_CODES.get(status_code)


def classify_error(status_code, response_text=''):
    """Return a classified error code for a failed HTTP response.

    Prefers the taxonomy mapping; falls back to ``http_<status>``; and finally
    to a non-JSON / envelope error when the response is not usable.
    """
    code = classify_http_status(status_code)
    if code:
        return code
    if status_code >= 400:
        return 'http_%s' % status_code
    if response_text:
        return 'omnix_request_failed'
    return 'non_json_response'
