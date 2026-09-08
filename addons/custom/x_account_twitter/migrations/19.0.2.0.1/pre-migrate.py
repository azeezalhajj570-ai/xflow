def migrate(cr, version):
    cr.execute("""
        ALTER TABLE x_twitter_event
        DROP CONSTRAINT IF EXISTS x_twitter_event_event_uuid_uniq
    """)
    cr.execute("""
        ALTER TABLE x_twitter_event
        ADD CONSTRAINT x_twitter_event_account_id_event_uuid_uniq
        UNIQUE (account_id, event_uuid)
    """)
