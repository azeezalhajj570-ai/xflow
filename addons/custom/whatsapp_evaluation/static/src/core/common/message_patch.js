/* @odoo-module */

import { Message } from "@mail/core/common/message";
import { patch } from "@web/core/utils/patch";

patch(Message.prototype, {
    /**
     * @override
     */
    update(data) {
        super.update(data);
        if (data.whatsapp_status) {
            this.whatsappStatus = data.whatsapp_status; // sent, delivered, read, error
        }
    },

    get whatsappIconClass() {
        switch (this.whatsappStatus) {
            case 'sent':
                return 'fa fa-check text-muted'; // Single gray tick
            case 'delivered':
                return 'fa fa-check-double text-muted'; // Double gray ticks
            case 'read':
                return 'fa fa-check-double text-primary'; // Double blue ticks
            case 'error':
                return 'fa fa-exclamation-triangle text-danger'; // Error
            default:
                return '';
        }
    },

    get isWhatsAppMessage() {
        return Boolean(this.whatsappStatus);
    }
});
