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
        this.pendingSaves = new Set();
        this.saveErrors = new Map();
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
            readOnly: false,
            balanceResolutionDialog: {
                visible: false,
                reason: "external_referral",
                note: "",
                error: "",
            },
            confirmationDialog: {
                visible: false,
                title: "",
                message: "",
                confirmLabel: "Confirm",
                cancelLabel: "Cancel",
            },
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
        this.applyPrescriptionData(data);
        this.state.loading = false;
    }

    applyPrescriptionData(data) {
        this.state.order = data;
        this.state.lines = data.lines;
        this.state.activeLineId = data.lines.length ? data.lines[0].id : false;
        this.state.directionOptions = data.direction_options || {};
        this.state.productOptions = data.product_options || [];
        this.state.explanationConfirmed = data.medication_explanation_confirmed;
        this.state.readOnly = Boolean(data.is_readonly);
    }

    async goToPrescriptionList() {
        await this.action.doAction("sale.action_quotations_with_onboarding", {
            clearBreadcrumbs: true,
        });
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
            return "Served Externally";
        }
        if (line.prescription_status === "balance_waived" || (this.hasOutstandingBalance(line) && line.balance_resolution)) {
            return "Balance Waived";
        }
        const prescribed = Number(line.quantity_prescribed || 0);
        const dispensed = Number(line.quantity_dispensed || 0);
        if (dispensed <= 0) {
            return "Not Dispensed";
        }
        if (dispensed < prescribed) {
            return "Partially Dispensed";
        }
        return "Fully Dispensed";
    }

    getLineStatusClass(line) {
        const status = this.getLineStatus(line);
        if (status === "Fully Dispensed" || status === "Served Externally" || status === "Balance Waived") {
            return "o_lesotho_tab_status served";
        }
        if (status === "Partially Dispensed") {
            return "o_lesotho_tab_status partial";
        }
        if (status === "Not Dispensed") {
            return "o_lesotho_tab_status pending";
        }
        return "o_lesotho_tab_status served";
    }

    hasOutstandingBalance(line) {
        if (!line.served_internally) {
            return false;
        }
        const prescribed = Number(line.quantity_prescribed || 0);
        const dispensed = Number(line.quantity_dispensed || 0);
        return dispensed < prescribed;
    }

    collectBalanceResolution() {
        this.state.balanceResolutionDialog.visible = true;
        this.state.balanceResolutionDialog.reason = "external_referral";
        this.state.balanceResolutionDialog.note = "";
        this.state.balanceResolutionDialog.error = "";
        return new Promise((resolve) => {
            this.balanceResolutionResolver = resolve;
        });
    }

    setBalanceResolutionReason(value) {
        this.state.balanceResolutionDialog.reason = value;
        this.state.balanceResolutionDialog.error = "";
        if (value !== "other") {
            this.state.balanceResolutionDialog.note = "";
        }
    }

    setBalanceResolutionNote(value) {
        this.state.balanceResolutionDialog.note = value;
        this.state.balanceResolutionDialog.error = "";
    }

    cancelBalanceResolutionDialog() {
        this.state.balanceResolutionDialog.visible = false;
        if (this.balanceResolutionResolver) {
            this.balanceResolutionResolver(false);
            this.balanceResolutionResolver = null;
        }
    }

    confirmBalanceResolutionDialog() {
        const reason = this.state.balanceResolutionDialog.reason;
        const note = (this.state.balanceResolutionDialog.note || "").trim();
        if (!["external_referral", "other"].includes(reason)) {
            this.state.balanceResolutionDialog.error =
                "Choose either external referral or other before closing without a back order.";
            return;
        }
        if (reason === "other" && !note) {
            this.state.balanceResolutionDialog.error =
                "Enter an explanation for the other balance resolution reason.";
            return;
        }
        this.state.balanceResolutionDialog.visible = false;
        if (this.balanceResolutionResolver) {
            this.balanceResolutionResolver({
                balanceResolution: reason,
                balanceResolutionNote: note,
            });
            this.balanceResolutionResolver = null;
        }
    }

    confirmAction({ title, message, confirmLabel = "Confirm", cancelLabel = "Cancel" }) {
        this.state.confirmationDialog.visible = true;
        this.state.confirmationDialog.title = title;
        this.state.confirmationDialog.message = message;
        this.state.confirmationDialog.confirmLabel = confirmLabel;
        this.state.confirmationDialog.cancelLabel = cancelLabel;
        return new Promise((resolve) => {
            this.confirmationResolver = resolve;
        });
    }

    cancelConfirmationDialog() {
        this.state.confirmationDialog.visible = false;
        if (this.confirmationResolver) {
            this.confirmationResolver(false);
            this.confirmationResolver = null;
        }
    }

    confirmConfirmationDialog() {
        this.state.confirmationDialog.visible = false;
        if (this.confirmationResolver) {
            this.confirmationResolver(true);
            this.confirmationResolver = null;
        }
    }

    toggleReview() {
        this.state.reviewExpanded = !this.state.reviewExpanded;
    }

    getOrderStatusClass(status) {
        const activeStatus = this.state.order.prescription_status || "awaiting_dispensing";
        return `o_lesotho_status_step ${activeStatus === status ? "active" : ""}`;
    }

    isOnHold() {
        return this.state.order.prescription_status === "on_hold" || Boolean(this.state.order.is_on_hold);
    }

    canServe() {
        return !this.state.readOnly && !this.isOnHold() && this.state.explanationConfirmed;
    }

    focusBarcode(index) {
        if (this.state.readOnly) {
            return;
        }
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
        if (this.state.readOnly) {
            return;
        }
        const line = await this.orm.call("sale.order", "add_prescription_dispensing_line", [[this.state.orderId]]);
        this.state.lines.push(line);
        this.state.activeLineId = line.id;
        this.focusBarcode(this.state.lines.length - 1);
    }

    async removeLine(line) {
        if (this.state.readOnly) {
            return;
        }
        if (line.is_existing_prescription) {
            return;
        }
        await this.orm.call("sale.order", "remove_prescription_dispensing_line", [[this.state.orderId], line.id]);
        this.state.lines = this.state.lines.filter((item) => item.id !== line.id);
    }

    updateLocal(line, field, value) {
        line[field] = value;
    }

    trackSave(promise) {
        this.pendingSaves.add(promise);
        promise.finally(() => this.pendingSaves.delete(promise));
        return promise;
    }

    async waitForPendingSaves() {
        while (this.pendingSaves.size) {
            await Promise.all([...this.pendingSaves]);
        }
        if (this.saveErrors.size) {
            throw new Error("Some prescription changes could not be saved.");
        }
    }

    clearSaveError(key) {
        this.saveErrors.delete(key);
    }

    recordSaveError(key, error, fallbackMessage) {
        const message = error.message || fallbackMessage;
        this.saveErrors.set(key, message);
        this.notification.add(message, { type: "danger" });
    }

    onProductChanged(line, value) {
        if (this.state.readOnly) {
            return;
        }
        const productId = value ? parseInt(value, 10) : false;
        if (productId) {
            const product = this.state.productOptions.find((item) => item.id === productId);
            this.updateLocal(line, "product_id", productId);
            this.updateLocal(line, "dispensed_product", product ? product.name : "");
            this.updateLocal(line, "batch_number", "");
            this.updateLocal(line, "expiry_date", "");
            this.updateLocal(line, "batch_options", []);
            this.saveLine(line, "product_id", productId, { skipLocalUpdate: true });
        }
    }

    onBatchChanged(line, value) {
        if (this.state.readOnly) {
            return;
        }
        const batchOptions = line.batch_options || [];
        const selectedBatch = batchOptions.find((item) => item.batch_number === value);
        this.updateLocal(line, "batch_number", value);
        this.updateLocal(line, "expiry_date", selectedBatch ? selectedBatch.expiry_date : "");
        this.saveLine(line, "batch_number", value, { skipLocalUpdate: true });
    }

    onFieldChanged(line, field, value) {
        if (this.state.readOnly) {
            return;
        }
        if (field === "served_internally" && !value) {
            this.updateLocal(line, "quantity_dispensed", 0);
        }
        if (field === "balance_resolution" && value !== "other") {
            this.updateLocal(line, "balance_resolution_note", "");
            this.saveLine(line, "balance_resolution_note", "");
        }
        if (field === 'quantity_dispensed') {
            const numericValue = parseFloat(value || 0);
            if (numericValue > 0 && !line.served_internally) {
                this.updateLocal(line, "served_internally", true);
            }
            this.saveLine(line, field, numericValue);
        } else if (field === 'dose' || field === 'duration') {
            this.saveLine(line, field, parseInt(value || 0, 10));
        } else {
            this.saveLine(line, field, value);
        }
    }

    saveLine(line, field, value, options = {}) {
        if (this.state.readOnly) {
            return;
        }
        if (!options.skipLocalUpdate) {
            this.updateLocal(line, field, value);
        }
        if (field === "product_id") {
            const product = this.state.productOptions.find((item) => item.id === value);
            this.updateLocal(line, "dispensed_product", product ? product.name : "");
        }
        const saveKey = `${line.id}:${field}`;
        this.clearSaveError(saveKey);
        const savePromise = this.orm.call(
            "sale.order",
            "update_prescription_dispensing_line",
            [[this.state.orderId], line.id, { [field]: value }]
        ).then((updated) => {
            Object.assign(line, updated);
        }).catch((error) => {
            this.recordSaveError(saveKey, error, "Error updating prescription line.");
            return false;
        });
        return this.trackSave(savePromise);
    }

    setSubstitute(line, checked) {
        if (this.state.readOnly) {
            return;
        }
        if (checked) {
            // When enabling substitute, mark it as pack substituted
            this.saveLine(line, "is_pack_substituted", true);
            return;
        }
        const productId = line.prescribed_product_id || line.product_id || false;
        this.updateLocal(line, "is_pack_substituted", false);
        this.updateLocal(line, "product_id", productId);
        const product = this.state.productOptions.find((item) => item.id === productId);
        this.updateLocal(line, "dispensed_product", product ? product.name : line.prescribed_product);
        this.updateLocal(line, "batch_number", "");
        this.updateLocal(line, "expiry_date", "");
        this.updateLocal(line, "batch_options", []);

        const saveKey = `${line.id}:substitution`;
        this.clearSaveError(saveKey);
        const savePromise = this.orm.call(
            "sale.order",
            "update_prescription_dispensing_line",
            [[this.state.orderId], line.id, { is_pack_substituted: false, product_id: productId }]
        ).then((updated) => {
            Object.assign(line, updated);
        }).catch((error) => {
            this.recordSaveError(saveKey, error, "Error updating prescription line.");
            return false;
        });
        return this.trackSave(savePromise);
    }

    async scanBarcode(line, index, ev) {
        if (this.state.readOnly) {
            return;
        }
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

    setExplanation(ev) {
        if (this.state.readOnly) {
            return;
        }
        this.state.explanationConfirmed = ev.target.checked;
        const saveKey = "order:medication_explanation_confirmed";
        this.clearSaveError(saveKey);
        const savePromise = this.orm.write("sale.order", [this.state.orderId], {
            medication_explanation_confirmed: this.state.explanationConfirmed,
        }).catch((error) => {
            this.recordSaveError(saveKey, error, "Error updating explanation status.");
            return false;
        });
        return this.trackSave(savePromise);
    }

    async cancel() {
        await this.goToPrescriptionList();
    }

    async save() {
        if (this.state.readOnly) {
            await this.goToPrescriptionList();
            return;
        }
        try {
            await this.waitForPendingSaves();
            await this.orm.call("sale.order", "action_save_prescription_from_ui", [[this.state.orderId]]);
            this.notification.add("Prescription saved.", { type: "success" });
        } catch (error) {
            this.notification.add(
                error.message || "Prescription not saved because some changes could not be persisted.",
                { type: "danger" }
            );
        }
    }

    async putOnHold() {
        if (this.state.readOnly) {
            await this.goToPrescriptionList();
            return;
        }
        const reason = window.prompt("Reason for putting this prescription on hold:");
        if (!reason || !reason.trim()) {
            this.notification.add("Enter a reason before putting the prescription on hold.", { type: "warning" });
            return;
        }
        try {
            await this.waitForPendingSaves();
        } catch (error) {
            this.notification.add(
                "Prescription not put on hold because some changes were not saved. Please correct the highlighted issue and try again.",
                { type: "warning" }
            );
            return;
        }
        const listAction = await this.orm.call(
            "sale.order",
            "action_hold_prescription_from_ui",
            [[this.state.orderId], reason.trim()]
        );
        await this.action.doAction(listAction);
    }

    async resumeDispensing() {
        if (this.state.readOnly) {
            await this.goToPrescriptionList();
            return;
        }
        try {
            const data = await this.orm.call(
                "sale.order",
                "action_resume_prescription_from_ui",
                [[this.state.orderId]]
            );
            this.applyPrescriptionData(data);
            this.notification.add("Dispensing resumed.", { type: "success" });
        } catch (error) {
            this.notification.add(error.message || "Prescription could not be resumed.", { type: "danger" });
        }
    }

    async serve() {
        if (this.state.readOnly) {
            await this.goToPrescriptionList();
            return;
        }
        if (this.isOnHold()) {
            this.notification.add("Resume dispensing before serving an on-hold prescription.", { type: "warning" });
            return;
        }
        if (!this.state.explanationConfirmed) {
            return;
        }
        try {
            await this.waitForPendingSaves();
        } catch (error) {
            this.notification.add(
                "Prescription not served because some changes were not saved. Please correct the highlighted issue and try again.",
                { type: "warning" }
            );
            return;
        }
        const confirmedServe = await this.confirmAction({
            title: "Confirm Dispensing",
            message: "Are you sure you want to dispense these products and update the prescription status?",
            confirmLabel: "Dispense",
            cancelLabel: "Cancel",
        });
        if (!confirmedServe) {
            return;
        }
        const summary = await this.orm.call(
            "sale.order",
            "evaluate_prescription_serving",
            [[this.state.orderId]]
        );
        if (summary.has_internal_lines && Number(summary.total_dispensed || 0) <= 0) {
            this.notification.add(
                "Enter a quantity to dispense for at least one internal prescription item before serving.",
                { type: "warning" }
            );
            return;
        }
        let createBackorder = false;
        let balanceResolution = false;
        let balanceResolutionNote = false;
        if (summary.needs_backorder) {
            createBackorder = await this.confirmAction({
                title: "Outstanding Balance",
                message: "Some quantities are still outstanding. Create a back order for the balance?",
                confirmLabel: "Create Back Order",
                cancelLabel: "Choose Resolution",
            });
            if (!createBackorder) {
                const resolution = await this.collectBalanceResolution();
                if (!resolution) {
                    return;
                }
                balanceResolution = resolution.balanceResolution;
                balanceResolutionNote = resolution.balanceResolutionNote;
            }
        }
        const reportAction = await this.orm.call(
            "sale.order",
            "action_serve_prescription_from_ui",
            [[this.state.orderId], createBackorder, balanceResolution, balanceResolutionNote]
        );
        await this.action.doAction(reportAction);
        await this.goToPrescriptionList();
    }
}

PrescriptionDispense.template = "lesotho_sale.PrescriptionDispense";

registry.category("actions").add("lesotho_sale.prescription_dispense", PrescriptionDispense);
