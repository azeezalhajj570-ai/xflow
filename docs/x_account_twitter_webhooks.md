# X Account — Official Webhooks & X Activity API (XAA)

The `x_account_twitter` module can receive DM and chat events in near‑real‑time
through X's **official V2 Webhooks API** plus **X Activity API (XAA)
subscriptions**, instead of relying only on polling.

This document covers configuration, endpoints, supported events, local
development/testing, and production deployment.

> **Scope note:** Enterprise accounts may separately use the filtered‑stream
> approach for business posts. That path stays stream‑only; **webhooks/XAA are
> for DMs and chat events** delivered to the account(s) listed below.

---

## Architecture

```
X (Twitter) ── CRC challenge ──► /x_account/twitter/webhook (GET)
        │
        │ XAA subscription event (DM / chat)  — signed POST
        ▼
/x_account/twitter/webhook (POST, auth=public, csrf=False)
        │
        ▼
TwitterActivity.ingest_webhook()
   • validate signature (x-twitter-webhooks-signature, HMAC‑SHA256)
   • route by envelope filter.user_id → social.account(x_provider=twitter)
   • dedup by event_uuid (x.twitter.event unique constraint)
   • enqueue x.account.task (operation=process_webhook_event)
        │
        ▼
cron _process_queue() ──► x.account.task worker ──► provider.process_webhook_event
        │
        ▼
TwitterActivity.process_event(event)
   • re-key idempotently (x.message.external_id, encrypted for chats)
   • retry: raises TwitterTemporaryError → task backs off (bounded by max_attempts)
   • non‑retryable: marked done‑with‑error
```

Self‑healing is handled by the cron job
`cron_x_twitter_ensure_webhook_subscriptions` (every 30 minutes,
`model_social_account._ensure_x_webhook_subscriptions()`) which registers the
webhook and XAA subscriptions if they are missing.

---

## Configuration

Set these under *Settings → General Settings → X Account*, or directly as
`ir.config_parameter`:

| Config key | Purpose |
|---|---|
| `x_account_twitter.webhook_enabled` | Master switch for webhook processing |
| `x_account_twitter.webhook_base_url` | Public HTTPS base URL of this Odoo instance (no path, no port). The receiver URL is `<base>/x_account/twitter/webhook` |
| `x_account_twitter.app_consumer_secret` | App consumer secret — keys the HMAC‑SHA256 event signatures and the CRC response |
| `x_account_twitter.app_bearer_token` | App‑only Bearer token — authorizes webhook/XAA registration |
| `social.twitter_oauth2_client_id` / `social.twitter_oauth2_client_secret` | Existing OAuth 2.0 client credentials (reused globally) |

**Secrets are never logged.** The consumer secret/bearer are used only in-memory
by the `TwitterWebhook` service.

### X Developer Console (app‑side)

1. In the **X Developer Portal**, create an OAuth 2.0 app (or reuse the existing
   one used by the OAuth accounts).
2. App access: OAuth 2.0 with `dm.read` and `dm.write` scopes.
3. Under *Webhooks*, add a webhook whose **URL** is the public
   `/x_account/twitter/webhook` endpoint and whose **Token** is the app consumer
   secret (X calls this the "webhook secret" / consumer secret).
4. Register both an **app‑only Bearer token** and read the **consumer secret**
   into the config params above.

---

## Endpoint

**`GET /x_account/twitter/webhook`** — Challenge‑Response Check (CRC).
X sends `?crc_token=...`; the handler replies
`{"response_token": "sha256=<base64(HMAC-SHA256(crc_token, consumer_secret))>"}`.

**`POST /x_account/twitter/webhook`** — Event delivery.
- `auth='public'`, `csrf=False`, `website=False` (bypasses the HTTP layer).
- Requires header **`x-twitter-webhooks-signature`** = `sha256=<base64(HMAC-SHA256(raw body, consumer_secret))>`.
- Verifies signature → `TwitterActivity.ingest_webhook` (acks fast, enqueues work).
- Invalid/missing signature → `403` with an empty body (no echo).

The controller delegates to service layers; unit tests exercise those services
directly (matching the OmniX convention) rather than literal HTTP dispatch.

---

## Supported XAA Events

| Event type | Handling |
|---|---|
| `dm.received` | Legacy (unencrypted) inbound DM → discuss channel (`_handle_dm`, `direct_message_events[]`) |
| `dm.sent` | Outbound DM → discuss channel |
| `chat.received` | Chat message received → stored encrypted (`_handle_chat`, `encoded_event` blob) |
| `chat.sent` | Chat message sent → stored encrypted |
| `chat.conversation_join` | New chat conversation → channel adoption |

- Group chats are detected via colon‑separated or `g...` conversation ids and
  their payloads are E2E‑encrypted; they are recorded (`x.message` with
  `encrypted=True`) but no plaintext is stored.
- `post.create` and other stream‑only events are ignored.
- Events for a `user_id` with no linked `social.account(x_provider='twitter')`
  are logged and skipped.

Envelope shape consumed: `data.{event_uuid, event_type, filter.user_id, payload,
includes}`; dedup key is `event_uuid` (enforced by the `x.twitter.event`
`event_uuid` unique constraint and an ingest‑time `search_count` check).

---

## Datastore

- `x.twitter.webhook` — registered webhook (+ app URL, state).
- `x.twitter.subscription` — per‑account/per‑event XAA subscription.
- `x.twitter.event` — inbound event record (dedup by `event_uuid`, lifecycle
  `queued → processing → done|failed|skipped`).
- `x.account.task` — durable queue entries (`operation=process_webhook_event`)
  drained by the existing cron worker.
- `x.message` — persisted DM/chat messages (idempotent by `external_id`).

Outbound sends through the OAuth account path are unchanged.

---

## Local Development & Testing

Run the module test suite (fresh test DB; HTTP disabled):

```bash
./scripts/run-tests.sh x_account_twitter
```

Covered by `x_account_twitter/tests/test_twitter_webhook.py`:

- `TestTwitterWebhookService` — HMAC signature verify, CRC response, webhook
  register/list/delete, subscription lifecycle (mocked `requests.request`).
- `TestTwitterActivityIngest` — routing by `filter.user_id`, dedup, unknown/missing
  ids, no‑account skip.
- `TestTwitterActivityProcess` — DM in/out persistence, encrypted chat storage,
  idempotent re‑processing, retryable (`TwitterTemporaryError` re‑raised) and
  non‑retryable (marks done‑with‑error) paths.

> **Note:** to test against *live* X, the receiver URL must be publicly reachable
> over HTTPS with no proxy port. For local Odoo this usually requires a tunnel
> (e.g. `ngrok http 8069`) because X will not deliver to `localhost`.

Regression: `./scripts/run-tests.sh x_account`.

---

## Production Deployment

1. Verify the settings in **X Developer Portal** and in Odoo *Settings* match
   (URL, consumer secret, bearer token).
2. Enable `x_account_twitter.webhook_enabled`.
3. Install/upgrade the module so the 30‑minute self‑heal cron is created:
   ```bash
   docker compose exec odoo /entrypoint.sh odoo server \
     -d <db> -i x_account_twitter --stop-after-init --workers=0 --http-port=18069
   ```
   (For an already‑installed module use `-u x_account_twitter`.)
4. Confirm the webhook is registered:
   ```bash
   docker compose exec odoo /entrypoint.sh odoo server \
     -d <db> -u x_account_twitter --stop-after-init --workers=0 --http-port=18069 \
     --logfile=/var/log/odoo/x_webhook_check.log
   ```
   or call `XService.get_provider('twitter').register_webhook(safe=True)` from a
   shell.
5. Restart the Odoo containers:
   ```bash
   docker compose restart odoo
   ```
6. Watch inbound events land in the discuss channels; tail the queue worker logs
   for retries.

Enterprise filtered‑stream business posts continue to flow through the existing
stream integration (webhooks do not replace that path).
