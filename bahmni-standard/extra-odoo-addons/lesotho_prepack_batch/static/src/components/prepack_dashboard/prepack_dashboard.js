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
      isAuthorized: false,
      includePrepacks: false,
      hasReleaseDiscrepancy: false,
      releaseDiscrepancyReason: "",
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
            await this.loadInventory();
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
      location_id: this.state.selectedLocationId,
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
      this.state.batchId = details.id;
      this.state.batchName = details.name;
      this.state.batchItems = details.items;
      this.state.isAuthorized = details.isAuthorized;
      this.state.selectedLocationId = details.location_src_id || null;
      this.state.selectedLocationName = details.location_src_name || "";
      this.state.releaseDiscrepancyReason = details.release_discrepancy_reason || "";
      this.state.hasReleaseDiscrepancy = Boolean(this.state.releaseDiscrepancyReason);
    }
    return details;
  }

  clearSelectedBatch() {
    this.state.batchId = null;
    this.state.batchName = "";
    this.state.batchItems = [];
    this.state.isAuthorized = false;
    this.state.hasReleaseDiscrepancy = false;
    this.state.releaseDiscrepancyReason = "";
    if (this.state.mode === "authorize" || this.state.mode === "history") {
      this.state.selectedLocationId = null;
      this.state.selectedLocationName = "";
    }
  }

  async autoSave() {
    if (this.state.mode === "create") {
      await this.orm.call("bahmni.prepack.batch", "save_prepack_batch", [this.state.batchItems], {
        location_id: this.state.selectedLocationId,
      });
    }
  }

  async recordDamagedProducts() {
    if (this.state.batchItems.length === 0) {
      this.notification.add("Add products to the prepacking list before recording damage.", { type: "warning" });
      return;
    }
    const draft = await this.orm.call("bahmni.prepack.batch", "save_prepack_batch", [this.state.batchItems], {
      location_id: this.state.selectedLocationId,
    });
    this.state.batchId = draft.id;
    this.state.batchName = draft.name;
    await this.orm.write("bahmni.prepack.batch", [draft.id], {
      has_damaged_products: true,
    });
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
    return `${item.name} (${item.batch})`;
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
      target[fieldName] = 0;
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
    const item = { ...this.state.selectedProduct, targets: [{ size: 0, qty: 0, packaging_material_id: null, packaging_material_name: "", packaging_material_soh: 0, packaging_material_uom: "" }] };
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
    item.targets.push({ size: 0, qty: 0, packaging_material_id: null, packaging_material_name: "", packaging_material_soh: 0, packaging_material_uom: "" });
    await this.autoSave();
  }

  async onPackagingMaterialChange(target, ev) {
    const productId = parseInt(ev.target.value, 10) || null;
    const product = this.state.packagingMaterials.find(item => item.id === productId);
    target.packaging_material_id = productId;
    target.packaging_material_name = product ? product.name : "";
    target.packaging_material_soh = product ? product.soh : 0;
    target.packaging_material_uom = product ? product.uom : "";
    await this.autoSave();
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
      location_id: this.state.selectedLocationId,
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
    if (this.state.hasReleaseDiscrepancy && !this.state.releaseDiscrepancyReason.trim()) {
      this.notification.add("Please record the discrepancy reason before releasing.", { type: "danger" });
      return;
    }
    await this.orm.call(
      "bahmni.prepack.batch",
      "action_release_batch",
      [[this.state.batchId], this.state.hasReleaseDiscrepancy ? this.state.releaseDiscrepancyReason.trim() : ""]
    );
    this.notification.add("Batch released successfully!", { type: "success" });

    if (this.state.mode === "authorize") {
      await this.loadPendingBatches();
    }
    this.state.isAuthorized = true;
    // Refresh batch details to update all lines and show the print button.
    await this.selectBatch(this.state.batchId);
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
    await this.orm.call("bahmni.prepack.batch.line", "action_authorize_line", [[lineId]]);
    this.notification.add("Item validated for release successfully!", { type: "success" });

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
    await this.actionService.doAction(action);
    if (this.state.mode === "authorize" && this.state.isAuthorized) {
      await this.loadPendingBatches();
      this.clearSelectedBatch();
    }
  }

  async printLineLabel(lineId) {
    const action = await this.orm.call(
      "bahmni.prepack.batch.line",
      "action_print_prepack_label",
      [[lineId]]
    );
    await this.actionService.doAction(action);
  }
}
PrepackDashboard.template = "lesotho_prepack_batch.PrepackDashboard";

registry.category("actions").add("prepack_dashboard_client_action", PrepackDashboard);
