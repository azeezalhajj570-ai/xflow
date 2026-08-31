# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import secrets
import string

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.addons.whatsapp_evaluation.tools.whatsapp_api import WhatsAppApi
from odoo.addons.whatsapp_evaluation.tools.whatsapp_exception import WhatsAppError

_logger = logging.getLogger(__name__)


class WhatsAppAccount(models.Model):
    _name = 'whatsapp.account'
    _inherit = ['mail.thread']
    _description = 'WhatsApp Business Account'

    name = fields.Char(string="Name", tracking=1)
    active = fields.Boolean(default=True, tracking=6)

    # Evolution API Credentials
    base_url = fields.Char('API Base URL', required=True, help="Base URL of your Evolution API instance (e.g., https://api.example.com)")
    instance_name = fields.Char('Instance Name', required=True, help="Name of the Evolution API instance")
    api_key = fields.Char('Global API Key', required=True, groups="base.group_system", help="Global API Key (for management)")
    instance_token = fields.Char('Instance Token', groups="base.group_system", help="Token specific to this instance (for sending messages)")

    webhook_verify_token = fields.Char(string="Webhook Verify Token", compute='_compute_verify_token',
                                       groups='base.group_system', store=True, readonly=False)
    callback_url = fields.Char(string="Callback URL", compute='_compute_callback_url', readonly=True, copy=False)

    allowed_company_ids = fields.Many2many(
        comodel_name='res.company', string="Allowed Company",
        default=lambda self: self.env.company)
    notify_user_ids = fields.Many2many(
        comodel_name='res.users', default=lambda self: self.env.user,
        domain=[('share', '=', False)], required=True, tracking=5,
        help="Users to notify when a message is received")

    @api.constrains('notify_user_ids')
    def _check_notify_user_ids(self):
        if not self.ids:
            return
        f = self._fields['notify_user_ids']
        self.env.cr.execute(
            'SELECT {} FROM {} WHERE {} IN %s'.format(f.column1, f.relation, f.column1),
            [tuple(self.ids)]
        )
        ids_with_users = {r[0] for r in self.env.cr.fetchall()}
        for wa in self:
            if wa.id not in ids_with_users:
                raise ValidationError(_("Users to notify is required"))

    def _compute_verify_token(self):
        for account in self:
            if not account.webhook_verify_token:
                account.webhook_verify_token = secrets.token_urlsafe(32)

    def _compute_callback_url(self):
        for account in self:
            base_url = self.get_base_url()
            # Force HTTPS for webhook callback as required by many external APIs
            if base_url.startswith('http://'):
                base_url = base_url.replace('http://', 'https://', 1)
            account.callback_url = base_url + '/whatsapp_evaluation/webhook'

    @api.model
    def _get_api_client(self):
        self.ensure_one()
        return WhatsAppApi(self.base_url, self.instance_name, self.api_key, self.instance_token)

    def button_test_connection(self):
        for account in self:
            api = account._get_api_client()
            try:
                # Test connection logic will be implemented in WhatsAppApi
                api._test_connection()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _("Connection Successful"),
                        'message': _("Successfully connected to Evolution API instance."),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            except WhatsAppError as e:
                raise UserError(_("Connection Failed: %s") % str(e))
            except Exception as e:
                _logger.error("WhatsApp Connection Error: %s", str(e))
                raise UserError(_("Connection Failed. Check logs for details."))

    def button_configure_webhook(self):
        """ Pushes the callback URL to the Evolution API """
        self.ensure_one()
        if not self.callback_url:
             raise UserError(_("Callback URL is not generated yet."))
        
        api = self._get_api_client()
        try:
            api.update_webhook(self.callback_url, enabled=True)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Webhook Configured"),
                    'message': _("Successfully updated webhook settings on Evolution API."),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except WhatsAppError as e:
            raise UserError(_("Webhook Configuration Failed: %s") % str(e))
