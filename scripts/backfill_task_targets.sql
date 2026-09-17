-- Backfill x_account_task.target_post_id / target_screen_name from task_context.
--
-- Both are stored computed fields. The compute read only a top-level `post_id`,
-- but channel automation stores the tweet id nested under `post.post_id`, while
-- group automation stores a top-level `target_id`. The field therefore stayed
-- empty for every channel task, which made its index unusable for dedup and let
-- each hourly re-post of the same link spawn another doomed task.
--
-- Fixing the compute does not rewrite rows that already exist, so backfill them
-- here. One-off script, not a module migration.
--
-- Idempotent: only rows whose target fields are still empty are considered, so
-- re-running is a no-op. A malformed task_context is skipped with a warning
-- instead of aborting the whole run.
--
-- Run with:
--   docker exec -i madarbot-postgres-1 psql -U odoo -d <db> < scripts/backfill_task_targets.sql
--
-- Inspect before/after:
--   SELECT operation, count(*) FILTER (WHERE COALESCE(target_post_id, '') = '')
--            AS empty_post_id, count(*) FROM x_account_task GROUP BY operation;

DO $$
DECLARE
    rec record;
    filled int := 0;
    skipped int := 0;
BEGIN
    FOR rec IN
        SELECT id, task_context
          FROM x_account_task
         WHERE COALESCE(target_post_id, '') = ''
           AND COALESCE(target_screen_name, '') = ''
           AND task_context ~ '^\s*\{'
    LOOP
        BEGIN
            UPDATE x_account_task
               SET target_post_id = NULLIF(COALESCE(
                       NULLIF(rec.task_context::jsonb ->> 'post_id', ''),
                       NULLIF(rec.task_context::jsonb -> 'post' ->> 'post_id', ''),
                       NULLIF(rec.task_context::jsonb ->> 'target_id', '')), ''),
                   target_screen_name = NULLIF(
                       rec.task_context::jsonb ->> 'screen_name', '')
             WHERE id = rec.id;
            filled := filled + 1;
        EXCEPTION WHEN others THEN
            skipped := skipped + 1;
            RAISE WARNING 'x_account_task %: skipped, invalid task_context (%)',
                rec.id, SQLERRM;
        END;
    END LOOP;
    RAISE NOTICE 'backfill_task_targets: % row(s) filled, % skipped',
        filled, skipped;
END $$;
