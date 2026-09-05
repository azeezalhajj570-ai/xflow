#!/usr/bin/env python3
"""Monitor X Chat public key versions for diagnostic purposes.

This script queries the X API for public keys and logs the available versions.
Use this to track when new key versions appear after a key rotation.

Usage:
    docker exec odooo-odoo python3 /mnt/custom-addons/scripts/check_key_versions.py [account_ids]
    
Example:
    docker exec odooo-odoo python3 /mnt/custom-addons/scripts/check_key_versions.py 81 82
"""

import sys
import json
from datetime import datetime

sys.path.insert(0, '/usr/lib/python3/dist-packages')

import odoo
from odoo.tools import config

config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'odoo_2026-08-11_22-38-33'])

from odoo.modules.registry import Registry
registry = Registry('odoo_2026-08-11_22-38-33')

from odoo import api, SUPERUSER_ID


def check_account_keys(env, account_id):
    """Check and log the public key versions for an account."""
    account = env['social.account'].sudo().browse(account_id)
    
    if not account.exists():
        print(f"Account {account_id} not found")
        return
    
    if not account.twitter_user_id:
        print(f"Account {account_id} has no twitter_user_id")
        return
    
    print(f"\n{'='*60}")
    print(f"Account {account_id}: {account.name}")
    print(f"Twitter User ID: {account.twitter_user_id}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"{'='*60}")
    
    # Get stored state
    print(f"\nStored State:")
    print(f"  Key Mode: {account.x_chat_key_mode}")
    print(f"  PIN Locked: {account.x_chat_pin_locked}")
    print(f"  Signing Key Version: {account.x_chat_signing_key_version or 'unset'}")
    print(f"  PIN Status: {'SET' if account.x_encryption_code else 'NOT SET'}")
    
    # Query API
    try:
        from odoo.addons.x_account_twitter.services.twitter_api_client import TwitterApiClient
        client = TwitterApiClient(account)
        
        data = client.request(
            'GET', '/2/users/%s/public_keys' % account.twitter_user_id,
            params={'public_key.fields': 'public_key_version,juicebox_config'})
        
        rows = (data or {}).get('data') or []
        
        print(f"\nAPI Response:")
        print(f"  Total records: {len(rows)}")
        
        if rows:
            versions = []
            for row in rows:
                version = str(row.get('public_key_version'))
                has_juicebox = bool(row.get('juicebox_config'))
                versions.append(version)
                print(f"  Version {version}: juicebox_config={'YES' if has_juicebox else 'NO'}")
            
            # Check if stored version is in API
            stored_version = str(account.x_chat_signing_key_version) if account.x_chat_signing_key_version else None
            if stored_version:
                if stored_version in versions:
                    print(f"\n  ✓ Stored version {stored_version} found in API")
                else:
                    print(f"\n  ✗ Stored version {stored_version} NOT found in API")
                    print(f"    Available versions: {versions}")
            
            # Get latest version
            def parse_version(v):
                try:
                    return int(v)
                except (ValueError, TypeError):
                    return 0
            
            latest_version = max(versions, key=parse_version)
            print(f"\n  Latest API version: {latest_version}")
            
            if stored_version and stored_version != latest_version:
                print(f"  ⚠ Version mismatch: stored={stored_version} latest={latest_version}")
        
    except Exception as e:
        print(f"\nAPI Error: {e}")
    
    # Check pending tasks
    tasks = env['x.account.task'].sudo().search([
        ('account_id', '=', account_id),
        ('status', '=', 'pending')
    ])
    print(f"\nPending Tasks: {len(tasks)}")
    
    if tasks:
        oldest = min(tasks, key=lambda t: t.create_date)
        newest = max(tasks, key=lambda t: t.create_date)
        print(f"  Oldest: {oldest.create_date}")
        print(f"  Newest: {newest.create_date}")


def main():
    # Parse account IDs from command line or use defaults
    if len(sys.argv) > 1:
        account_ids = [int(x) for x in sys.argv[1:]]
    else:
        # Default: check accounts with pending tasks
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            tasks = env['x.account.task'].sudo().search([('status', '=', 'pending')])
            account_ids = list(set(tasks.mapped('account_id').ids))
            account_ids.sort()
    
    if not account_ids:
        print("No accounts to check")
        return
    
    print(f"Checking key versions for accounts: {account_ids}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        for account_id in account_ids:
            check_account_keys(env, account_id)
    
    print(f"\n{'='*60}")
    print("Check complete")


if __name__ == '__main__':
    main()
