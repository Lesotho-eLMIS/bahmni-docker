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
      pendingBatches: [],
      historyBatches: [],
      selectedProductKey: null,
      selectedProduct: null,
      checks: { chk1: false, chk2: false, chk3: false },
      canAddToBatch: false,
      batchItems: [],
      batchId: null,
      batchName: "",
      isAuthorized: false,
      includePrepacks: false,
      rejectComment: "",
    });
    onWillStart(async () => {
      this.state.permissions = await this.orm.call("bahmni.prepack.batch", "check_prepack_permissions", []);

      const context = this.props.action.context || {};
      this.state.mode = context.mode || "all";

      if (this.state.mode === "authorize") {
        this.state.step = 3;
        this.state.pendingBatches = await this.orm.call("bahmni.prepack.batch", "fetch_pending_batches", []);
      } else if (this.state.mode === "history") {
        this.state.step = 4; // History step
        this.state.historyBatches = await this.orm.call("bahmni.prepack.batch", "fetch_batch_history", []);
      } else {
        if (this.state.permissions.can_create) {
          this.state.step = 1;
          await this.loadInventory();

          // Load draft if exists
          const draft = await this.orm.call("bahmni.prepack.batch", "fetch_draft_batch", []);
          if (draft) {
            this.state.batchItems = draft.items;
          }
        } else if (this.state.permissions.can_authorize) {
          this.state.step = 3;
          this.state.pendingBatches = await this.orm.call("bahmni.prepack.batch", "fetch_pending_batches", []);
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
    });
    this.state.inventory = inventory;
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
      this.state.rejectComment = "";
    }
  }

  async autoSave() {
    if (this.state.mode === "create") {
      await this.orm.call("bahmni.prepack.batch", "save_prepack_batch", [this.state.batchItems]);
    }
  }

  onProductSelect(ev) {
    const selectedKey = ev.target.value;
    this.state.selectedProductKey = selectedKey;
    this.state.selectedProduct = this.state.inventory.find(i => i.key === selectedKey);
    this.state.checks = { chk1: false, chk2: false, chk3: false };
    this.validateChecklist();
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
    const item = { ...this.state.selectedProduct, targets: [{ size: 0, qty: 0 }] };
    this.state.batchItems.push(item);

    // Reset
    this.state.selectedProductKey = null;
    this.state.selectedProduct = null;
    this.state.checks = { chk1: false, chk2: false, chk3: false };
    this.state.canAddToBatch = false;

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
  async addTarget(item) {
    item.targets.push({ size: 0, qty: 0 });
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

    const result = await this.orm.call("bahmni.prepack.batch", "submit_prepack_batch", [payload]);

    this.notification.add(`Batch ${result.name} submitted for authorization.`, { type: "success" });

    if (this.state.mode === "create") {
      // Switch to Authorize mode
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

  async authorizeBatch() {
    const action = await this.orm.call("bahmni.prepack.batch", "action_authorize_batch", [[this.state.batchId]]);
    this.state.isAuthorized = true;
    this.notification.add("Batch Authorized successfully!", { type: "success" });

    // Refresh batch details to update all lines
    await this.selectBatch(this.state.batchId);

    // Refresh pending batches if in authorize mode
    if (this.state.mode === "authorize") {
      this.state.pendingBatches = await this.orm.call("bahmni.prepack.batch", "fetch_pending_batches", []);
    }

    // If the server returned a print action, execute it immediately
    if (action && typeof action === "object") {
      await this.actionService.doAction(action);
    }
  }

  async rejectBatch() {
    if (!this.state.batchId) return;
    const comment = this.state.rejectComment || "";
    await this.orm.call("bahmni.prepack.batch", "action_reject_batch", [[this.state.batchId], comment]);
    this.notification.add("Batch rejected and sent back to creator.", { type: "success" });

    // Refresh view
    this.state.batchId = null;
    this.state.batchName = "";
    this.state.batchItems = [];
    this.state.isAuthorized = false;
    this.state.rejectComment = "";
    if (this.state.mode === "authorize") {
      this.state.pendingBatches = await this.orm.call("bahmni.prepack.batch", "fetch_pending_batches", []);
    }
  }

  async deleteBatch() {
    if (!this.state.batchId) return;
    if (!confirm("Are you sure you want to delete this prepacking batch? This cannot be undone.")) {
      return;
    }
    await this.orm.call("bahmni.prepack.batch", "unlink", [[this.state.batchId]]);
    this.notification.add("Prepacking batch deleted successfully.", { type: "success" });
    this.state.batchId = null;
    this.state.batchName = "";
    this.state.batchItems = [];
    this.state.isAuthorized = false;
    if (this.state.mode === "authorize") {
      this.state.pendingBatches = await this.orm.call("bahmni.prepack.batch", "fetch_pending_batches", []);
    }
  }

  async authorizeLine(lineId) {
    await this.orm.call("bahmni.prepack.batch.line", "action_authorize_line", [[lineId]]);
    this.notification.add("Item Authorized successfully!", { type: "success" });

    // Refresh batch details
    await this.selectBatch(this.state.batchId);

    // Refresh pending batches if in authorize mode
    if (this.state.mode === "authorize") {
      this.state.pendingBatches = await this.orm.call("bahmni.prepack.batch", "fetch_pending_batches", []);
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
        this.state.pendingBatches = await this.orm.call("bahmni.prepack.batch", "fetch_pending_batches", []);
      }
    } catch (error) {
      this.notification.add("Failed to reject item: " + error.message, { type: "danger" });
    }
  }

  async printLabels() {
    if (!this.state.batchId) {
      this.notification.add("Select an authorized batch before printing labels.", { type: "warning" });
      return;
    }
    const action = await this.orm.call(
      "bahmni.prepack.batch",
      "action_print_prepack_labels",
      [[this.state.batchId]]
    );
    await this.actionService.doAction(action);
  }
}
PrepackDashboard.template = "lesotho_prepack_batch.PrepackDashboard";

registry.category("actions").add("prepack_dashboard_client_action", PrepackDashboard);
