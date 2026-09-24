"""Make the per-chat engagement rules fire on creation, not on a cron scan.

``channel_action_automation.xml`` ships its rules inside a ``noupdate`` block,
so the XML loader never rewrites them on an existing database — flip the trigger
here instead.

``on_time_created`` is implemented as a 10-second scan over ``create_date >=
last_run AND create_date < now()``, and ``create_date`` is stamped with the
creating transaction's start time (``cr.now()``), not the commit time. A message
whose transaction commits after the cron has already advanced ``last_run`` past
that timestamp is never selected again, so it is dropped for good — that is what
lost roughly 9% of link messages. ``on_create`` runs inside the message's own
transaction, so nothing can fall outside a scan window.

``filter_domain`` is a computed-stored field whose compute blanks it for every
non-time trigger, so it is deliberately left untouched here: it carries the
per-chat ``x_auto_*`` gating. ``trg_date_range*`` only mean something for time
triggers, so they are cleared.
"""

_TRIGGERED = [
    'base_automation_x_chat_auto_like',
    'base_automation_x_chat_auto_repost',
    'base_automation_x_chat_auto_comment',
    'base_automation_x_chat_auto_bookmark',
    'base_automation_x_chat_auto_follow',
]


def migrate(cr, version):
    cr.execute(
        """
        UPDATE base_automation
           SET trigger = 'on_create',
               trg_date_range = NULL,
               trg_date_range_type = NULL,
               trg_date_range_mode = NULL
         WHERE id IN (
            SELECT res_id FROM ir_model_data
             WHERE module = 'x_account'
               AND model = 'base.automation'
               AND name = ANY(%s)
         )
        """,
        (_TRIGGERED,),
    )
