# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, fields, models
from odoo.exceptions import AccessError


class XSessionStore(models.Model):
    """Encrypted credential storage.

    This is an in-DB model (PostgreSQL), so it is present in backups/snapshots/
    replication. Security rests ENTIRELY on key separation (the encryption key is
    supplied via deployment config, never stored here), ACLs, and masking.
    """

    _name = 'x.session.store'
    _description = 'X Session Store'
    _rec_name = 'account_id'

    account_id = fields.Many2one(
        'social.account',
        string='X Account',
        required=True,
        index=True,
        ondelete='cascade',
    )
    encrypted_blob = fields.Text(
        string='Encrypted Session Blob',
        readonly=True,
        help='AES-256-GCM encrypted session/cookie payload. Never logged or '
             'exposed through normal APIs.',
    )
    alg = fields.Char(string='Algorithm', default='aes-256-gcm', readonly=True)
    created_at = fields.Datetime(string='Created At', default=fields.Datetime.now, readonly=True)
    last_access_at = fields.Datetime(string='Last Access At', readonly=True)
    source = fields.Char(string='Source', help='Origin of the session (e.g. xaction)')

    def write(self, vals):
        if 'encrypted_blob' in vals and not self.env.su:
            raise AccessError(_('Only system users may modify encrypted session blobs.'))
        return super().write(vals)
