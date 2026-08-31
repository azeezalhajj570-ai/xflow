# Spec: AI WhatsApp — AI Agent Auto-Reply for WhatsApp Conversations

## Objective

Enable AI agents to automatically respond to incoming WhatsApp messages, mirroring how `ai_livechat` works for website live chat. When a customer sends a WhatsApp message, the assigned AI agent generates and sends a reply using the LLM, with support for human takeover.

**Who:** Businesses using Odoo WhatsApp + AI modules who want automated AI-powered customer support on WhatsApp.

**Success looks like:** A customer sends a WhatsApp message → AI agent responds within seconds with a contextually relevant answer → human operator can take over the conversation at any point.

## Tech Stack

- **Framework:** Odoo 19 (Python ORM, XML views)
- **Dependencies:** `whatsapp` (Enterprise), `ai` (base AI module), `mail`, `discuss`
- **Key models inherited:** `ai.agent`, `discuss.channel`, `whatsapp.account`
- **Pattern:** Follow `ai_livechat` module architecture exactly

## Commands

```
Install: ./odoo-bin -u ai_whatsapp -d <database>
Test: python -m pytest addons/custom/ai_whatsapp/tests/
Lint: ruff check addons/custom/ai_whatsapp/
Dev: ./odoo-bin -d <database> --dev=all
```

## Project Structure

```
addons/custom/ai_whatsapp/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── ai_agent.py              # Extend ai.agent with WhatsApp fields + preprompt
│   ├── discuss_channel.py       # Hook AI response on incoming WhatsApp messages
│   └── whatsapp_account.py      # Add ai_agent_id to WhatsApp account
├── data/
│   └── ai_agent_data.xml        # Default WhatsApp AI agent record
├── views/
│   └── whatsapp_account_views.xml  # Add AI agent field to WhatsApp account form
├── tests/
│   ├── __init__.py
│   └── test_ai_whatsapp.py      # Tests for AI response flow
└── security/
    └── ir.model.access.csv      # Access rights if new models added
```

## Code Style

Follow `ai_livechat` conventions exactly. Example from the pattern:

```python
from textwrap import dedent
from odoo import fields, models

PREPROMPTS = {
    'whatsapp': dedent("""
        - You are a WhatsApp customer support agent. Keep responses short and concise.
        - WhatsApp messages have a character limit preference — be brief.
        - Do not use markdown formatting — WhatsApp does not render it.
        - Use plain text with simple line breaks for structure.
    """).strip(),
}

class AIAgent(models.Model):
    _inherit = 'ai.agent'

    whatsapp_account_ids = fields.One2many(
        comodel_name='whatsapp.account',
        inverse_name='ai_agent_id',
    )

    def _is_user_access_allowed(self):
        return super()._is_user_access_allowed() or self.whatsapp_account_ids

    def _build_system_context(self, extra_system_context: str = ""):
        messages = super()._build_system_context(extra_system_context)
        discuss_channel = self.env.context.get('discuss_channel', self.env['discuss.channel'])
        if discuss_channel.channel_type == 'whatsapp':
            messages.append(PREPROMPTS['whatsapp'])
        return messages
```

## Testing Strategy

- **Framework:** Odoo `TransactionCase`
- **Location:** `tests/test_ai_whatsapp.py`
- **Coverage:** 
  - AI agent assignment to WhatsApp account
  - Incoming message triggers AI response
  - Response is posted to the correct discuss channel
  - Human takeover stops AI responses
  - AI agent system context includes WhatsApp pre-prompt
- **Verify:** `python -m pytest addons/custom/ai_whatsapp/tests/`

## Boundaries

- **Always:**
  - Follow `ai_livechat` patterns exactly
  - Use `_inherit` (model extension), never create new base models
  - Use `sudo()` appropriately for public-facing WhatsApp webhook flows
  - Test with `message_type='whatsapp_message'`
  - Handle the case where `whatsapp` module fields may not exist gracefully

- **Ask first:**
  - Adding new dependencies beyond `whatsapp`, `ai`, `mail`
  - Changing the `discuss.channel.message_post()` override chain
  - Modifying the AI response generation flow
  - Adding new fields to `whatsapp.account` (Enterprise model)

- **Never:**
  - Modify Enterprise module source code directly
  - Bypass the existing `_generate_response_for_channel()` method
  - Store API keys or secrets in code
  - Remove or alter existing `marketing_automation_whatsapp` behavior

## Architecture — How It Works

### WhatsApp Module Structure (from container)

**Key Models:**
- `whatsapp.account` — WhatsApp Business Account (App ID, token, phone number, webhook config)
- `whatsapp.message` — Individual WhatsApp messages (inbound/outbound, state tracking)
- `discuss.channel` — Extended with `channel_type='whatsapp'`, `whatsapp_number`, `wa_account_id`, `whatsapp_partner_id`
- `mail.message` — Extended with `message_type='whatsapp_message'`, `wa_message_ids` (One2many to whatsapp.message)

**Message Flow (existing):**
1. Meta webhook → `/whatsapp/webhook/` controller
2. Controller calls `whatsapp.account._process_messages(value)`
3. `_process_messages()` finds/creates `discuss.channel` via `_get_whatsapp_channel()`
4. Channel calls `message_post()` with `message_type='whatsapp_message'` and `whatsapp_inbound_msg_uid`
5. `discuss.channel.message_post()` override creates `whatsapp.message` record in `_notify_thread()`
6. Outbound messages: `message_post()` creates `whatsapp.message` and calls `_send_message()` to Meta API

### AI WhatsApp Message Flow (new)

```
Incoming WhatsApp message
  └── Meta Webhook → /whatsapp/webhook/ controller
      └── whatsapp.account._process_messages()
          └── discuss.channel.message_post(message_type='whatsapp_message', whatsapp_inbound_msg_uid=...)
              └── ai_whatsapp override of message_post()
                  ├── Checks: channel_type == 'whatsapp'
                  ├── Checks: message is incoming (author_id == whatsapp_partner_id)
                  ├── Checks: AI agent is assigned to wa_account_id
                  ├── Checks: no human operator has taken over (ai_agent_id not cleared)
                  └── Calls: ai_agent._generate_response_for_channel(mail_message, channel)
                      └── AI generates response via LLM
                          └── _post_ai_response() posts reply to channel
                              └── WhatsApp module sends reply via Meta API
```

### Key Design Decisions

1. **Trigger point:** Override `discuss.channel.message_post()` — check for `message_type='whatsapp_message'` and `author_id == whatsapp_partner_id` (incoming message)
2. **AI agent assignment:** Add `ai_agent_id` field to `whatsapp.account` model
3. **Human takeover:** Add `ai_agent_id` field to `discuss.channel` for WhatsApp channels; set to False when human takes over
4. **Channel type:** Use existing `channel_type='whatsapp'` — no new type needed
5. **Response format:** Plain text (use `html2plaintext()` on AI response before posting)
6. **Avoid loops:** Check that the message author is the WhatsApp partner (not the AI agent's partner)

### Differences from ai_livechat

| Aspect | ai_livechat | ai_whatsapp |
|--------|-------------|-------------|
| Trigger | HTTP endpoint `/im_livechat/get_session` | `message_post()` override on incoming WA message |
| Channel creation | Created on session start | Already exists (created by whatsapp module) |
| Routing rules | `im_livechat.channel.rule` with URL matching | `whatsapp.account` with `ai_agent_id` |
| Forward to human | Controller endpoint `/ai_livechat/forward_operator` | Set `ai_agent_id = False` on channel |
| Response format | HTML (rendered in web widget) | Plain text (WhatsApp format) |
| Chatbot fallback | Can forward to scripted chatbot | No scripted chatbot for WhatsApp |
| Message identification | `message_type='comment'` | `message_type='whatsapp_message'` |

## Success Criteria

- [ ] AI agent can be assigned to a WhatsApp account
- [ ] Incoming WhatsApp message triggers AI response automatically
- [ ] AI response is sent back via WhatsApp (appears in discuss channel + delivered to customer)
- [ ] Human operator can take over (AI stops responding)
- [ ] AI uses WhatsApp-specific system prompt (short, plain text responses)
- [ ] Module installs without errors alongside existing `whatsapp` + `ai` modules
- [ ] Existing `marketing_automation_whatsapp` behavior is not affected
- [ ] Tests pass for all core scenarios

## Open Questions

1. **Should there be a "working hours" concept** — should AI only respond outside business hours, or always?
2. **Should AI responses be limited by conversation length** — e.g., after N back-and-forth, escalate to human?
3. **Rate limiting** — should there be a cooldown to prevent AI from responding too fast to rapid messages?
4. **What about media messages** (images, voice notes, documents) — should AI acknowledge them or ignore?
5. **Should the AI agent be able to use tools** (like creating CRM leads) during WhatsApp conversations?
