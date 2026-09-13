# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Read-only SQL-view report for X engagement operations.

One row per ``x.account.task`` whose operation is one of the four engagement
actions (like, repost, bookmark, comment), enriched with the acting X account,
the discuss channel the automation fired from, and the target tweet (id,
author handle and a clickable link).

Implemented as a PostgreSQL view (``_auto = False``) so the raw operation log
can be sliced by channel / account / tweet in list, pivot and graph views
without storing a denormalized table.

``x.account.task.task_context`` is free-form JSON, and the target tweet id has
been stored under different keys over time (``post_id`` for action tasks,
``target_id`` for group automation, a nested ``post.post_id`` for channel
automation). The view normalizes all of them, and falls back to the task's
stored ``target_post_id`` column.
"""

from odoo import fields, models, tools

_ENGAGEMENT_OPERATIONS = ('like', 'repost', 'bookmark', 'comment')


class XAccountOperationReport(models.Model):
    _name = 'x.account.operation.report'
    _description = 'X Account Operations Report'
    _auto = False
    _log_access = False
    _order = 'create_date desc'
    _rec_name = 'tweet_id'

    account_id = fields.Many2one(
        'social.account', string='X Account', readonly=True)
    channel_id = fields.Many2one(
        'discuss.channel', string='Discuss Channel', readonly=True)
    company_id = fields.Many2one(
        'res.company', string='Company', readonly=True)
    operation = fields.Selection(
        [(op, op.replace('_', ' ').title()) for op in _ENGAGEMENT_OPERATIONS],
        string='Operation', readonly=True)
    tweet_id = fields.Char(string='Tweet ID', readonly=True)
    tweet_screen_name = fields.Char(string='Tweet Author', readonly=True)
    tweet_url = fields.Char(string='Tweet Link', readonly=True)
    status = fields.Selection(
        [
            ('pending', 'Pending'),
            ('running', 'Running'),
            ('success', 'Success'),
            ('failed', 'Failed'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status', readonly=True)
    source = fields.Char(string='Source', readonly=True)
    create_date = fields.Datetime(string='Created On', readonly=True)
    done_at = fields.Datetime(string='Done On', readonly=True)
    operation_count = fields.Integer(
        string='Operations', readonly=True, group_operator='sum')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            'CREATE OR REPLACE VIEW %s AS (%s)'
            % (self._table, self._view_sql()))

    def _view_sql(self):
        operations = ', '.join("'%s'" % op for op in _ENGAGEMENT_OPERATIONS)
        return """
            SELECT
                sub.id,
                sub.account_id,
                sub.channel_id,
                sub.company_id,
                sub.operation,
                sub.tweet_id,
                sub.tweet_screen_name,
                CASE
                    WHEN sub.tweet_id IS NULL THEN NULL
                    WHEN sub.tweet_screen_name IS NOT NULL
                        THEN 'https://x.com/' || sub.tweet_screen_name
                             || '/status/' || sub.tweet_id
                    ELSE 'https://x.com/i/web/status/' || sub.tweet_id
                END AS tweet_url,
                sub.status,
                sub.source,
                sub.create_date,
                sub.done_at,
                1 AS operation_count
            FROM (
                SELECT
                    task.id,
                    task.account_id,
                    COALESCE(task.company_id, account.company_id) AS company_id,
                    NULLIF(task.task_json ->> 'channel_id', '')::int AS channel_id,
                    task.operation,
                    COALESCE(
                        NULLIF(task.task_json ->> 'post_id', ''),
                        NULLIF(task.task_json ->> 'target_id', ''),
                        NULLIF(task.task_json -> 'post' ->> 'post_id', ''),
                        NULLIF(task.target_post_id, '')
                    ) AS tweet_id,
                    COALESCE(
                        NULLIF(task.task_json ->> 'screen_name', ''),
                        author.x_username
                    ) AS tweet_screen_name,
                    task.status,
                    task.source,
                    task.create_date,
                    task.done_at
                FROM (
                    SELECT
                        t.*,
                        CASE WHEN t.task_context ~ '^\\s*\\{'
                             THEN t.task_context::jsonb END AS task_json
                    FROM x_account_task t
                    WHERE t.operation IN (%s)
                ) task
                LEFT JOIN social_account account ON account.id = task.account_id
                LEFT JOIN res_partner author
                    ON author.x_user_id = task.task_json ->> 'author_x_id'
            ) sub
        """ % operations
