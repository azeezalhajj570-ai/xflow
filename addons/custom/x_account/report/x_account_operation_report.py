# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Read-only SQL-view report for X engagement operations.

One row per ``x.account.task`` whose operation is one of the six engagement
actions (like, repost, bookmark, comment, follow, unbookmark), enriched with
the acting X account, the discuss channel the automation fired from (linked to
its X chat URL ``https://x.com/i/chat/<conversation_id>``), and the target
tweet (id, author handle and a clickable link).

Implemented as a PostgreSQL view (``_auto = False``) so the raw operation log
can be sliced by channel / account / tweet in list, pivot and graph views
without storing a denormalized table.

``x.account.task.task_context`` is free-form JSON, and the target tweet id has
been stored under different keys over time (``post_id`` for action tasks,
``target_id`` for group automation, a nested ``post.post_id`` for channel
automation). The view normalizes all of them, and falls back to the task's
stored ``target_post_id`` column.

``received_at`` is the timestamp of the message the task was created from: the
latest message of the channel linking the same tweet that was already received
when the task was created. ``processing_time`` (``done_at - received_at``) can
therefore never come out negative, which it did when a later repost of the same
link was picked up as the receipt.
"""

from odoo import fields, models, tools

_ENGAGEMENT_OPERATIONS = (
    'like', 'repost', 'bookmark', 'comment', 'follow', 'unbookmark')


class XAccountOperationReport(models.Model):
    _name = 'x.account.operation.report'
    _description = 'X Account Operations Report'
    _auto = False
    _log_access = False
    _order = 'received_at desc, id desc'
    _rec_name = 'tweet_id'

    account_id = fields.Many2one(
        'social.account', string='X Account', readonly=True)
    channel_id = fields.Many2one(
        'discuss.channel', string='Discuss Channel', readonly=True)
    channel_name = fields.Char(string='Channel Name', readonly=True)
    channel_url = fields.Char(string='Channel Link', readonly=True)
    channel_link = fields.Html(string='Channel', readonly=True)
    company_id = fields.Many2one(
        'res.company', string='Company', readonly=True)
    operation = fields.Selection(
        [(op, op.replace('_', ' ').title()) for op in _ENGAGEMENT_OPERATIONS],
        string='Operation', readonly=True)
    tweet_id = fields.Char(string='Tweet ID', readonly=True)
    tweet_screen_name = fields.Char(string='Tweet Author', readonly=True)
    author_x_id = fields.Char(string='Author X ID', readonly=True)
    author_url = fields.Char(string='Author Link', readonly=True)
    author_link = fields.Html(string='Author', readonly=True)
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
    create_date = fields.Datetime(string='Task Created', readonly=True)
    received_at = fields.Datetime(string='Received At', readonly=True)
    done_at = fields.Datetime(string='Processed At', readonly=True)
    processing_time = fields.Integer(
        string='Period of Processing (sec)',
        readonly=True,
        group_operator='avg',
        help='Seconds between the received post and the processed task.',
    )
    operation_count = fields.Integer(
        string='Operations', readonly=True, group_operator='sum')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            'CREATE OR REPLACE VIEW %s AS (%s)'
            % (self._table, self._view_sql()))
        self._init_message_index()

    def _init_message_index(self):
        """Index the tweet link extracted from the message body.

        The view resolves the receipt through
        ``substring(body_plain FROM '/status/([0-9]+)')``. Without statistics
        on that expression the planner estimates the join at a handful of rows
        and falls back to a nested loop, which re-scans every message of the
        channel for every task (minutes on a loaded database). Indexing the
        expression gives the planner the real selectivity and turns each probe
        into a single index lookup.
        """
        self.env.cr.execute("SELECT to_regclass('x_message')")
        if self.env.cr.fetchone()[0] is None:
            return
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS x_message__tweet_link_index
            ON x_message (channel_id, substring(body_plain FROM '/status/([0-9]+)'))
        """)

    def _view_sql(self):
        operations = ', '.join("'%s'" % op for op in _ENGAGEMENT_OPERATIONS)
        return """
            SELECT
                sub.id,
                sub.account_id,
                sub.channel_id,
                sub.channel_name,
                CASE
                    WHEN sub.channel_conversation_id IS NOT NULL
                        THEN 'https://x.com/i/chat/' || sub.channel_conversation_id
                END AS channel_url,
                CASE
                    WHEN sub.channel_conversation_id IS NOT NULL
                        THEN '<a href="https://x.com/i/chat/'
                             || sub.channel_conversation_id
                             || '" target="_blank" rel="noopener">'
                             || COALESCE(sub.channel_name, sub.channel_conversation_id)
                             || '</a>'
                    ELSE sub.channel_name
                END AS channel_link,
                sub.company_id,
                sub.operation,
                sub.tweet_id,
                COALESCE(sub.tweet_screen_name, sub.author_x_id) AS tweet_screen_name,
                sub.author_x_id,
                CASE
                    WHEN sub.tweet_screen_name IS NOT NULL
                        THEN 'https://x.com/' || sub.tweet_screen_name
                    WHEN sub.author_x_id IS NOT NULL
                        THEN 'https://x.com/i/user/' || sub.author_x_id
                END AS author_url,
                CASE
                    WHEN sub.tweet_screen_name IS NOT NULL
                        THEN '<a href="https://x.com/' || sub.tweet_screen_name
                             || '" target="_blank" rel="noopener">'
                             || sub.tweet_screen_name || '</a>'
                    WHEN sub.author_x_id IS NOT NULL
                        THEN '<a href="https://x.com/i/user/' || sub.author_x_id
                             || '" target="_blank" rel="noopener">'
                             || sub.author_x_id || '</a>'
                END AS author_link,
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
                COALESCE(sub.received_at, sub.create_date) AS received_at,
                sub.done_at,
                CASE
                    WHEN sub.done_at IS NOT NULL THEN
                        EXTRACT(EPOCH FROM (
                            sub.done_at
                            - COALESCE(sub.received_at, sub.create_date)
                        ))
                END AS processing_time,
                1 AS operation_count
            FROM (
                SELECT
                    base.*,
                    channel.name AS channel_name,
                    channel.x_conversation_id AS channel_conversation_id,
                    MAX(COALESCE(message.external_created_at, message.create_date))
                        AS received_at
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
                        NULLIF(task.task_json ->> 'author_x_id', '') AS author_x_id,
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
                        WHERE t.operation IN (__OPERATIONS__)
                    ) task
                    LEFT JOIN social_account account ON account.id = task.account_id
                    LEFT JOIN res_partner author
                        ON author.x_user_id = task.task_json ->> 'author_x_id'
                ) base
                LEFT JOIN discuss_channel channel ON channel.id = base.channel_id
                -- The message the task was created from: the latest message of
                -- the channel that links the target tweet and was already
                -- received when the task was created. Taking the latest message
                -- of the channel regardless of the task would pick up a later
                -- repost of the same link and date the receipt after the task
                -- was processed, which made `processing_time` negative.
                LEFT JOIN x_message message
                    ON message.channel_id = base.channel_id
                   AND substring(message.body_plain FROM '/status/([0-9]+)')
                           = base.tweet_id
                   AND COALESCE(message.external_created_at,
                                message.create_date) <= base.create_date
                GROUP BY base.id, base.account_id, base.company_id,
                         base.channel_id, base.operation, base.tweet_id,
                         base.tweet_screen_name, base.author_x_id, base.status,
                         base.source, base.create_date, base.done_at,
                         channel.name, channel.x_conversation_id
            ) sub
        """.replace('__OPERATIONS__', operations)
