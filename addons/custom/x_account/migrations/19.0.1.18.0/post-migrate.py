"""Link existing tasks to the message they were created from.

``x.account.task.message_id`` gives every task an explicit origin message, so
the message form can show the tasks it spawned. Tasks created before the field
existed carry their origin only inside ``task_context`` (free-form JSON), so
this backfills the link using the same resolution as
``x.account.operation.report``: the latest message of the task's channel that
was already received when the task was created and that matches its target.

Engagement tasks (like/repost/comment/bookmark/unbookmark) match on the tweet
link in the message body. ``follow`` has no tweet id and matches on the message
author instead — by X id when the context carries one, by username otherwise.
Tasks whose message cannot be resolved (no channel in the context, no matching
message) keep an empty link; the message stat simply counts nothing for them.

The JSON is only cast when the text looks like an object: ``task_context`` is
free text, and a malformed value must not abort the upgrade.
"""

_ENGAGEMENT_OPERATIONS = ('like', 'repost', 'comment', 'bookmark', 'unbookmark')


def migrate(cr, version):
    _link_engagement_tasks(cr)
    _link_follow_tasks(cr)


def _link_engagement_tasks(cr):
    """Point engagement tasks at the message whose body links their target."""
    cr.execute(
        """
        UPDATE x_account_task task
           SET message_id = pick.message_id
          FROM (
                SELECT DISTINCT ON (t.id) t.id AS task_id, m.id AS message_id
                  FROM x_account_task t
                  JOIN x_message m
                    ON m.channel_id =
                           (t.task_context::jsonb ->> 'channel_id')::int
                   AND substring(m.body_plain FROM '/status/([0-9]+)') =
                       COALESCE(
                           NULLIF(t.task_context::jsonb ->> 'post_id', ''),
                           NULLIF(t.task_context::jsonb ->> 'target_id', ''),
                           NULLIF(t.task_context::jsonb -> 'post' ->> 'post_id', ''),
                           NULLIF(t.target_post_id, '')
                       )
                   AND COALESCE(m.external_created_at, m.create_date)
                       <= t.create_date
                 WHERE t.message_id IS NULL
                   AND t.operation = ANY(%s)
                   AND t.task_context ~ '^\\s*\\{'
                   AND (t.task_context::jsonb ->> 'channel_id') ~ '^[0-9]+$'
                 ORDER BY t.id,
                          COALESCE(m.external_created_at, m.create_date) DESC
               ) pick
         WHERE task.id = pick.task_id
        """,
        (list(_ENGAGEMENT_OPERATIONS),),
    )


def _link_follow_tasks(cr):
    """Point follow tasks at the message whose author was followed."""
    cr.execute(
        """
        UPDATE x_account_task task
           SET message_id = pick.message_id
          FROM (
                SELECT DISTINCT ON (t.id) t.id AS task_id, m.id AS message_id
                  FROM x_account_task t
                  JOIN x_message m
                    ON m.channel_id =
                           (t.task_context::jsonb ->> 'channel_id')::int
                   AND COALESCE(m.external_created_at, m.create_date)
                       <= t.create_date
                   AND (
                         (
                           NULLIF(t.task_context::jsonb ->> 'author_x_id', '')
                               IS NOT NULL
                           AND m.author_x_id =
                               NULLIF(t.task_context::jsonb ->> 'author_x_id', '')
                         )
                      OR (
                           NULLIF(t.task_context::jsonb ->> 'screen_name', '')
                               IS NOT NULL
                           AND m.author_x_username =
                               NULLIF(t.task_context::jsonb ->> 'screen_name', '')
                         )
                   )
                 WHERE t.message_id IS NULL
                   AND t.operation = 'follow'
                   AND t.task_context ~ '^\\s*\\{'
                   AND (t.task_context::jsonb ->> 'channel_id') ~ '^[0-9]+$'
                 ORDER BY t.id,
                          COALESCE(m.external_created_at, m.create_date) DESC
               ) pick
         WHERE task.id = pick.task_id
        """
    )
