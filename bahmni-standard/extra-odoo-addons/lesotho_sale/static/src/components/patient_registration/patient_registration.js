/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, useRef, onWillStart } from "@odoo/owl";
import { useSetupAction } from "@web/webclient/actions/action_hook";

export class PatientRegistration extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.form = useRef("form");
        this.state = useState({
            saving: false, dirty: false, error: "", options: {}, patientId: this.props.action.context?.patient_id || null,
            values: {
                first_name: "", middle_name: "", last_name: "", sex: "",
                age_years: "", age_months: "", age_days: "",
                birth_date: "", estimated: false, birth_time: "", street: "", street2: "",
                city: "", district_name: "", state_name: "", zip: "", phone: "", mobile: "", email: "",
            },
        });
        useSetupAction({
            beforeLeave: () => this.canLeave(),
            beforeUnload: (ev) => {
                if (this.state.dirty) {
                    ev.preventDefault();
                    ev.returnValue = "";
                }
            },
        });
        onWillStart(async () => {
            this.state.options = await this.orm.call("res.partner", "patient_registration_options", [], { patient_id: this.state.patientId });
            if (this.state.patientId) {
                const patient = await this.orm.call("res.partner", "get_patient_registration", [[this.state.patientId]]);
                Object.assign(this.state.values, patient.values);
            }
        });
    }

    canLeave() {
        return !this.state.dirty || window.confirm("Discard the unsaved patient details?");
    }

    get patientName() {
        const v = this.state.values;
        return [v.first_name, v.middle_name, v.last_name].filter(Boolean).join(" ") || "New Patient";
    }

    onChange() {
        this.state.dirty = true;
        this.state.error = "";
    }

    parseDate(value) {
        const [year, month, day] = value.split("-").map(Number);
        return new Date(year, month - 1, day);
    }

    dateString(date) {
        return `${String(date.getFullYear()).padStart(4, "0")}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    }

    onBirthDateChange() {
        this.onChange();
        const v = this.state.values;
        if (!v.birth_date || v.birth_date > this.state.options.today) {
            v.age_years = v.age_months = v.age_days = "";
            return;
        }
        const birth = this.parseDate(v.birth_date);
        const today = this.parseDate(this.state.options.today);
        let months = (today.getFullYear() - birth.getFullYear()) * 12 + today.getMonth() - birth.getMonth();
        const anniversary = (offset) => {
            const date = new Date(birth.getFullYear(), birth.getMonth() + offset, 1);
            date.setDate(Math.min(birth.getDate(), new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate()));
            return date;
        };
        if (anniversary(months) > today) months--;
        const anchor = anniversary(months);
        v.age_years = String(Math.floor(months / 12));
        v.age_months = String(months % 12);
        // UTC arithmetic avoids daylight-saving offsets between calendar dates.
        v.age_days = String(Math.round((Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) - Date.UTC(anchor.getFullYear(), anchor.getMonth(), anchor.getDate())) / 86400000));
    }

    onAgeChange() {
        this.onChange();
        const v = this.state.values;
        const parts = [v.age_years, v.age_months, v.age_days];
        if (parts.every(p => p === "")) {
            v.birth_date = "";
            return;
        }
        if (parts.some(p => p !== "" && !/^\d+$/.test(String(p))) || Number(v.age_months) > 11 || Number(v.age_days) > 30) {
            v.birth_date = "";
            return;
        }
        const today = this.parseDate(this.state.options.today);
        const date = new Date(today.getFullYear() - Number(v.age_years), today.getMonth() - Number(v.age_months), 1);
        date.setDate(Math.min(today.getDate(), new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate()));
        date.setDate(date.getDate() - Number(v.age_days));
        v.birth_date = this.dateString(date);
        v.estimated = true;
    }

    async createPrescription(ev) {
        return this.save(ev, true);
    }

    async save(ev, createPrescription = false) {
        ev.preventDefault();
        if (this.state.saving || !this.form.el.reportValidity()) return;
        const v = this.state.values;
        if (!this.state.patientId && !v.birth_date && [v.age_years, v.age_months, v.age_days].every(p => p === "")) {
            this.state.error = "Enter an age or date of birth.";
            return;
        }
        this.state.saving = true;
        this.state.error = "";
        let patient;
        try {
            patient = this.state.patientId
                ? await this.orm.call("res.partner", "update_patient_registration", [[this.state.patientId], { ...v }])
                : await this.orm.call("res.partner", "register_patient", [{ ...v }]);
        } catch (error) {
            this.state.error = error.data?.message || error.message || "The patient could not be saved. Please try again.";
            this.state.saving = false;
            return;
        }
        const wasEditing = Boolean(this.state.patientId);
        this.state.patientId = patient.id;
        this.state.dirty = false;
        this.notification.add(`Patient ${patient.name}${patient.identifier ? ` [${patient.identifier}]` : ""} ${wasEditing ? "updated" : "created"}.`, { type: "success" });
        try {
            if (createPrescription) {
                await this.action.doAction("lesotho_base.action_create_new_prescription_placeholder", {
                    clearBreadcrumbs: true,
                    additionalContext: { default_partner_id: patient.id },
                });
            } else {
                await this.action.doAction("account.res_partner_action_customer", { clearBreadcrumbs: true });
            }
        } finally {
            this.state.saving = false;
        }
    }

    async cancel() {
        if (this.canLeave()) {
            this.state.dirty = false;
            await this.action.doAction("account.res_partner_action_customer", { clearBreadcrumbs: true });
        }
    }
}

PatientRegistration.template = "lesotho_sale.PatientRegistration";
registry.category("actions").add("lesotho_patient_registration", PatientRegistration);
