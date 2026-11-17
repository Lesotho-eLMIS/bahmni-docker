/** @odoo-module **/

import { registry } from "@web/core/registry";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { listView } from "@web/views/list/list_view";

const patientEditorAction = (patientId) => ({
    type: "ir.actions.client",
    name: "Edit Patient",
    tag: "lesotho_patient_registration",
    target: "current",
    context: { patient_id: patientId },
});

class PatientKanbanController extends kanbanView.Controller {
    async openRecord(record) {
        return this.actionService.doAction(patientEditorAction(record.resId));
    }
}

class PatientListController extends listView.Controller {
    async openRecord(record) {
        return this.actionService.doAction(patientEditorAction(record.resId));
    }
}

registry.category("views").add("lesotho_patients_kanban", { ...kanbanView, Controller: PatientKanbanController });
registry.category("views").add("lesotho_patients_list", { ...listView, Controller: PatientListController });
