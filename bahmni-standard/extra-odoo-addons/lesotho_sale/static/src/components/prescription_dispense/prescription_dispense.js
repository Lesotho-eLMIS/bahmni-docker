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
            activeLineId: false,
            directionOptions: {},
            productOptions: [],
            explanationConfirmed: false,
            reviewExpanded: true,
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
        this.state.activeLineId = data.lines.length ? data.lines[0].id : false;
        this.state.directionOptions = data.direction_options || {};
        this.state.productOptions = data.product_options || [];
        this.state.explanationConfirmed = data.medication_explanation_confirmed;
        this.state.loading = false;
    }

    selectLine(line) {
        this.state.activeLineId = line.id;
    }

    isLineActive(line) {
        return this.state.activeLineId === line.id;
    }

    isLineComplete(line) {
        return [
            line.dispensed_product,
            line.quantity_dispensed,
            line.batch_number,
            line.barcode,
            line.dose,
            line.dose_unit,
            line.frequency,
            line.route,
            line.duration,
            line.duration_units,
            line.instructions,
            line.additional_instructions,
        ].every((value) => value !== false && value !== null && value !== undefined && value !== "");
    }

    getLineStatus(line) {
        if (!line.served_internally) {
            return "Serve externally";
        }
        if (!this.isLineComplete(line)) {
            return "Pending";
        }
        const prescribed = Number(line.quantity_prescribed || 0);
        const dispensed = Number(line.quantity_dispensed || 0);
        if (dispensed <= 0) {
            return "Pending";
        }
        if (Math.abs(prescribed - dispensed) < 0.0001) {
            return "Fully served";
        }
        if (dispensed > 0 && dispensed < prescribed) {
            return "Partially served";
        }
        return "Check quantity";
    }

    getLineStatusClass(line) {
        const status = this.getLineStatus(line);
        if (status === "Fully served") {
            return "o_lesotho_tab_status served";
        }
        if (status === "Partially served") {
            return "o_lesotho_tab_status partial";
        }
        if (status === "Serve externally") {
            return "o_lesotho_tab_status external";
        }
        if (status === "Check quantity") {
            return "o_lesotho_tab_status warning";
        }
        return "o_lesotho_tab_status pending";
    }

    toggleReview() {
        this.state.reviewExpanded = !this.state.reviewExpanded;
    }

    getOrderStatusClass(status) {
        const orderState = this.state.order.state;
        const activeStatus = orderState === "sent" ? "sent" : (["sale", "done"].includes(orderState) ? "sale" : "draft");
        return `o_lesotho_status_step ${activeStatus === status ? "active" : ""}`;
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
        this.state.activeLineId = line.id;
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
        if (field === "product_id") {
            const product = this.state.productOptions.find((item) => item.id === value);
            this.updateLocal(line, "dispensed_product", product ? product.name : "");
        }
        const updated = await this.orm.call(
            "sale.order",
            "update_prescription_dispensing_line",
            [[this.state.orderId], line.id, { [field]: value }]
        );
        Object.assign(line, updated);
    }

    async setSubstitute(line, checked) {
        if (checked) {
            await this.saveLine(line, "is_pack_substituted", true);
            return;
        }
        const productId = line.prescribed_product_id || line.product_id || false;
        this.updateLocal(line, "is_pack_substituted", false);
        this.updateLocal(line, "product_id", productId);
        const product = this.state.productOptions.find((item) => item.id === productId);
        this.updateLocal(line, "dispensed_product", product ? product.name : line.prescribed_product);
        const updated = await this.orm.call(
            "sale.order",
            "update_prescription_dispensing_line",
            [[this.state.orderId], line.id, { is_pack_substituted: false, product_id: productId }]
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
            const nextLine = this.state.lines[index + 1];
            if (nextLine) {
                this.state.activeLineId = nextLine.id;
            }
            this.focusBarcode(0);
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

    async cancel() {
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order",
            res_id: this.state.orderId,
            views: [[false, "form"]],
            target: "current",
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
