# Fix UTM Medium Unique Constraint Error on Account Relink

## Problem

When relinking an X account (e.g., "malak ملاك" or "مرام"), the system fails with:
```
Unprocessable Entity
لا يمكن إكمال العملية: يجب أن يكون الاسم فريداً
(The name must be unique)
```

## Root Cause

1. The base Odoo `social.account` create() method automatically creates a `utm.medium` record with name `[X] account_name`
2. The `utm.medium` model has a UNIQUE constraint on the `name` field
3. When relinking an account with the same name, it tries to create a duplicate `utm.medium`, which fails

Example from database:
```
utm_medium id 79: [X] malak ملاك
utm_medium id 89: [X] مرام
```

When trying to relink these accounts, the system tries to create new utm.medium records with the same names, violating the unique constraint.

## Solution

Override the `create()` method in `x_account_twitter/models/social_account.py` to:
1. Check if a `utm.medium` with the name `[X] account_name` already exists
2. If it exists, reuse it instead of creating a new one
3. This allows relinking accounts with the same name without errors

## Implementation

### File: `addons/custom/x_account_twitter/models/social_account.py`

Modify the `create()` method (around line 95) to add utm.medium reuse logic:

```python
@api.model_create_multi
def create(self, vals_list):
    """Auto-assign the twitter provider to accounts created by the OAuth
    callback (OAuth 1.0a or OAuth 2.0).

    OAuth-linked accounts (tokens present) are real accounts and keep the
    default follow-stream creation. Accounts without tokens (e.g. a
    'twitter'-provider account not yet linked) must not trigger the default
    stream, which would call the real X API and fail — the same suppression
    x_account uses for session imports.
    
    Also handles utm.medium uniqueness: when relinking an account with the
    same name, reuse the existing utm.medium instead of creating a duplicate.
    """
    for vals in vals_list:
        media_type = vals.get('media_type')
        if not media_type and vals.get('media_id'):
            media_type = self.env['social.media'].browse(vals['media_id']).media_type
        if (media_type == 'twitter' and not vals.get('twitter_oauth_token')
                and not vals.get('x_oauth2_access_token')
                and not self.env.context.get('x_no_default_stream')):
            self = self.with_context(x_no_default_stream=True)
            break
    
    # Pre-create utm.medium records to handle uniqueness constraint
    # This prevents errors when relinking accounts with the same name
    for vals in vals_list:
        if vals.get('media_id') and vals.get('name') and not vals.get('utm_medium_id'):
            media = self.env['social.media'].browse(vals['media_id'])
            medium_name = "[%(media_name)s] %(account_name)s" % {
                "media_name": media.name,
                "account_name": vals['name']
            }
            # Check if utm.medium with this name already exists
            existing_medium = self.env['utm.medium'].sudo().search([
                ('name', '=', medium_name)
            ], limit=1)
            if existing_medium:
                vals['utm_medium_id'] = existing_medium.id
    
    records = super().create(vals_list)
    for record, vals in zip(records, vals_list):
        if record.media_type != 'twitter' or vals.get('x_provider'):
            continue
        if record.twitter_oauth_token and not vals.get('x_oauth2_access_token'):
            record.write(self._get_oauth1_defaults())
        elif record.x_oauth2_access_token:
            record.write(self._get_oauth2_defaults())
    return records
```

## Testing

1. Update the module:
   ```bash
   docker exec -u odoo odooo-odoo /entrypoint.sh odoo server -d odoo_2026-08-11_22-38-33 -u x_account_twitter --stop-after-init
   ```

2. Try relinking an account that already exists (e.g., "malak ملاك" or "مرام")

3. Verify that:
   - No unique constraint error occurs
   - The account is successfully relinked
   - The existing utm.medium is reused

## Impact

- Allows relinking X accounts without errors
- Reuses existing utm.medium records instead of creating duplicates
- No breaking changes to existing functionality
