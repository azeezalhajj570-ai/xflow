/** @odoo-module */

import { Record } from "@mail/core/common/record";

export class WhatsAppAccount extends Record {
    static _name = "whatsapp.account";
    static id = "id";

    /** @type {number} */
    id;
    /** @type {string} */
    name;
}

WhatsAppAccount.register();
