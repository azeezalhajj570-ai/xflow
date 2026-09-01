# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""X post URL resolver.

Responsibility is only ``URL -> Post Reference`` (platform, post_id,
canonical_url). It never performs the API action — the provider calls the API.

Supported forms:

    https://x.com/<handle>/status/<post_id>
    https://twitter.com/<handle>/status/<post_id>
    https://x.com/<handle>/status/<post_id>?s=20
    http://x.com/<handle>/status/<post_id>#fragment
"""

import re

from odoo.exceptions import ValidationError

_STATUS_RE = re.compile(r'^https?://(?:x|twitter)\.com/[^/]+/status/(\d+)', re.IGNORECASE)


class TwitterLink:
    """Parse an X/Twitter post URL into a normalized PostReference."""

    @staticmethod
    def resolve(url):
        """Return {'platform', 'post_id', 'canonical_url'} for a valid X post URL.

        Raises ValidationError when the URL is not a parseable X status link.
        """
        if not url:
            raise ValidationError('Missing X post URL')
        match = _STATUS_RE.match(url.strip())
        if not match:
            raise ValidationError('Not a valid X/Twitter post URL: %s' % url)
        post_id = match.group(1)
        canonical_url = 'https://x.com/-/status/%s' % post_id
        return {
            'platform': 'x',
            'post_id': post_id,
            'canonical_url': canonical_url,
        }
