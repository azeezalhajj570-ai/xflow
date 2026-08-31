/* @odoo-module */

import { fields } from "@mail/core/common/record";
import { DiscussApp } from "@mail/core/public_web/discuss_app_model";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(DiscussApp.prototype, {
    setup() {
        super.setup(...arguments);
        this.whatsapp = fields.One("DiscussAppCategory", {
            compute() {
                return {
                    extraClass: "o-mail-DiscussSidebarCategory-whatsapp",
                    icon: "fa fa-whatsapp",
                    id: "whatsapp",
                    name: _t("WhatsApp"),
                    hideWhenEmpty: false,
                    canView: false,
                    canAdd: true,
                    addTitle: _t("Search WhatsApp Channel"),
                    serverStateKey: "is_discuss_sidebar_category_whatsapp_open",
                    sequence: 20,
                };
            },
            eager: true,
        });
    },
});
