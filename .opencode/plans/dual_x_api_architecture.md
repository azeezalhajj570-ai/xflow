# Dual X API Architecture Implementation Plan

## Executive Summary

This plan implements a dual-provider architecture for XFlow where:
- **Official X API** (x_account_twitter) handles events: OAuth, webhooks, subscriptions, incoming DMs
- **GetXAPI** (x_account_getxapi) handles actions: retweet, reply, like, follow, send_dm, etc.

The architecture maintains backward compatibility while enabling the split responsibility model.

---

## Current Architecture Analysis

### Existing Providers

| Provider | Module | Auth | Webhooks | Actions | Events |
|----------|--------|------|----------|---------|--------|
| `twitter` | x_account_twitter | OAuth 2.0 | Yes (X Activity API) | Yes | Yes |
| `omnix` | x_account_omnix | Session + API key | Yes (per-account) | Yes | Yes |
| `getxapi` | x_account_getxapi | API key | No | Yes | No |
| `session_web` | x_account | Session cookies | No | Yes | Yes |
| `official_publish` | x_account | OAuth 1.0a | No | Limited | No |

### Current Dispatch Pattern

```python
provider = XService.get_provider(account)  # Returns ONE provider
provider.like(post)
provider.repost(post)
provider.comment(post, text)
```

All operations route through a single provider determined by `account.x_provider`.

---

## Target Architecture

```
                    XFlow
                      │
                social.account
                      │
             ┌────────┴────────┐
             │                 │
      get_event_provider()  get_action_provider()
             │                 │
      Official X API        GetXAPI
             │                 │
       Webhooks             Retweet
       Subscriptions        Reply
       Incoming events      Like
       DM events            Post
       Account events       Follow
             │              DM Send
             │                 │
             └────────┬────────┘
                      │
                 XFlow State
```

---

## Implementation Phases

### Phase 1: Provider Split Abstraction

**Objective:** Add `get_event_provider()` and `get_action_provider()` methods to `social.account` without changing behavior.

**Files to modify:**
- `x_account/models/social_account.py`

**Changes:**

1. Add two new methods to `social.account`:

```python
def get_event_provider(self):
    """Return the provider for event handling (webhooks, subscriptions, incoming events)."""
    self.ensure_one()
    # For now, return the same as get_provider() for backward compatibility
    # Phase 5 will implement the actual routing logic
    from odoo.addons.x_account.services.x_service import XService
    return XService.get_provider(self)

def get_action_provider(self):
    """Return the provider for action handling (retweet, reply, like, etc.)."""
    self.ensure_one()
    # For now, return the same as get_provider() for backward compatibility
    # Phase 4 will implement the actual routing logic
    from odoo.addons.x_account.services.x_service import XService
    return XService.get_provider(self)
```

2. Keep `XService.get_provider(account)` unchanged for backward compatibility.

**Testing:**
- Verify existing functionality is unchanged
- Both methods return the same provider as before

---

### Phase 2: Extend social.account for Dual Providers

**Objective:** Add fields to support dual provider configuration.

**Files to modify:**
- `x_account/models/social_account.py`
- `x_account/views/social_account_views.xml`

**Changes:**

1. Add new fields to `social.account`:

```python
# Dual provider configuration
x_event_provider = fields.Selection([
    ('twitter', 'Official X API'),
    ('omnix', 'OmniX'),
], string='Event Provider', help='Provider for webhooks, subscriptions, and incoming events')

x_action_provider = fields.Selection([
    ('getxapi', 'GetXAPI'),
    ('twitter', 'Official X API'),
    ('omnix', 'OmniX'),
    ('session_web', 'Session Web'),
], string='Action Provider', help='Provider for actions (retweet, reply, like, etc.)')

# GetXAPI-specific fields (if not already present)
x_getxapi_enabled = fields.Boolean('GetXAPI Enabled', default=False)
# x_getxapi_auth_token already exists in x_account_getxapi
```

2. Add computed field for backward compatibility:

```python
@property
def _effective_event_provider(self):
    """Return the effective event provider code."""
    return self.x_event_provider or self.x_provider

@property
def _effective_action_provider(self):
    """Return the effective action provider code."""
    return self.x_action_provider or self.x_provider
```

3. Update views to show the new fields (optional, can be in settings).

**Migration:**
- Existing accounts keep `x_provider` as the single provider
- New accounts can optionally set `x_event_provider` and `x_action_provider`

---

### Phase 3: Implement Provider Interfaces

**Objective:** Define clear interfaces for event and action providers.

**Files to create/modify:**
- `x_account/services/x_provider.py` (add interfaces)
- `x_account_twitter/services/twitter_provider.py` (ensure it implements event interface)
- `x_account_getxapi/services/getxapi_provider.py` (ensure it implements action interface)

**Changes:**

1. Add interface documentation to `XProvider`:

```python
class XProvider:
    """Base class for X providers.
    
    Providers can implement one or both of these interfaces:
    - XEventProvider: Webhooks, subscriptions, incoming events
    - XActionProvider: Actions (retweet, reply, like, etc.)
    """
    
    # Event provider capabilities
    _supports_events = False
    _supports_webhooks = False
    _supports_subscriptions = False
    
    # Action provider capabilities
    _supports_actions = False
    _supported_actions = ()  # Tuple of supported action names
```

2. Update `TwitterProvider`:

```python
class TwitterProvider(XProvider):
    _provider_code = 'twitter'
    _supports_events = True
    _supports_webhooks = True
    _supports_subscriptions = True
    _supports_actions = True
    _supported_actions = ('validate_session', 'like', 'comment', 'repost', 'follow',
                          'fetch_groups', 'fetch_group_messages', 'get_dms',
                          'process_webhook_event', 'register_webhook',
                          'unsubscribe_all_events', 'delete_webhook_registration')
```

3. Update `GetXAPIProvider`:

```python
class GetXAPIProvider(XProvider):
    _provider_code = 'getxapi'
    _supports_events = False
    _supports_webhooks = False
    _supports_subscriptions = False
    _supports_actions = True
    _supported_actions = ('validate_session', 'like', 'comment', 'repost', 'follow',
                          'post_tweet', 'send_dm', 'get_dms', 'fetch_groups',
                          'fetch_group_messages')
```

---

### Phase 4: Route Actions to GetXAPI

**Objective:** Implement action routing to GetXAPI when configured.

**Files to modify:**
- `x_account/models/social_account.py`
- `x_account/services/x_service.py`

**Changes:**

1. Add helper to `XService`:

```python
# In x_service.py
@staticmethod
def get_provider_by_code(account, provider_code):
    """Return a provider instance for a specific provider code."""
    account.ensure_one()
    provider_cls = XProviderRegistry.resolve(provider_code)
    if provider_cls is None:
        raise RuntimeError(f'No X provider registered for {provider_code}')
    return provider_cls(account.env, account)
```

2. Update `get_action_provider()`:

```python
def get_action_provider(self):
    """Return the provider for action handling."""
    self.ensure_one()
    from odoo.addons.x_account.services.x_service import XService
    
    action_provider_code = self.x_action_provider or self.x_provider
    
    # Special case: if using Official X API but GetXAPI is enabled, use GetXAPI for actions
    if self.x_provider == 'twitter' and self.x_getxapi_enabled:
        action_provider_code = 'getxapi'
    
    return XService.get_provider_by_code(self, action_provider_code)
```

**Testing:**
- Verify actions route to GetXAPI when `x_getxapi_enabled=True`
- Verify actions route to Official X API when `x_getxapi_enabled=False`
- Verify backward compatibility for existing accounts

---

### Phase 5: Route Events to Official X API

**Objective:** Implement event routing to Official X API.

**Files to modify:**
- `x_account/models/social_account.py`

**Changes:**

1. Update `get_event_provider()`:

```python
def get_event_provider(self):
    """Return the provider for event handling."""
    self.ensure_one()
    from odoo.addons.x_account.services.x_service import XService
    
    event_provider_code = self.x_event_provider or self.x_provider
    
    # For events, we prefer Official X API if available
    if self.x_provider in ('getxapi', 'session_web'):
        # If the account has Official X API credentials, use it for events
        if self.x_oauth2_access_token or self.twitter_oauth_token:
            event_provider_code = 'twitter'
    
    return XService.get_provider_by_code(self, event_provider_code)
```

**Testing:**
- Verify events route to Official X API when credentials are available
- Verify backward compatibility for existing accounts

---

### Phase 6: Update Task Queue

**Objective:** Update the task queue to use the appropriate provider.

**Files to modify:**
- `x_account/models/account_task.py`

**Changes:**

1. Update task execution to use the appropriate provider:

```python
def _execute_task(self):
    """Execute a single task."""
    account = self.account_id
    operation = self.operation
    
    # Determine which provider to use based on operation type
    event_operations = ('process_webhook_event', 'register_webhook', 
                        'validate_webhook_registration', 'unsubscribe_all_events',
                        'delete_webhook_registration')
    
    if operation in event_operations:
        provider = account.get_event_provider()
    else:
        provider = account.get_action_provider()
    
    # Execute the operation
    fn = getattr(provider, operation, None)
    if fn is None:
        raise ValueError(f'Provider does not support operation: {operation}')
    
    ctx = self.context or {}
    result = fn(**{k: v for k, v in ctx.items() if k != 'self'})
    return result
```

**Testing:**
- Verify action operations use action provider
- Verify event operations use event provider

---

### Phase 7: Update Business Logic Call Sites

**Objective:** Update all business logic to use the appropriate provider.

**Files to modify:**
- `x_account_getxapi/models/social_live_post.py`
- `x_account/models/discuss_channel.py`
- Any other call sites

**Changes:**

1. Update `social_live_post.py`:

```python
def _post_twitter(self):
    account = self.account_id
    provider = account.get_action_provider()  # Use action provider
    result = provider.post_tweet(self.message)
    # ...
```

2. Update `discuss_channel.py`:

```python
def action_fetch_dms(self, account, limit=50):
    provider = account.get_event_provider()  # Use event provider for DM reads
    get_dms = getattr(provider, 'get_dms', None)
    # ...
```

**Testing:**
- Verify all call sites use the appropriate provider

---

### Phase 8: Add Tests

**Objective:** Comprehensive test coverage for dual provider architecture.

**Files to create:**
- `x_account/tests/test_dual_provider.py`

**Test cases:**

1. **Provider routing tests:**
   - Test `get_event_provider()` returns correct provider
   - Test `get_action_provider()` returns correct provider
   - Test backward compatibility when dual provider fields are not set

2. **Action routing tests:**
   - Test actions route to GetXAPI when enabled
   - Test actions route to Official X API when GetXAPI is disabled
   - Test each action method (retweet, reply, like, follow, send_dm, etc.)

3. **Event routing tests:**
   - Test events route to Official X API
   - Test webhook registration uses event provider
   - Test subscription management uses event provider

4. **Credential isolation tests:**
   - Test GetXAPI credentials are not used for Official X API calls
   - Test Official X API credentials are not used for GetXAPI calls
   - Test credential fields remain separate

5. **Failure scenario tests:**
   - Test GetXAPI unavailable (actions fail clearly)
   - Test Official X API token expiration (events fail clearly)
   - Test one provider failure doesn't corrupt the other

6. **Cost tracking tests:**
   - Test GetXAPI costs are tracked
   - Test retweet cost is $0.001
   - Test DM cost is $0.002

---

### Phase 9: Verify Credential Isolation

**Objective:** Ensure credentials never cross providers.

**Audit checklist:**

1. **GetXAPI client:**
   - Verify it only uses `x_account.getxapi_api_key` (global) and `x_getxapi_auth_token` (per-account)
   - Verify it never accesses OAuth tokens

2. **Official X API client:**
   - Verify it only uses OAuth tokens (`x_oauth2_access_token`, `x_oauth2_refresh_token`)
   - Verify it never accesses GetXAPI credentials

3. **Provider instantiation:**
   - Verify `get_event_provider()` only passes Official X API credentials
   - Verify `get_action_provider()` only passes GetXAPI credentials

4. **Logging:**
   - Verify logs don't contain credentials from the wrong provider

---

## Migration Strategy

### For Existing Accounts

1. **Accounts with `x_provider='twitter'`:**
   - Continue to work as before (both events and actions use Official X API)
   - Optionally enable GetXAPI for actions by setting `x_getxapi_enabled=True`

2. **Accounts with `x_provider='getxapi'`:**
   - Continue to work as before (both events and actions use GetXAPI)
   - Note: GetXAPI doesn't support webhooks, so events won't work
   - Recommendation: Migrate to dual provider setup

3. **Accounts with `x_provider='omnix'`:**
   - Continue to work as before
   - No changes required

### For New Accounts

1. **Recommended setup:**
   - `x_provider='twitter'` (for backward compatibility)
   - `x_getxapi_enabled=True` (to use GetXAPI for actions)
   - This gives: Events → Official X API, Actions → GetXAPI

2. **Alternative setup:**
   - `x_event_provider='twitter'`
   - `x_action_provider='getxapi'`
   - Explicit dual provider configuration

---

## Acceptance Criteria

- [ ] `social.account` remains the single X account model
- [ ] Official X API remains responsible for OAuth
- [ ] Official X API remains responsible for webhooks
- [ ] Official X API remains responsible for event subscriptions
- [ ] Official X API remains responsible for incoming events
- [ ] GetXAPI becomes the action provider (when enabled)
- [ ] Retweet uses GetXAPI (when enabled)
- [ ] Reply/comment uses GetXAPI (when enabled)
- [ ] Like uses GetXAPI (when enabled)
- [ ] Create Tweet uses GetXAPI (when enabled)
- [ ] Edit Tweet uses GetXAPI (when enabled)
- [ ] Follow uses GetXAPI (when enabled)
- [ ] Unfollow uses GetXAPI (when enabled)
- [ ] Send DM uses GetXAPI (when enabled)
- [ ] GetXAPI costs are tracked
- [ ] Retweet cost is recorded as $0.001/call
- [ ] DM cost is recorded as $0.002/call
- [ ] No unnecessary paid API calls are introduced
- [ ] Mutation timeout retries are disabled (already implemented in GetXAPI client)
- [ ] Official X credentials and GetXAPI credentials remain isolated
- [ ] Existing X Chat encryption/decryption remains unchanged
- [ ] Existing webhook processing remains unchanged
- [ ] Existing subscription lifecycle remains unchanged
- [ ] No duplicate X account model is introduced
- [ ] Existing XFlow business logic doesn't need to know which provider is used
- [ ] All tests pass

---

## Risk Mitigation

1. **Backward compatibility:**
   - Existing accounts continue to work without changes
   - Dual provider is opt-in via `x_getxapi_enabled` flag

2. **Credential isolation:**
   - Separate fields for each provider's credentials
   - Provider instantiation only passes relevant credentials

3. **Failure isolation:**
   - One provider's failure doesn't affect the other
   - Clear error messages when a provider is not configured

4. **Testing:**
   - Comprehensive test coverage for all scenarios
   - Integration tests with mocked API responses

5. **Monitoring:**
   - GetXAPI cost tracking already implemented
   - Can monitor API spend per provider

---

## Open Questions

1. **Should we deprecate `x_provider` field?**
   - Recommendation: Keep it for backward compatibility, but encourage use of `x_event_provider` and `x_action_provider`

2. **What if an account has GetXAPI enabled but no Official X API credentials?**
   - Recommendation: Fall back to GetXAPI for events (but note GetXAPI doesn't support webhooks)
   - Or: Require Official X API credentials for event provider

3. **Should we support OmniX as an event provider?**
   - Recommendation: Yes, OmniX supports webhooks and can be used for events
   - But the spec focuses on Official X API for events

4. **How to handle accounts that want to use Official X API for actions?**
   - Recommendation: Allow `x_action_provider='twitter'` explicitly
   - Or: If `x_getxapi_enabled=False`, use Official X API for actions

---

## Implementation Order

1. Phase 1: Provider split abstraction (1-2 days)
2. Phase 2: Extend social.account fields (1 day)
3. Phase 3: Provider interfaces (1 day)
4. Phase 4: Route actions to GetXAPI (2-3 days)
5. Phase 5: Route events to Official X API (1-2 days)
6. Phase 6: Update task queue (1 day)
7. Phase 7: Update business logic (1-2 days)
8. Phase 8: Add tests (2-3 days)
9. Phase 9: Verify credential isolation (1 day)

**Total estimated time:** 11-16 days

---

## Success Metrics

1. All acceptance criteria met
2. All tests pass
3. No regression in existing functionality
4. GetXAPI costs are tracked accurately
5. Credential isolation verified
6. Business logic unchanged (uses provider abstraction)
