# Evaluation: whatsapp_addons (Evolution API) vs Official WhatsApp Module

## Executive Summary

The custom `whatsapp_evaluation` module uses **Evolution API** (a WhatsApp Business API proxy) instead of Meta's official Cloud API. While functional for basic messaging, it **lacks critical patterns** needed for AI integration and marketing automation compatibility.

**Verdict:** Requires significant refactoring to match the official module's architecture before AI integration can proceed.

---

## Model Comparison

| Official Model | Custom Model | Status | Action Required |
|----------------|--------------|--------|-----------------|
| `whatsapp.account` | `whatsapp_evaluation.account` | ⚠️ Different name | Rename or create alias |
| `whatsapp.message` | `whatsapp_evaluation.message` | ⚠️ Different name | Rename or create alias |
| `whatsapp.template` | `whatsapp_evaluation.template` | ✅ Exists | Review compatibility |
| `whatsapp.template.button` | ❌ Missing | ❌ Not implemented | Implement if needed |
| `whatsapp.template.variable` | `whatsapp_evaluation.template.variable` | ✅ Exists | Review compatibility |
| `whatsapp.composer` | ❌ Missing | ❌ Not implemented | Implement for mass sending |

---

## Field Comparison: discuss.channel

| Field | Official | Custom | Status |
|-------|----------|--------|--------|
| `channel_type` selection_add | ✅ `('whatsapp', 'WhatsApp Conversation')` | ✅ Same | OK |
| `whatsapp_number` | ✅ Char | ✅ Char | OK |
| `wa_account_id` | ✅ Many2one('whatsapp.account') | ⚠️ Many2one('whatsapp_evaluation.account') | Model name differs |
| `whatsapp_partner_id` | ✅ Many2one('res.partner') | ✅ Same | OK |
| `last_wa_mail_message_id` | ✅ Many2one('mail.message') | ❌ Missing | **Required for AI** |
| `whatsapp_channel_valid_until` | ✅ Datetime (computed) | ⚠️ Datetime (not computed) | Fix computation |
| `whatsapp_channel_active` | ✅ Boolean (computed) | ❌ Missing | **Required** |

---

## Field Comparison: whatsapp.message

| Field | Official | Custom | Status |
|-------|----------|--------|--------|
| `mobile_number` | ✅ Char | ✅ Char | OK |
| `mobile_number_formatted` | ✅ Char (computed) | ❌ Missing | **Required** |
| `message_type` | ✅ Selection (outbound/inbound) | ✅ Same | OK |
| `state` | ✅ Selection (8 states) | ⚠️ Selection (6 states) | Missing: replied, bounced, cancel |
| `failure_type` | ✅ Selection (9 types) | ❌ Missing | **Required** |
| `failure_reason` | ✅ Char | ✅ Char | OK |
| `wa_template_id` | ✅ Many2one | ❌ Missing | Optional |
| `msg_uid` | ✅ Char | ✅ Char | OK |
| `wa_account_id` | ✅ Many2one | ✅ Many2one | OK |
| `parent_id` | ✅ Many2one (self) | ❌ Missing | **Required for replies** |
| `mail_message_id` | ✅ Many2one | ✅ Many2one | OK |
| `body` | ✅ Html (related) | ✅ Html | OK |
| `free_text_json` | ✅ Json | ❌ Missing | Optional |

---

## Field Comparison: mail.message

| Field | Official | Custom | Status |
|-------|----------|--------|--------|
| `message_type` selection_add | ✅ `('whatsapp_message', 'WhatsApp')` | ✅ Same | OK |
| `wa_message_ids` | ✅ One2many('whatsapp.message') | ⚠️ One2many('whatsapp_evaluation.message') | Model name differs |

---

## Critical Missing Functionality

### 1. discuss.channel.message_post() Override

**Official Pattern:**
```python
def message_post(self, *args, body='', message_type='notification', parent_id=False, **kwargs):
    # Handle inbound WhatsApp messages
    if message_type == 'whatsapp_message' and self.channel_type == 'whatsapp':
        # Create whatsapp.message record in _notify_thread
        # Handle parent_id for replies
        # Send via WhatsApp API for outbound
```

**Custom Implementation:**
```python
def message_post(self, *args, **kwargs):
    # Only creates whatsapp.message for OUTBOUND messages
    # Does NOT handle inbound message_type='whatsapp_message'
    # Does NOT handle parent_id for replies
```

**Impact:** AI integration cannot work without proper inbound message handling.

---

### 2. discuss.channel._notify_thread() Override

**Official Pattern:**
```python
def _notify_thread(self, message, msg_vals=False, **kwargs):
    if kwargs.get('whatsapp_inbound_msg_uid') and self.channel_type == 'whatsapp':
        # Create whatsapp.message record for inbound
        self.env['whatsapp.message'].create({
            'mail_message_id': message.id,
            'message_type': 'inbound',
            'msg_uid': kwargs['whatsapp_inbound_msg_uid'],
            'state': 'received',
            ...
        })
```

**Custom Implementation:** ❌ **Not implemented**

**Impact:** Inbound messages don't get proper whatsapp.message records.

---

### 3. discuss.channel._get_whatsapp_channel() Signature

**Official:**
```python
def _get_whatsapp_channel(self, whatsapp_number, wa_account_id, sender_name=False, 
                          create_if_not_found=False, related_message=False):
```

**Custom:**
```python
def _get_whatsapp_channel(self, whatsapp_number, wa_account_id, partner=None, 
                          create_if_not_found=False):
```

**Impact:** Missing `sender_name` and `related_message` parameters breaks compatibility.

---

### 4. Controller Webhook Processing

**Official Pattern:**
```python
# In whatsapp.account._process_messages()
channel.message_post(
    message_type='whatsapp_message',
    author_id=channel.whatsapp_partner_id.id,
    whatsapp_inbound_msg_uid=messages['id'],
    ...
)
```

**Custom Implementation:**
```python
# In controller directly
new_msg = channel.with_context(whatsapp_inbound_msg_uid=...).message_post(
    body=formatted_body,
    author_id=author_id,
    message_type='comment',  # ❌ Should be 'whatsapp_message'
    ...
)
# Then manually creates whatsapp_evaluation.message
```

**Impact:** Using `message_type='comment'` instead of `'whatsapp_message'` breaks AI detection logic.

---

### 5. Missing Helper Methods

| Method | Official | Custom | Impact |
|--------|----------|--------|--------|
| `_find_active_channel()` | ✅ | ❌ | Cannot find channels for marketing automation |
| `_process_messages()` | ✅ | ❌ | No centralized message processing |
| `_compute_whatsapp_channel_valid_until()` | ✅ | ❌ | Channel validity not computed |
| `_compute_whatsapp_channel_active()` | ✅ | ❌ | Cannot filter active channels |
| `_compute_mobile_number_formatted()` | ✅ | ❌ | Phone validation missing |

---

## Required Changes for AI Integration

### Priority 1: Critical (Must Fix)

1. **Rename models** or create compatibility aliases:
   - `whatsapp_evaluation.account` → `whatsapp.account`
   - `whatsapp_evaluation.message` → `whatsapp.message`

2. **Fix message_post()** to use `message_type='whatsapp_message'` for inbound

3. **Add missing fields** to discuss.channel:
   - `last_wa_mail_message_id`
   - `whatsapp_channel_active` (computed)

4. **Add missing fields** to whatsapp.message:
   - `mobile_number_formatted`
   - `parent_id`
   - `failure_type`

5. **Implement _notify_thread()** override for proper inbound message handling

### Priority 2: Important (Should Fix)

6. **Move message processing** from controller to `whatsapp.account._process_messages()`

7. **Fix _get_whatsapp_channel()** signature to match official

8. **Add computed fields** for channel validity

9. **Add phone number formatting** using phone_validation

### Priority 3: Nice to Have

10. Add `whatsapp.composer` for mass sending
11. Add `whatsapp.template.button` for interactive templates
12. Add marketing_automation compatibility

---

## Recommended Implementation Plan

### Phase 1: Model Alignment (1-2 days)
- Rename models to match official naming
- Add missing fields
- Fix computed fields

### Phase 2: Message Flow Alignment (1 day)
- Fix message_post() override
- Implement _notify_thread() override
- Move processing logic to account model

### Phase 3: Controller Alignment (0.5 day)
- Update controller to use new patterns
- Fix message_type usage

### Phase 4: AI Integration (Proceed with original plan)
- Once models align, proceed with ai_whatsapp module

---

## Alternative: Compatibility Layer

Instead of renaming everything, create a compatibility module:

```python
# whatsapp_compat/models/whatsapp_account.py
class WhatsAppAccount(models.Model):
    _name = 'whatsapp.account'
    _inherit = 'whatsapp_evaluation.account'
    # Alias to existing model
```

This allows both modules to coexist while providing the expected model names for AI integration.

---

## Decision Required

**Option A:** Refactor `whatsapp_evaluation` to match official naming and patterns
- **Pros:** Clean, future-proof, compatible with other modules
- **Cons:** Breaking change, requires data migration

**Option B:** Create compatibility layer (`whatsapp_compat`)
- **Pros:** No breaking changes, backward compatible
- **Cons:** Extra module, potential confusion

**Option C:** Modify `ai_whatsapp` to work with `whatsapp_evaluation` naming
- **Pros:** No changes to existing module
- **Cons:** Diverges from official pattern, harder to maintain

**Recommendation:** Option A (Refactor) — cleaner long-term solution.
