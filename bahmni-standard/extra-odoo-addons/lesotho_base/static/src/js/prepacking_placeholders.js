/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

class PrepackingPlaceholder extends Component {}

PrepackingPlaceholder.template = "lesotho_base.PrepackingPlaceholder";

class CreateNewPrescription extends Component {
  setup() {
    this.orm = useService("orm");
    this.action = useService("action");
    this.notification = useService("notification");
    this.state = useState({
      patientId: "",
      weight: "",
      height: "",
      bmi: "",
      bp: "",
      allergies: "",
      careSetting: "",
      dispensaryId: "",
      showCreateConfirmation: false,
      creating: false,
      patientOptions: [],
      productOptions: [],
      careSettingOptions: [],
      dispensaryOptions: [],
      directionOptions: {
        dose_unit: [],
        frequency: [],
        route: [],
        duration_units: [],
        instructions: [],
      },
      lines: [this.newLine()],
    });

    onWillStart(async () => {
      await this.loadOptions();
    });
  }

  newLine() {
    return {
      id: Date.now() + Math.random(),
      productId: "",
      product: "",
      dose: "",
      doseUnit: "",
      frequency: "",
      route: "",
      duration: "",
      durationUnit: "",
      instructions: "",
      additionalInstructions: "",
      prescribedQuantity: "",
    };
  }

  async loadOptions() {
    try {
      const options = await this.orm.call("sale.order", "get_prescription_create_form_options", []);
      this.state.patientOptions = options.patients || [];
      this.state.productOptions = options.products || [];
      this.state.careSettingOptions = options.care_settings || [];
      this.state.dispensaryOptions = options.dispensaries || [];
      this.state.directionOptions = options.direction_options || this.state.directionOptions;
    } catch (error) {
      this.notification.add("Could not load prescription form options.", { type: "danger" });
      console.error(error);
    }
  }

  async goToPrescriptionList() {
    await this.action.doAction("sale.action_quotations_with_onboarding", {
      clearBreadcrumbs: true,
    });
  }

  addLine() {
    this.state.lines.push(this.newLine());
  }

  removeLine(line) {
    if (this.state.lines.length > 1) {
      const index = this.state.lines.indexOf(line);
      if (index !== -1) {
        this.state.lines.splice(index, 1);
      }
    }
  }

  updateLine(line, field, value) {
    const updatedLine = { ...line, [field]: value };
    if (["dose", "frequency", "duration", "durationUnit"].includes(field)) {
      updatedLine.prescribedQuantity = this.calculatePrescribedQuantity(updatedLine);
    }
    this.replaceLine(line, updatedLine);
  }

  updateProduct(line, productId) {
    const parsedId = productId ? Number.parseInt(productId, 10) : "";
    const product = this.state.productOptions.find((option) => option.id === parsedId);
    this.replaceLine(line, {
      ...line,
      productId: parsedId || "",
      product: product ? product.name : "",
    });
  }

  replaceLine(line, updatedLine) {
    const index = this.state.lines.findIndex((candidate) => candidate.id === line.id);
    if (index !== -1) {
      this.state.lines.splice(index, 1, updatedLine);
    }
  }

  updatePatient(patientId) {
    const parsedId = patientId ? Number.parseInt(patientId, 10) : "";
    const patient = this.state.patientOptions.find((option) => option.id === parsedId);
    this.state.patientId = parsedId || "";
    this.state.weight = patient ? patient.weight : "";
    this.state.height = patient ? patient.height : "";
    this.state.bmi = patient ? patient.bmi : "";
    this.state.bp = patient ? patient.bp : "";
    this.state.allergies = patient ? patient.allergies : "";
  }

  requestCreateConfirmation() {
    const validationMessage = this.getValidationMessage();
    if (validationMessage) {
      this.notification.add(validationMessage, { type: "warning" });
      return;
    }
    this.state.showCreateConfirmation = true;
  }

  cancelCreateConfirmation() {
    this.state.showCreateConfirmation = false;
  }

  async createPrescription() {
    if (this.state.creating) {
      return;
    }
    this.state.showCreateConfirmation = false;
    this.state.creating = true;

    try {
      const result = await this.orm.call("sale.order", "create_prescription_from_ui", [
        this.getPrescriptionPayload(),
      ]);
      this.notification.add("Prescription created.", { type: "success" });
      if (result && result.action) {
        await this.action.doAction(result.action, { clearBreadcrumbs: true });
      }
    } catch (error) {
      this.notification.add(error.message || "Prescription could not be created.", { type: "danger" });
    } finally {
      this.state.creating = false;
    }
  }

  getPrescriptionPayload() {
    return {
      patient_id: this.state.patientId,
      care_setting: this.state.careSetting,
      dispensary_id: this.state.dispensaryId,
      lines: this.state.lines.map((line) => ({
        product_id: line.productId,
        prescribed_quantity: this.calculatePrescribedQuantity(line),
        dose: line.dose,
        dose_unit: line.doseUnit,
        frequency: line.frequency,
        route: line.route,
        duration: line.duration,
        duration_unit: line.durationUnit,
        instructions: line.instructions,
        additional_instructions: line.additionalInstructions,
      })),
    };
  }

  getValidationMessage() {
    if (!this.state.patientId) {
      return "Select a patient before creating the prescription.";
    }
    if (!this.state.lines.length) {
      return "Add at least one prescribed item.";
    }
    for (const [index, line] of this.state.lines.entries()) {
      if (!line.productId) {
        return `Select a product for prescribed item ${index + 1}.`;
      }
      const prescribedQuantity = this.calculatePrescribedQuantity(line);
      if (!prescribedQuantity || Number.parseFloat(prescribedQuantity) <= 0) {
        return `Complete dose, frequency, duration and duration unit for prescribed item ${index + 1}.`;
      }
    }
    return "";
  }

  calculatePrescribedQuantity(line) {
    const dose = Number.parseFloat(line.dose);
    const duration = Number.parseFloat(line.duration);
    const frequency = this.getFrequencyMultiplier(line.frequency);

    if (!Number.isFinite(dose) || !Number.isFinite(duration) || !Number.isFinite(frequency)) {
      return "";
    }

    const durationMultiplier = this.getDurationUnitMultiplier(line.durationUnit);

    if (!Number.isFinite(durationMultiplier)) {
      return "";
    }

    const quantity = dose * frequency * duration * durationMultiplier;
    return Number.isInteger(quantity) ? String(quantity) : quantity.toFixed(2);
  }

  getFrequencyMultiplier(value) {
    const frequency = String(value || "").trim().toLowerCase();
    if (!frequency) {
      return NaN;
    }

    const numeric = Number.parseFloat(frequency);
    if (Number.isFinite(numeric)) {
      return numeric;
    }

    if (
      frequency.includes("immediate") ||
      frequency.includes("stat") ||
      frequency.includes("once") ||
      frequency.includes("daily") ||
      frequency === "od" ||
      frequency === "qd" ||
      frequency.includes("q24h")
    ) {
      return 1;
    }
    if (
      frequency.includes("twice") ||
      frequency.includes("bid") ||
      frequency.includes("bd") ||
      frequency.includes("q12h")
    ) {
      return 2;
    }
    if (
      frequency.includes("three") ||
      frequency.includes("tid") ||
      frequency.includes("tds") ||
      frequency.includes("q8h")
    ) {
      return 3;
    }
    if (
      frequency.includes("four") ||
      frequency.includes("qid") ||
      frequency.includes("qds") ||
      frequency.includes("q6h")
    ) {
      return 4;
    }

    return NaN;
  }

  getDurationUnitMultiplier(value) {
    const unit = String(value || "").trim().toLowerCase();
    if (!unit) {
      return 1;
    }

    if (unit.includes("hour")) {
      return 1 / 24;
    }
    if (unit.includes("day")) {
      return 1;
    }
    if (unit.includes("week")) {
      return 7;
    }
    if (unit.includes("month")) {
      return 30;
    }
    if (unit.includes("year")) {
      return 365;
    }

    return 1;
  }
}

CreateNewPrescription.template = "lesotho_base.CreateNewPrescription";

registry.category("actions").add("lesotho_base.create_prepacks_placeholder", PrepackingPlaceholder);
registry.category("actions").add("lesotho_base.authorise_prepacks_placeholder", PrepackingPlaceholder);
registry.category("actions").add("lesotho_base.view_prepacks_placeholder", PrepackingPlaceholder);
registry.category("actions").add("lesotho_base.create_new_prescription_placeholder", CreateNewPrescription);
