# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""GetXAPI API usage tracking model.

Every paid GetXAPI request is logged here so administrators can monitor
API spending. The client writes to this model automatically after each
request; it is never written to directly by business logic.
"""

from odoo import api, fields, models


class GetXAPIUsage(models.Model):
    _name = 'getxapi.api.usage'
    _description = 'GetXAPI API Usage'
    _order = 'create_date desc'
    _rec_name = 'endpoint'

    account_id = fields.Many2one(
        'social.account',
        string='X Account',
        index=True,
        ondelete='set null',
        help='The X account that triggered this API call.',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='account_id.company_id',
        store=True,
        index=True,
    )
    endpoint = fields.Char(
        string='Endpoint',
        index=True,
        help='The GetXAPI endpoint path (e.g. twitter/tweet/retweet).',
    )
    method = fields.Selection(
        [('GET', 'GET'), ('POST', 'POST')],
        string='Method',
        help='HTTP method used for the request.',
    )
    timestamp = fields.Datetime(
        string='Timestamp',
        default=fields.Datetime.now,
        index=True,
    )
    status_code = fields.Integer(
        string='Status Code',
        help='HTTP status code returned by GetXAPI.',
    )
    success = fields.Boolean(
        string='Success',
        index=True,
        default=True,
    )
    estimated_cost = fields.Float(
        string='Estimated Cost (USD)',
        digits=(16, 8),
        help='Estimated USD cost for this API call based on the pricing table.',
    )
    request_duration = fields.Integer(
        string='Duration (ms)',
        help='Request duration in milliseconds.',
    )
    error_type = fields.Char(
        string='Error Type',
        help='Classified error code when the request failed (e.g. rate_limit, authentication_failure).',
    )

    @api.model
    def _archive_old_records(self, days=90):
        """Delete usage records older than ``days`` days."""
        cutoff = fields.Datetime.now()
        from datetime import timedelta
        cutoff = cutoff - timedelta(days=days)
        old = self.search([('timestamp', '<', cutoff)])
        if old:
            old.unlink()

    @api.model
    def _compute_total_cost(self, domain=None):
        """Return the total estimated cost for records matching ``domain``."""
        records = self.search(domain or [])
        return sum(records.mapped('estimated_cost'))
