/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, useRef, useState } from "@odoo/owl";

export class PrescriptionDispense extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.root = useRef("root");
        this.state = useState({
            loading: true,
            orderId: this.props.action.context.active_id,
            order: {},
            lines: [],
            explanationConfirmed: false,
        });

        onMounted(async () => {
            await this.loadPrescription();
            this.focusBarcode(0);
        });
    }

    async loadPrescription() {
        if (!this.state.orderId) {
            this.state.loading = false;
            return;
        }
        const data = await this.orm.call("sale.order", "fetch_prescription_dispensing", [[this.state.orderId]]);
        this.state.order = data;
        this.state.lines = data.lines;
        this.state.explanationConfirmed = data.medication_explanation_confirmed;
        this.state.loading = false;
    }

    focusBarcode(index) {
        setTimeout(() => {
            const inputs = this.root.el.querySelectorAll(".o_lesotho_barcode");
            const input = inputs[index];
            if (input) {
                input.focus();
                input.select();
            }
        }, 100);
    }

    async addProduct() {
        const line = await this.orm.call("sale.order", "add_prescription_dispensing_line", [[this.state.orderId]]);
        this.state.lines.push(line);
        this.focusBarcode(this.state.lines.length - 1);
    }

    async removeLine(line) {
        if (line.is_existing_prescription) {
            return;
        }
        await this.orm.call("sale.order", "remove_prescription_dispensing_line", [[this.state.orderId], line.id]);
        this.state.lines = this.state.lines.filter((item) => item.id !== line.id);
    }

    updateLocal(line, field, value) {
        line[field] = value;
    }

    async saveLine(line, field, value) {
        this.updateLocal(line, field, value);
        const updated = await this.orm.call(
            "sale.order",
            "update_prescription_dispensing_line",
            [[this.state.orderId], line.id, { [field]: value }]
        );
        Object.assign(line, updated);
    }

    async scanBarcode(line, index, ev) {
        if (ev.key !== "Enter") {
            return;
        }
        ev.preventDefault();
        try {
            const updated = await this.orm.call(
                "sale.order.line",
                "action_apply_barcode_scan",
                [[line.id], line.barcode]
            );
            Object.assign(line, updated);
            this.focusBarcode(index + 1);
        } catch (error) {
            this.notification.add(error.message || "Barcode could not be applied.", { type: "danger" });
        }
    }

    async setExplanation(ev) {
        this.state.explanationConfirmed = ev.target.checked;
        await this.orm.write("sale.order", [this.state.orderId], {
            medication_explanation_confirmed: this.state.explanationConfirmed,
        });
    }

    async serve() {
        if (!this.state.explanationConfirmed) {
            return;
        }
        if (!window.confirm("Are you sure you want to dispense these products and update the prescription status?")) {
            return;
        }
        const reportAction = await this.orm.call(
            "sale.order",
            "action_serve_prescription_from_ui",
            [[this.state.orderId]]
        );
        await this.action.doAction(reportAction);
    }
}

PrescriptionDispense.template = "lesotho_sale.PrescriptionDispense";

registry.category("actions").add("lesotho_sale.prescription_dispense", PrescriptionDispense);
