# MadarBot Bridge — Production Architecture

## Design Principles

Following Odoo 19 Enterprise internal patterns exactly:

| Odoo Pattern | Telegram Equivalent | Rationale |
|---|---|---|
| `sms.sms` (message queue) | `madarbot.telegram.message` | State machine + cron processing |
| `sms.composer` (transient) | `madarbot.telegram.composer` | Wizard pattern for composition |
| `sms.tracker` (delivery) | `madarbot.telegram.tracker` | Delivery status logging |
| `mail.message_schedule` (deferred) | `scheduled_at` field | Deferred delivery |
| `mail._notify_thread_by_*` | `_notify_thread` → queue create | No HTTP in models |
| `mailgateway message_process` | Webhook → incoming queue → cron | Async, idempotent ingestion |
| `bus.bus` precommit/postcommit | Cron-based dispatch | Transaction-safe outbound |
| `SELECT FOR UPDATE NOWAIT` | Same pattern | Concurrent cron safety |

## Core Principle

**No synchronous network calls in ORM methods.** All Telegram API communication happens in dedicated cron jobs that process queue records with proper locking.

---

## State Machines

### Outgoing Message States

```
                        ┌──────────┐
                        │  PENDING │
                        └────┬─────┘
                             │ cron picks up
                             ▼
                     ┌───────────────┐
                     │  PROCESSING   │
                     │ (FOR UPDATE)  │
                     └───┬───────┬───┘
                         │       │
                  API OK │       │ API error
                         ▼       ▼
                   ┌────────┐ ┌───────┐
                   │  SENT  │ │ ERROR │
                   └────┬───┘ └───┬───┘
                        │         │
                   ┌────▼────┐  retry_count++
                   │DELIVERED│  < max_retries?
                   │(webhook)│     │        │
                   └─────────┘  yes       no
                                  │        │
                                  ▼        ▼
                            ┌────────┐ ┌────────────┐
                            │PENDING │ │DEAD_LETTER │
                            │(retry) │ └────────────┘
                            └────────┘
```

### Incoming Message States

```
         ┌─────────┐
         │ PENDING │
         └────┬────┘
              │ cron picks up
              ▼
      ┌──────────────┐
      │  PROCESSING  │
      │ (FOR UPDATE) │
      └───┬──────┬───┘
          │      │
   success │      │ error
          ▼      ▼
    ┌─────────┐ ┌───────┐
    │PROCESSED│ │ ERROR │
    └─────────┘ └───┬───┘
                    │
               retry_count++
               < max_retries?
               │          │
              yes         no
               │          │
               ▼          ▼
          ┌────────┐ ┌────────────┐
          │PENDING │ │DEAD_LETTER │
          └────────┘ └────────────┘
```

---

## Data Model

```mermaid
classDiagram
    class MadarBotAccount {
        +Char name
        +Char token [groups=base.group_system]
        +Char username
        +Boolean active
        +Text processed_update_ids [JSON]
        +One2many channel_ids
        +One2many message_ids
    }

    class MadarBotTelegramMessage {
        +Selection direction [incoming/outgoing]
        +Selection state [pending/processing/sent/delivered/processed/error/dead_letter/cancelled]
        +Char telegram_chat_id [index, required]
        +Integer telegram_message_id [index]
        +Integer update_id [index]
        +Text body
        +Char body_mimetype
        +Many2one account_id [required]
        +Many2one guest_id
        +Many2one channel_id [index]
        +Many2one mail_message_id [index]
        +Many2one mailing_trace_id
        +Integer error_code
        +Text error_description
        +Integer retry_count
        +Integer max_retries
        +Datetime scheduled_at [index]
        +Datetime sent_at
        +Datetime delivered_at
        +Datetime processed_at
        +One2many tracker_ids
    }

    class MadarBotTelegramComposer {
        +Text body [required]
        +Many2one account_id
        +Many2many channel_ids
        +Many2many guest_ids
        +Many2one mailing_id
        +method _action_send() -> list~int~
    }

    class MadarBotTelegramTracker {
        +Many2one message_id [required]
        +Selection state
        +Integer error_code
        +Text error_description
        +Datetime tracked_at
    }

    class DiscussChannel {
        +Selection channel_type [selection_add: telegram]
        +Char telegram_chat_id
        +Many2one telegram_account_id
    }

    class MailGuest {
        +Char telegram_chat_id
        +Integer telegram_user_id
        +Char telegram_username
        +Char telegram_language_code
        +Boolean telegram_is_bot
    }

    class MailBlacklist {
        +Char telegram_user_id
        +method _add_telegram()
        +method _remove_telegram()
    }

    MadarBotAccount --> DiscussChannel : has channels
    MadarBotAccount --> MadarBotTelegramMessage : has messages
    MadarBotTelegramMessage --> MadarBotTelegramTracker : tracked by
    MadarBotTelegramMessage --> MadarBotAccount : sent via
    MadarBotTelegramMessage --> DiscussChannel : on channel
    MadarBotTelegramMessage --> MailGuest : from guest
```

---

## Sequence Diagrams

### Incoming Telegram Message

```mermaid
sequenceDiagram
    participant TG as Telegram
    participant WC as WebhookController
    participant TM as madarbot.message
    participant AC as madarbot.account
    participant CRON as Cron (30s)
    participant MG as mail.guest
    participant DC as discuss.channel
    participant MM as mail.message

    TG->>WC: POST /madarbot/webhook/{id}/{hash}
    Note over WC: Validate hash + secret token header
    WC->>AC: Check update_id idempotency
    alt Duplicate update_id
        WC-->>TG: 200 OK (ignored)
    else New update
        WC->>AC: Mark update_id as processed
        WC->>TM: create({direction:'incoming', state:'pending', ...})
        WC-->>TG: 200 OK
    end

    Note over CRON: Every 30 seconds
    CRON->>TM: search([state='pending', direction='incoming'])
    TM->>TM: SELECT ... FOR UPDATE NOWAIT
    alt Lock acquired
        TM->>TM: write(state='processing')
        TM->>MG: _get_or_create_telegram_guest()
        TM->>DC: find or create discuss.channel
        TM->>DC: message_post(body, author_guest_id, ...)
        DC->>MM: mail.message created
        DC->>DC: _notify_thread(bus dispatch only)
        TM->>TM: write(state='processed', mail_message_id, processed_at)
    else Lock failed (another cron)
        Note over TM: Skip, try next cycle
    end
```

### Outgoing Telegram Message (from Discuss)

```mermaid
sequenceDiagram
    participant USER as User
    participant DC as discuss.channel
    participant TM as madarbot.message
    participant CRON as Cron (30s)
    participant API as Telegram Bot API

    USER->>DC: message_post(body)
    DC->>DC: _notify_thread()
    DC->>DC: _notify_thread_by_telegram()
    Note over DC: NO HTTP call — only create queue record
    DC->>TM: create({direction:'outgoing', state:'pending', body, chat_id, ...})
    DC-->>USER: message posted (fast)

    Note over CRON: Every 30 seconds
    CRON->>TM: search([state='pending', direction='outgoing'])
    TM->>TM: SELECT ... FOR UPDATE NOWAIT
    alt Lock acquired
        TM->>TM: write(state='processing')
        TM->>API: POST sendMessage {chat_id, text, parse_mode}
        alt Success
            API-->>TM: {ok:true, result:{message_id: N}}
            TM->>TM: write(state='sent', telegram_message_id=N, sent_at=now)
        else Error
            API-->>TM: {ok:false, error_code: 403}
            TM->>TM: write(state='error', error_code=403, retry_count+=1)
            alt retry_count >= max_retries
                TM->>TM: write(state='dead_letter')
            end
        end
    end
```

### Outgoing Telegram Message (from Mass Mailing)

```mermaid
sequenceDiagram
    participant USER as User
    participant MM as mailing.mailing
    participant COMP as telegram.composer
    participant TM as madarbot.telegram.message
    participant CRON as Cron

    USER->>MM: action_send (mailing_type='telegram')
    MM->>COMP: open wizard (transient)
    COMP-->>USER: Show composer
    USER->>COMP: Confirm send
    COMP->>COMP: _action_send()
    loop For each recipient
        COMP->>TM: create({direction:'outgoing', state:'pending', body, chat_id, mailing_trace})
    end
    COMP-->>USER: return (fast)

    Note over CRON: Cron processes queue (same as discuss flow)
    CRON->>TM: SELECT ... FOR UPDATE NOWAIT
    TM->>API: sendMessage
    API-->>TM: ok/error
    TM->>TM: update state
    TM->>TR: create tracker record
```

---

## Migration Plan

### Phase 1: Remove Anti-patterns (before production)

1. **Remove `madarbot.telegram.channel` model** — duplicate of `discuss.channel` fields
2. **Remove dead code**: `_get_telegram_send_method`, `_set_webhook_typing`, `_get_webhook_base_url`, `webhook_secret` field
3. **Remove unused dependencies**: `mass_mailing`, `base_automation`, `approvals` from bridge manifest
4. **Remove unused imports**: `hmac`, `defaultdict`, `api`, `hashlib` (from module level)

### Phase 2: Add Queue Models

1. Create `madarbot.telegram.message` model with state machine
2. Create `madarbot.telegram.composer` (transient, like `sms.composer`)
3. Create `madarbot.telegram.tracker` model (like `sms.tracker`)
4. Add `SELECT FOR UPDATE NOWAIT` locking pattern to cron processing
5. Add `processed_update_ids` JSON field to `madarbot.account`
6. Create `_process_incoming_messages` cron (30s interval)
7. Create `_process_outgoing_messages` cron (30s interval)
8. Create `_cleanup_processed_updates` cron (daily)

### Phase 3: Refactor Controllers

1. Webhook: validate `X-Telegram-Bot-Api-Secret-Token` header
2. Webhook: create queue record only, return immediately
3. Webhook: idempotency via `update_id` stored on account
4. Remove all `sudo()` scatter — use `request.env.sudo()` once

### Phase 4: Refactor Models

1. `discuss_channel.py`: `_notify_thread` creates queue record, never calls HTTP
2. `madarbot_guest.py`: remove `_set_auth_cookie()` from webhook path
3. `mail_blacklist.py`: fix `_search` override name, or use dedicated blacklist model
4. `mailing_mailing.py`: delegate to `telegram.composer` instead of HTTP

### Phase 5: SQL Data Migration

```sql
-- No data migration needed (no production data yet)
-- If upgrading from v1 prototype:
-- ALTER TABLE madarbot_telegram_message ADD COLUMN ...
```

### Phase 6: Security Hardening

1. `madarbot.account.token` stays `groups=base.group_system`
2. Add ACLs for `mass_mailing_telegram` (currently empty)
3. Webhook endpoint has no session overhead (stateless)
4. Rate limiter uses atomic SQL UPSERT instead of TOCTOU pattern
