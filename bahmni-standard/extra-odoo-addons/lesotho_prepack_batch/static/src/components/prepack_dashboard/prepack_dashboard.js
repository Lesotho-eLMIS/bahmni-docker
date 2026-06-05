/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart } from "@odoo/owl";
import { session } from "@web/session";

export class PrepackDashboard extends Component {
  setup() {
    this.orm = useService("orm");
    this.notification = useService("notification");
    this.actionService = useService("action");
    this.printedPrepackLineIds = new Set();
    this.state = useState({
      step: 1,
      mode: "all", // "all", "create", "authorize"
      currentUser: session.name,
      permissions: { can_create: false, can_authorize: false },
      inventory: [],
      packagingMaterials: [],
      prepackLocations: [],
      selectedLocationId: null,
      selectedLocationName: "",
      authorizationLocationId: null,
      pendingBatches: [],
      historyBatches: [],
      productSearch: "",
      selectedProductKey: null,
      selectedProduct: null,
      checks: { chk1: false, chk2: false, chk3: false },
      canAddToBatch: false,
      batchItems: [],
      batchId: null,
      batchName: "",
      batchState: null,
      isAuthorized: false,
      includePrepacks: false,
    });
    onWillStart(async () => {
      this.state.permissions = await this.orm.call("bahmni.prepack.batch", "check_prepack_permissions", []);

      const context = this.props.action.context || {};
      this.state.mode = context.mode || "all";

      if (this.state.mode === "authorize") {
        this.state.step = 3;
        await this.loadPrepackLocations();
        this.state.authorizationLocationId = this.state.selectedLocationId;
        await this.loadPendingBatches();
      } else if (this.state.mode === "history") {
        this.state.step = 4; // History step
        this.state.historyBatches = await this.orm.call("bahmni.prepack.batch", "fetch_batch_history", []);
      } else {
        if (this.state.permissions.can_create) {
          this.state.step = 1;
          await this.loadPrepackLocations();
          await this.loadPackagingMaterials();
          await this.loadInventory();

          // Load draft if exists
          const draft = await this.orm.call("bahmni.prepack.batch", "fetch_draft_batch", []);
          if (draft) {
            this.state.selectedLocationId = draft.location_id || this.state.selectedLocationId;
            this.state.selectedLocationName = draft.location_src_name || this.getLocationName(this.state.selectedLocationId);
            this.state.batchItems = draft.items;
            await this.loadPackagingMaterials();
            await this.loadInventory();
            this.normalizeBatchItems();
          }
        } else if (this.state.permissions.can_authorize) {
          this.state.step = 3;
          await this.loadPrepackLocations();
          this.state.authorizationLocationId = this.state.selectedLocationId;
          await this.loadPendingBatches();
        }
      }

      if (context.active_id) {
        await this.selectBatch(context.active_id);
      }
    });
  }

  async loadInventory() {
    const inventory = await this.orm.call("bahmni.prepack.batch", "fetch_bulk_inventory", [], {
      include_prepacks: this.state.includePrepacks,
      location_src_id: this.state.selectedLocationId,
    });
    this.state.inventory = inventory;
  }

  async loadPackagingMaterials() {
    this.state.packagingMaterials = await this.orm.call("bahmni.prepack.batch", "fetch_packaging_materials", [], {
      location_id: this.state.selectedLocationId,
    });
  }

  async loadPrepackLocations() {
    const result = await this.orm.call("bahmni.prepack.batch", "fetch_prepack_locations", []);
    this.state.prepackLocations = result.locations;
    this.state.selectedLocationId = result.default_location_id || (result.locations[0] && result.locations[0].id) || null;
    this.state.selectedLocationName = this.getLocationName(this.state.selectedLocationId);
  }

  async loadPendingBatches() {
    this.state.pendingBatches = await this.orm.call("bahmni.prepack.batch", "fetch_pending_batches", [], {
      location_id: this.state.authorizationLocationId,
    });
  }

  async onAuthorizationLocationChange(ev) {
    this.state.authorizationLocationId = parseInt(ev.target.value, 10) || null;
    await this.loadPendingBatches();
  }

  async onLocationChange(ev) {
    const locationId = parseInt(ev.target.value, 10) || null;
    if (!this.canEditLocation()) {
      ev.target.value = this.state.selectedLocationId || "";
      return;
    }
    if (this.state.batchItems.length > 0 && locationId !== this.state.selectedLocationId) {
      if (!confirm("Changing the prepacking location will clear the current prepacking list. Continue?")) {
        ev.target.value = this.state.selectedLocationId || "";
        return;
      }
      this.state.batchItems = [];
      this.state.step = 1;
    }
    this.state.selectedLocationId = locationId;
    this.state.selectedLocationName = this.getLocationName(locationId);
    this.resetSelectedProduct();
    await this.loadPackagingMaterials();
    await this.loadInventory();
    await this.autoSave();
  }

  canEditLocation() {
    return this.state.step === 1 && !this.state.batchId;
  }

  getLocationName(locationId) {
    const location = this.state.prepackLocations.find(item => item.id === locationId);
    return location ? location.name : "";
  }

  async onIncludePrepacksToggle() {
    await this.loadInventory();
  }

  async selectBatch(batchId) {
    const details = await this.orm.call("bahmni.prepack.batch", "fetch_batch_details", [batchId]);
    if (details) {
      if (this.state.batchId !== details.id) {
        this.printedPrepackLineIds.clear();
      }
      this.state.batchId = details.id;
      this.state.batchName = details.name;
      this.state.batchState = details.state;
      this.state.batchItems = details.items;
      this.normalizeBatchItems();
      this.state.isAuthorized = details.isAuthorized;
      this.state.selectedLocationId = details.location_src_id || null;
      this.state.selectedLocationName = details.location_src_name || "";
      if (this.state.mode === "history" && details.state === "pending_auth" && this.state.permissions.can_authorize) {
        this.state.step = 3;
      }
    }
    return details;
  }

  clearSelectedBatch() {
    this.printedPrepackLineIds.clear();
    this.state.batchId = null;
    this.state.batchName = "";
    this.state.batchState = null;
    this.state.batchItems = [];
    this.state.isAuthorized = false;
    if (this.state.mode === "authorize" || this.state.mode === "history") {
      this.state.selectedLocationId = null;
      this.state.selectedLocationName = "";
    }
  }

  async autoSave() {
    if (this.state.mode === "create") {
      await this.orm.call("bahmni.prepack.batch", "save_prepack_batch", [this.state.batchItems], {
        location_src_id: this.state.selectedLocationId,
      });
    }
  }

  async recordDamagedProducts() {
    if (this.state.batchItems.length === 0) {
      this.notification.add("Add products to the prepacking list before recording damage.", { type: "warning" });
      return;
    }
    const draft = await this.orm.call("bahmni.prepack.batch", "save_prepack_batch", [this.state.batchItems], {
      location_src_id: this.state.selectedLocationId,
    });
    this.state.batchId = draft.id;
    this.state.batchName = draft.name;
    await this.actionService.doAction({
      type: "ir.actions.act_window",
      name: "Record Damaged Products",
      res_model: "prepack.damage.wizard",
      views: [[false, "form"]],
      target: "new",
      context: {
        default_prepack_job_id: draft.id,
        from_prepack_dashboard: true,
      },
    });
  }

  onProductSelect(ev) {
    const selectedKey = ev.target.value;
    this.selectProduct(this.state.inventory.find(i => i.key === selectedKey));
  }

  onProductSearch(ev) {
    const searchValue = ev.target.value;
    this.state.productSearch = searchValue;

    const selectedProduct = this.state.inventory.find((item) => this.getProductLabel(item) === searchValue);
    this.selectProduct(selectedProduct, { keepSearchValue: true });
  }

  resetSelectedProduct() {
    this.state.selectedProductKey = null;
    this.state.selectedProduct = null;
    this.state.productSearch = "";
    this.state.checks = { chk1: false, chk2: false, chk3: false };
    this.state.canAddToBatch = false;
  }

  selectProduct(product, options = {}) {
    if (!product) {
      this.state.selectedProductKey = null;
      this.state.selectedProduct = null;
      if (!options.keepSearchValue) {
        this.state.productSearch = "";
      }
      this.state.checks = { chk1: false, chk2: false, chk3: false };
      this.validateChecklist();
      return;
    }

    this.state.selectedProductKey = product.key;
    this.state.selectedProduct = product;
    this.state.productSearch = this.getProductLabel(product);
    this.state.checks = { chk1: false, chk2: false, chk3: false };
    this.validateChecklist();
  }

  getProductLabel(item) {
    return item.location_name ? `${item.name} (${item.batch}) - ${item.location_name}` : `${item.name} (${item.batch})`;
  }

  getPackagingMaterialLabel(material) {
    return `${material.name} (${material.soh} ${material.uom})`;
  }

  getPackagingMaterialById(materialId) {
    return this.state.packagingMaterials.find(item => item.id === materialId) || null;
  }

  getBlankZeroValue(value) {
    return Number(value) === 0 ? "" : value;
  }

  normalizeBatchItems() {
    for (const item of this.state.batchItems) {
      for (const target of item.targets) {
        const material = this.getPackagingMaterialById(target.packaging_material_id);
        if (material) {
          this.applyPackagingMaterial(target, material);
        } else if (target.packaging_material_name) {
          target.packaging_material_search = target.packaging_material_name;
        }
        target.expected_qty = Number(target.expected_qty || target.qty || 0);
        target.actual_qty = Number(target.actual_qty || target.expected_qty || 0);
        this.updateReleaseDiscrepancy(target);
        this.markReleaseTargetValuesSaved(target);
      }
    }
  }

  preventNonNumericInput(ev, options = {}) {
    const allowedKeys = [
      "Backspace",
      "Delete",
      "Tab",
      "Enter",
      "Escape",
      "ArrowLeft",
      "ArrowRight",
      "ArrowUp",
      "ArrowDown",
      "Home",
      "End",
    ];
    if (
      allowedKeys.includes(ev.key) ||
      ((ev.ctrlKey || ev.metaKey) && ["a", "c", "v", "x"].includes(ev.key.toLowerCase()))
    ) {
      return;
    }
    if (options.allowDecimal && ["Decimal", "."].includes(ev.key) && !ev.target.value.includes(".")) {
      return;
    }
    if (!/^\d$/.test(ev.key)) {
      ev.preventDefault();
    }
  }

  onNumericFieldInput(target, fieldName, ev, options = {}) {
    const numericValue = options.allowDecimal
      ? ev.target.value.replace(/[^\d.]/g, "").replace(/(\..*)\./g, "$1")
      : ev.target.value.replace(/\D/g, "");
    ev.target.value = numericValue;
    if (numericValue === "") {
      target[fieldName] = "";
    } else if (options.allowDecimal) {
      target[fieldName] = numericValue;
    } else {
      target[fieldName] = parseInt(numericValue, 10);
    }
  }

  validateChecklist() {
    if (!this.state.selectedProduct) {
      this.state.canAddToBatch = false;
      return;
    }
    const { chk1, chk2, chk3 } = this.state.checks;
    const needsLiquid = this.state.selectedProduct.reqLiquidCheck;
    this.state.canAddToBatch = chk1 && chk2 && (needsLiquid ? chk3 : true);
  }

  async addToBatch() {
    if (this.state.batchItems.find(i => i.key === this.state.selectedProduct.key)) {
      this.notification.add("This product lot is already in your batch list.", { type: "danger" });
      return;
    }
    const item = { ...this.state.selectedProduct, targets: [] };
    item.targets.push(this.getEmptyTarget());
    this.state.batchItems.push(item);

    this.resetSelectedProduct();

    await this.autoSave();
  }

  async removeFromBatch(key) {
    this.state.batchItems = this.state.batchItems.filter(i => i.key !== key);
    await this.autoSave();
  }

  async clearBatch() {
    if (confirm("Are you sure you want to clear all items from the batch list?")) {
      this.state.batchItems = [];
      await this.autoSave();
    }
  }

  goToStep(step) {
    if (step === 3 && !this.state.permissions.can_authorize) {
      this.notification.add("You do not have permission to authorize prepacks.", { type: "danger" });
      return;
    }
    this.state.step = step;
  }

  goBack() {
    if (this.state.batchId) {
      this.clearSelectedBatch();
      if (this.state.mode === "history") {
        this.state.step = 4;
      }
      return;
    }
    if (this.state.step === 2) {
      this.goToStep(1);
      return;
    }
    if (this.state.step === 3 && this.state.mode === "all" && this.state.permissions.can_create) {
      this.goToStep(this.state.batchItems.length > 0 ? 2 : 1);
      return;
    }
    window.history.back();
  }

  getBackLabel() {
    if (this.state.batchId && this.state.mode === "history") {
      return "Back to History";
    }
    if (this.state.batchId && this.state.mode === "authorize") {
      return "Back to List";
    }
    if (this.state.step === 2) {
      return "Back to Prepacking List";
    }
    return "Back";
  }

  async addTarget(item) {
    item.targets.push(this.getEmptyTarget());
    await this.autoSave();
  }

  getEmptyTarget(material = null) {
    const target = {
      size: "",
      qty: "",
      packaging_material_id: null,
      packaging_material_name: "",
      packaging_material_search: "",
      packaging_material_soh: 0,
      packaging_material_uom: "",
    };
    this.applyPackagingMaterial(target, material);
    return target;
  }

  async onPackagingMaterialSearchInput(target, ev) {
    target.packaging_material_search = ev.target.value;
    const material = this.state.packagingMaterials.find(
      item => this.getPackagingMaterialLabel(item) === target.packaging_material_search
    );
    if (material) {
      await this.setPackagingMaterial(target, material);
    } else {
      this.clearPackagingMaterial(target);
    }
  }

  async setPackagingMaterial(target, product) {
    this.applyPackagingMaterial(target, product);
    await this.autoSave();
  }

  applyPackagingMaterial(target, product) {
    target.packaging_material_id = product ? product.id : null;
    target.packaging_material_name = product ? product.name : "";
    target.packaging_material_search = product ? this.getPackagingMaterialLabel(product) : "";
    target.packaging_material_soh = product ? product.soh : 0;
    target.packaging_material_uom = product ? product.uom : "";
  }

  clearPackagingMaterial(target) {
    target.packaging_material_id = null;
    target.packaging_material_name = "";
    target.packaging_material_soh = 0;
    target.packaging_material_uom = "";
  }

  async removeTarget(item, index) {
    item.targets.splice(index, 1);
    await this.autoSave();
  }

  getTotalUsed(item) {
    return item.targets.reduce((acc, t) => acc + (t.size * t.qty), 0);
  }

  async submitBatch() {
    // Validation
    let isValid = true;
    let hasTargets = false;
    const payload = [];

    for (const item of this.state.batchItems) {
      const used = this.getTotalUsed(item);
      if (used > item.soh) {
        this.notification.add(`Insufficient stock for ${item.name}.`, { type: "danger" });
        isValid = false;
      }
      const validTargets = item.targets.filter(t => t.size > 0 && t.qty > 0);
      if (validTargets.some(t => !t.packaging_material_id)) {
        this.notification.add(`Select packaging material for ${item.name}.`, { type: "danger" });
        isValid = false;
      }
      if (validTargets.length > 0) {
        hasTargets = true;
        payload.push({
          id: item.id,
          lot_id: item.lot_id,
          location_id: item.location_id,
          targets: validTargets,
        });
      }
    }

    if (!isValid) return;
    if (!hasTargets) {
      this.notification.add("Please define at least one valid prepack size and quantity.", { type: "danger" });
      return;
    }
    if (!this.state.selectedLocationId) {
      this.notification.add("Please select a prepacking location before submitting for release.", { type: "danger" });
      return;
    }

    const result = await this.orm.call("bahmni.prepack.batch", "submit_prepack_batch", [payload], {
      location_src_id: this.state.selectedLocationId,
    });

    this.notification.add(`Batch ${result.name} submitted for release.`, { type: "success" });

    if (this.state.mode === "create") {
      // Switch to release validation mode
      await this.actionService.doAction("lesotho_base.action_authorise_prepacks_placeholder", {
        clearBreadcrumbs: true,
        additional_context: { active_id: result.id },
      });
    } else {
      this.state.batchId = result.id;
      this.state.batchName = result.name;
      this.goToStep(3);
    }
  }

  async releaseBatch() {
    if (!this.validateReleaseValues()) {
      return;
    }
    if (!this.validateReleaseDiscrepancies()) {
      return;
    }
    await this.saveAllReleaseTargetValues();
    await this.saveAllLineDiscrepancies();
    await this.orm.call(
      "bahmni.prepack.batch",
      "action_release_batch",
      [[this.state.batchId]]
    );
    this.notification.add("Batch released successfully!", { type: "success" });

    if (this.state.mode === "authorize") {
      await this.loadPendingBatches();
    }
    this.state.isAuthorized = true;
    // Refresh batch details to update all lines and show the print button.
    await this.selectBatch(this.state.batchId);
  }

  getReleaseTargets() {
    return this.state.batchItems.flatMap(item =>
      item.targets.filter(target => target.line_id)
    );
  }

  validateReleaseDiscrepancies(targets = this.getReleaseTargets()) {
    for (const target of targets) {
      this.updateReleaseDiscrepancy(target);
    }
    const missingReason = targets.some(
      target => target.has_release_discrepancy && !(target.release_discrepancy_reason || "").trim()
    );
    if (missingReason) {
      this.notification.add("Please enter a discrepancy explanation.", { type: "warning" });
      return false;
    }
    const missingQualityCheck = targets.some(target => !target.quality_check_completed);
    if (missingQualityCheck) {
      this.notification.add("Please tick Quality Check Completed before releasing.", { type: "warning" });
      return false;
    }
    return true;
  }

  validateReleaseValues(targets = this.getReleaseTargets()) {
    const invalidTarget = targets.find(target => Number(target.size) <= 0 || Number(target.actual_qty) <= 0);
    if (invalidTarget) {
      this.notification.add("Pack size and actual prepacks must be greater than zero.", { type: "warning" });
      return false;
    }
    return true;
  }

  isReleaseActionDisabled(target) {
    if (!target.quality_check_completed) {
      return true;
    }
    return Boolean(target.has_release_discrepancy && !(target.release_discrepancy_reason || "").trim());
  }

  isInvalidReleaseValue(value) {
    return Number(value) <= 0;
  }

  getReleaseBulkUsage(target) {
    return Number(target.size || 0) * Number(target.actual_qty || 0);
  }

  markReleaseTargetValuesSaved(target) {
    target._saved_size = Number(target.size || 0);
    target._saved_actual_qty = Number(target.actual_qty || target.expected_qty || target.qty || 0);
    target._saved_quality_check_completed = Boolean(target.quality_check_completed);
    target._saved_release_discrepancy_reason = target.release_discrepancy_reason || "";
  }

  hasReleaseTargetValueChanges(target) {
    return (
      Number(target.size || 0) !== Number(target._saved_size || 0) ||
      Number(target.actual_qty || 0) !== Number(target._saved_actual_qty || 0) ||
      Boolean(target.quality_check_completed) !== Boolean(target._saved_quality_check_completed) ||
      (target.release_discrepancy_reason || "") !== (target._saved_release_discrepancy_reason || "")
    );
  }

  updateReleaseDiscrepancy(target) {
    const expectedQty = Number(target.expected_qty || target.qty || 0);
    const actualQty = Number(target.actual_qty || 0);
    target.has_release_discrepancy = expectedQty !== actualQty;
    if (!target.has_release_discrepancy) {
      target.release_discrepancy_reason = "";
    }
  }

  onReleaseActualQtyInput(target, ev) {
    this.onNumericFieldInput(target, "actual_qty", ev);
    this.updateReleaseDiscrepancy(target);
  }

  async saveReleaseTargetValues(target) {
    if (!target.line_id) {
      return;
    }
    if (!this.validateReleaseValues([target])) {
      return;
    }
    if (!this.hasReleaseTargetValueChanges(target)) {
      return;
    }
    await this.orm.call(
      "bahmni.prepack.batch.line",
      "action_update_release_values",
      [
        [target.line_id],
        target.size,
        target.actual_qty,
        Boolean(target.quality_check_completed),
        target.release_discrepancy_reason || "",
      ]
    );
    this.markReleaseTargetValuesSaved(target);
  }

  async saveAllReleaseTargetValues() {
    for (const target of this.getReleaseTargets()) {
      await this.saveReleaseTargetValues(target);
    }
  }

  onLineDiscrepancyReasonInput(target, ev) {
    target.release_discrepancy_reason = ev.target.value;
  }

  async onQualityCheckToggle(target, ev) {
    target.quality_check_completed = ev.target.checked;
    await this.saveReleaseTargetValues(target);
  }

  async saveLineDiscrepancy(target) {
    if (!target.line_id) {
      return;
    }
    this.updateReleaseDiscrepancy(target);
    await this.saveReleaseTargetValues(target);
  }

  async saveAllLineDiscrepancies() {
    for (const target of this.getReleaseTargets()) {
      await this.saveLineDiscrepancy(target);
    }
  }

  async rejectBatch() {
    if (!this.state.batchId) return;
    const comment = this.state.rejectComment || "";
    await this.orm.call("bahmni.prepack.batch", "action_reject_batch", [[this.state.batchId], comment]);
    this.notification.add("Batch rejected and sent back to creator.", { type: "success" });

    // Refresh view
    this.clearSelectedBatch();
    if (this.state.mode === "authorize") {
      await this.loadPendingBatches();
    }
  }

  async deleteBatch() {
    if (!this.state.batchId) return;
    if (!confirm("Are you sure you want to delete this prepacking batch? This cannot be undone.")) {
      return;
    }
    await this.orm.call("bahmni.prepack.batch", "unlink", [[this.state.batchId]]);
    this.notification.add("Prepacking batch deleted successfully.", { type: "success" });
    this.clearSelectedBatch();
    if (this.state.mode === "authorize") {
      await this.loadPendingBatches();
    }
  }

  async releaseLine(lineId) {
    const target = this.getReleaseTargets().find(item => item.line_id === lineId);
    if (target && !this.validateReleaseValues([target])) {
      return;
    }
    if (target && !this.validateReleaseDiscrepancies([target])) {
      return;
    }
    if (target) {
      await this.saveReleaseTargetValues(target);
      await this.saveLineDiscrepancy(target);
    }
    await this.orm.call("bahmni.prepack.batch.line", "action_authorize_line", [[lineId]]);
    this.notification.add("Item released successfully!", { type: "success" });

    // Refresh batch details
    await this.selectBatch(this.state.batchId);

    // Refresh pending batches if in authorize mode
    if (this.state.mode === "authorize") {
      await this.loadPendingBatches();
    }
  }

  async rejectLine(lineId) {
    // For individual line rejection, we'll just cancel/reject the line's MO
    // This is a simpler action than batch rejection
    try {
      await this.orm.call("bahmni.prepack.batch.line", "action_reject_line", [[lineId]]);
      this.notification.add("Item rejected successfully!", { type: "success" });
      
      // Refresh batch details to show updated state
      await this.selectBatch(this.state.batchId);
      
      // Refresh pending batches if in authorize mode
      if (this.state.mode === "authorize") {
        await this.loadPendingBatches();
      }
    } catch (error) {
      this.notification.add("Failed to reject item: " + error.message, { type: "danger" });
    }
  }

  async printLabels() {
    if (!this.state.batchId) {
      this.notification.add("Select a released batch before printing labels.", { type: "warning" });
      return;
    }
    const action = await this.orm.call(
      "bahmni.prepack.batch",
      "action_print_prepack_labels",
      [[this.state.batchId]]
    );
    await this.doPrintAction(action, true);
  }

  async printLineLabel(lineId) {
    const action = await this.orm.call(
      "bahmni.prepack.batch.line",
      "action_print_prepack_label",
      [[lineId]]
    );
    this.printedPrepackLineIds.add(lineId);
    await this.doPrintAction(action, this.hasPrintedAllReleasedLineLabels());
  }

  hasPrintedAllReleasedLineLabels() {
    const releasedLineIds = this.getReleaseTargets()
      .filter(target => target.state === "done")
      .map(target => target.line_id);
    return releasedLineIds.length > 0 && releasedLineIds.every(
      lineId => this.printedPrepackLineIds.has(lineId)
    );
  }

  async doPrintAction(action, returnToList = false) {
    if (!returnToList) {
      await this.actionService.doAction(action);
      return;
    }
    let returnedToList = false;
    const returnToPrepackList = async () => {
      if (returnedToList) {
        return;
      }
      returnedToList = true;
      await this.actionService.doAction({
        type: "ir.actions.client",
        name: "Release Prepacks",
        tag: "prepack_dashboard_client_action",
        target: "current",
        context: { mode: "authorize" },
      }, {
        clearBreadcrumbs: true,
      });
    };
    await this.actionService.doAction(action, {
      onClose: returnToPrepackList,
    });
    await returnToPrepackList();
  }
}
PrepackDashboard.template = "lesotho_prepack_batch.PrepackDashboard";

registry.category("actions").add("prepack_dashboard_client_action", PrepackDashboard);
