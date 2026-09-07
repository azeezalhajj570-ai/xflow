# Migration: 1.0.0
# Add missing index on x_twitter_event.task_id to speed up cascading deletes.
# Without this index, PostgreSQL sequentially scans 100k+ rows for every
# x_account_task deletion, causing 504 Gateway Timeouts.

cr = env.cr
cr.execute(
    "SELECT 1 FROM pg_indexes WHERE indexname = 'x_twitter_event__task_id_index'")
if not cr.fetchone():
    env.cr.execute(
        "CREATE INDEX CONCURRENTLY x_twitter_event__task_id_index "
        "ON x_twitter_event(task_id)")
    env.cr.commit()
