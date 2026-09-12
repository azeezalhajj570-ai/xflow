# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Thin HTTP transport for the GetXAPI REST API.

Owns request/response plumbing only (SRP): URL building, Bearer authentication,
bounded retry, JSON parsing, cost estimation, and usage logging. It knows
nothing about tweets, users, DMs, or Odoo business logic.

Retry policy:
- Reads (GET): retry up to DEFAULT_RETRIES on network error / 5xx / timeout
- Writes (POST): do NOT retry on timeout (state unknown); retry only on
  explicit 502/503/504 from the server
- Exponential backoff: BACKOFF_BASE * 2^(attempt-1) seconds
- Honor Retry-After header when present

Pagination:
- ``paginate()`` yields pages from cursor-based endpoints
- Guards: max_pages, max_items, repeated cursor detection
"""

import logging
import time

import requests

from . import getxapi_errors
from .getxapi_cost import estimate_cost

_LOGGER = logging.getLogger(__name__)

_BASE_URL = 'https://api.getxapi.com'
_TIMEOUT_SECONDS = 20


class GetXAPIClient:
    """Transport for one GetXAPI key.

    :param env: Odoo environment (for usage logging).
    :param api_key: GetXAPI Bearer token.
    :param account_id: Optional social.account id for usage tracking.
    :param base_url: Override base URL (for testing).
    :param timeout: Request timeout in seconds.
    """

    BASE_URL = _BASE_URL
    DEFAULT_TIMEOUT = _TIMEOUT_SECONDS
    DEFAULT_RETRIES = 2
    BACKOFF_BASE = 2

    def __init__(self, env, api_key, account_id=None, base_url=None, timeout=None):
        self._env = env
        self._api_key = api_key or ''
        self._account_id = account_id
        self._base_url = (base_url or _BASE_URL).rstrip('/')
        self._timeout = timeout or _TIMEOUT_SECONDS

    def get(self, path, params=None):
        """Send a GET request and return the parsed envelope."""
        return self.request('GET', path, params=params)

    def post(self, path, json=None, params=None):
        """Send a POST request and return the parsed envelope."""
        return self.request('POST', path, params=params, json=json)

    def request(self, method, path, params=None, json=None):
        """Send an authenticated request and return the parsed envelope.

        Raises GetXAPIError on failure. Automatically logs usage.
        """
        if not self._api_key:
            raise getxapi_errors.GetXAPIAuthenticationError(path, 'getxapi_api_key_missing')

        url = self._base_url + (path if path.startswith('/') else '/' + path)
        is_write = method.upper() != 'GET'
        start = time.monotonic()

        response = self._send(method, url, params, json, is_write=is_write)
        duration_ms = int((time.monotonic() - start) * 1000)

        if response.ok:
            try:
                envelope = response.json() if response.content else {}
            except ValueError:
                raise getxapi_errors.GetXAPITemporaryError(path, 'non_json_response')
            self._log_usage(path, method, response.status_code, True, duration_ms, '')
            return envelope

        body = self._body_json(response)
        error = getxapi_errors.classify(response.status_code, path, body)
        self._log_usage(
            path, method, response.status_code, False, duration_ms, error.code)
        raise error

    def paginate(self, path, params=None, method='GET', json=None,
                 max_pages=50, max_items=1000, cursor_key='cursor',
                 items_key='tweets', has_more_key='has_more'):
        """Cursor-based pagination helper.

        Yields one page (list of items) per call. Stops when:
        - ``has_more`` is false
        - cursor is missing or empty
        - cursor repeats (same as previous)
        - max_pages or max_items reached

        :param path: API endpoint path.
        :param params: Query parameters (GET) or None.
        :param method: HTTP method (GET or POST).
        :param json: JSON body (POST) or None.
        :param max_pages: Maximum number of pages to fetch.
        :param max_items: Maximum total items to yield.
        :param cursor_key: Key in params/json for the cursor value.
        :param items_key: Key in response envelope for the items list.
        :param has_more_key: Key in response envelope for the has_more flag.
        """
        params = dict(params or {})
        json_body = dict(json or {})
        previous_cursor = None
        total_items = 0

        for page_num in range(max_pages):
            if method.upper() == 'GET':
                envelope = self.get(path, params=params)
            else:
                envelope = self.post(path, json=json_body, params=params)

            data = (envelope or {}).get('data') or envelope or {}
            items = data.get(items_key) or data.get('entries') or data.get('results') or []
            if isinstance(items, dict):
                items = list(items.values())

            yield items

            total_items += len(items)
            if total_items >= max_items:
                break

            cursor = data.get('next_cursor') or data.get('cursor') or ''
            if isinstance(cursor, dict):
                cursor = cursor.get('cursor_id') or cursor.get('value') or cursor.get('cursor') or ''
            cursor = str(cursor) if cursor else ''

            has_more = data.get(has_more_key, bool(cursor))
            if not has_more or not cursor:
                break

            if cursor == previous_cursor:
                _LOGGER.warning(
                    'GetXAPI pagination: repeated cursor on page %s for %s; stopping',
                    page_num + 1, path)
                break
            previous_cursor = cursor

            if method.upper() == 'GET':
                params[cursor_key] = cursor
            else:
                json_body[cursor_key] = cursor

    def collect_pages(self, path, params=None, method='GET', json=None,
                      max_pages=50, max_items=1000, **kwargs):
        """Collect all items from a paginated endpoint into a single list.

        Convenience wrapper around :meth:`paginate` that flattens all pages.
        """
        items = []
        for page in self.paginate(
            path, params=params, method=method, json=json,
            max_pages=max_pages, max_items=max_items, **kwargs
        ):
            items.extend(page)
        return items

    def _send(self, method, url, params, json, is_write=False):
        """Send the HTTP request with bounded retry.

        :param is_write: If True, do NOT retry on timeout (state unknown).
        """
        retries = self.DEFAULT_RETRIES
        attempt = 0
        last_exc = None

        while True:
            try:
                response = requests.request(
                    method, url,
                    params=params,
                    json=json,
                    headers=self._headers(),
                    timeout=self._timeout,
                )
            except requests.Timeout as exc:
                if is_write:
                    raise getxapi_errors.GetXAPIError(
                        504, url, 'timeout on write operation (not retried)')
                if attempt >= retries:
                    raise getxapi_errors.GetXAPITemporaryError(url, 'timeout: %s' % exc)
                last_exc = exc
                delay = self._retry_delay(attempt + 1, None)
                _LOGGER.warning(
                    'GetXAPI timeout on %s (attempt %s/%s); retrying in %ss',
                    url, attempt + 1, retries, delay)
                self._sleep(delay)
                attempt += 1
                continue
            except requests.RequestException as exc:
                if attempt >= retries:
                    raise getxapi_errors.GetXAPITemporaryError(url, 'network_error: %s' % exc)
                last_exc = exc
                delay = self._retry_delay(attempt + 1, None)
                _LOGGER.warning(
                    'GetXAPI network error on %s (attempt %s/%s); retrying in %ss',
                    url, attempt + 1, retries, delay)
                self._sleep(delay)
                attempt += 1
                continue

            if response.status_code < 500:
                return response

            if attempt >= retries:
                return response

            if response.status_code == 502 and is_write:
                return response

            retry_after = response.headers.get('Retry-After')
            delay = self._retry_delay(attempt + 1, retry_after)
            _LOGGER.warning(
                'GetXAPI %s on %s (attempt %s/%s); retrying in %ss',
                response.status_code, url, attempt + 1, retries, delay)
            self._sleep(delay)
            attempt += 1

    def _headers(self):
        return {
            'Authorization': 'Bearer %s' % self._api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _log_usage(self, path, method, status_code, success, duration_ms, error_type):
        """Log API usage to getxapi.api.usage. Failures here are silently ignored."""
        if not self._account_id:
            return
        try:
            cost = estimate_cost(path)
            vals = {
                'endpoint': path,
                'method': method.upper(),
                'status_code': status_code,
                'success': success,
                'estimated_cost': cost,
                'request_duration': duration_ms,
                'error_type': error_type or '',
                'account_id': self._account_id,
            }
            created = self._env['getxapi.api.usage'].sudo().create(vals)
            _LOGGER.info('GetXAPI usage logged: acc=%s status=%s endpoint=%s id=%s',
                         self._account_id, status_code, path, created.id)
        except Exception as exc:
            _LOGGER.warning('GetXAPI usage LOG FAILED: %r', exc, exc_info=True)
            raise

    @staticmethod
    def _retry_delay(attempt, retry_after):
        """Seconds to wait before retry ``attempt`` (1-based)."""
        if retry_after:
            try:
                return max(0.5, min(float(retry_after), 60.0))
            except (TypeError, ValueError):
                pass
        return GetXAPIClient.BACKOFF_BASE * (2 ** max(attempt - 1, 0))

    @staticmethod
    def _sleep(seconds):
        """Sleep hook (overridable in tests)."""
        time.sleep(seconds)

    @staticmethod
    def _body_json(response):
        try:
            return response.json()
        except ValueError:
            return None
