# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import hashlib
import logging
import os

from odoo import fields

_logger = logging.getLogger(__name__)

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None


class XSessionManager:
    """Central session lifecycle service.

    - Persistent session state lives in x.session.store (patch of truth).
    - Runtime client state lives in an in-memory registry, never the durable
      source of truth.
    """

    _runtime_registry = {}

    _ENV_KEY = 'X_SESSION_ENCRYPTION_KEY'
    _DEV_FALLBACK_KEY = 'x-account-dev-encryption-key-not-for-production'

    @classmethod
    def _get_key_material(cls, env):
        key = os.environ.get(cls._ENV_KEY)
        if key:
            return key.encode()
        config = env['ir.config_parameter'].sudo().get_param('x_account.dev_encryption_key')
        if config:
            _logger.warning(
                'X session encryption key provided via dev config parameter. '
                'For production, use the %s environment variable.', cls._ENV_KEY)
            return config.encode()
        _logger.warning(
            'X session encryption key missing. Using non-production dev fallback key. '
            'Set %s in the deployment environment.', cls._ENV_KEY)
        return cls._DEV_FALLBACK_KEY.encode()

    @classmethod
    def _derive_key(cls, env):
        return hashlib.sha256(cls._get_key_material(env)).digest()

    @classmethod
    def encrypt(cls, env, plaintext):
        if AESGCM is None:
            raise RuntimeError('cryptography library is required for session encryption.')
        if isinstance(plaintext, str):
            plaintext = plaintext.encode('utf-8')
        key = cls._derive_key(env)
        salt = os.urandom(16)
        iv = os.urandom(12)
        derived = hashlib.sha256(salt + key).digest()
        ciphertext = AESGCM(derived).encrypt(iv, plaintext, None)
        return 'aes-256-gcm:{s}:{i}:{c}'.format(
            s=base64.b64encode(salt).decode(),
            i=base64.b64encode(iv).decode(),
            c=base64.b64encode(ciphertext).decode(),
        )

    @classmethod
    def decrypt(cls, env, blob):
        if AESGCM is None:
            raise RuntimeError('cryptography library is required for session decryption.')
        try:
            alg, salt_b64, iv_b64, ct_b64 = blob.split(':')
        except ValueError:
            raise ValueError('Malformed encrypted session blob.')
        if alg != 'aes-256-gcm':
            raise ValueError('Unsupported session encryption algorithm: %s' % alg)
        key = cls._derive_key(env)
        salt = base64.b64decode(salt_b64)
        iv = base64.b64decode(iv_b64)
        ciphertext = base64.b64decode(ct_b64)
        derived = hashlib.sha256(salt + key).digest()
        return AESGCM(derived).decrypt(iv, ciphertext, None)

    @classmethod
    def create_store(cls, account, cookie_str, source='manual'):
        """Encrypt a raw session cookie string and create/update the store record."""
        store = account.x_session_store_id
        blob = cls.encrypt(account.env, cookie_str)
        if store:
            store.sudo().write({'encrypted_blob': blob,
                                'last_access_at': fields.Datetime.now()})
        else:
            store = account.env['x.session.store'].sudo().create({
                'account_id': account.id,
                'encrypted_blob': blob,
                'source': source,
            })
            account.write({'x_session_store_id': store.id})
        return store

    @classmethod
    def load(cls, account):
        """Return the decrypted session cookie string, or None."""
        store = account.x_session_store_id
        if not store or not store.encrypted_blob:
            return None
        try:
            return cls.decrypt(account.env, store.encrypted_blob).decode('utf-8')
        except Exception:
            _logger.exception('Failed to decrypt session for account %s', account.id)
            return None

    @classmethod
    def delete_store(cls, account):
        store = account.x_session_store_id
        if store:
            store.sudo().unlink()
        account.write({'x_session_store_id': False})

    @classmethod
    def register_runtime(cls, account, provider):
        cls._runtime_registry[account.id] = provider

    @classmethod
    def get_runtime(cls, account):
        return cls._runtime_registry.get(account.id)

    @classmethod
    def drop_runtime(cls, account):
        cls._runtime_registry.pop(account.id, None)
