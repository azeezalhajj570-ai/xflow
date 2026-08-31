/** @odoo-module **/

import { ChannelMemberList } from "@mail/discuss/core/common/channel_member_list";
import { patch } from "@web/core/utils/patch";

patch(ChannelMemberList.prototype, {
    canOpenChatWith(member) {
        if (!super.canOpenChatWith(member)) {
            return false;
        }
        if (!member.thread || !member.thread.whatsapp_partner_id) {
            return true;
        }
        return member.persona.notEq(member.thread.whatsapp_partner_id);
    },
});
