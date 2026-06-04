/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ListController } from "@web/views/list/list_controller";

patch(ListController.prototype, "lesotho_sale_prescription_list_open", {
    async openRecord(record) {
        const context = this.props.context || {};
        if (
            this.props.resModel === "sale.order" &&
            context.open_prescription_dispense_page &&
            record.resId
        ) {
            const activeIds = this.model.root.records.map((datapoint) => datapoint.resId);
            return this.actionService.doAction({
                type: "ir.actions.client",
                name: "Prescription Dispensing",
                tag: "lesotho_sale.prescription_dispense",
                target: "current",
                context: {
                    ...context,
                    active_id: record.resId,
                    active_ids: activeIds,
                    active_model: "sale.order",
                },
            });
        }
        return this._super(record);
    },
});
