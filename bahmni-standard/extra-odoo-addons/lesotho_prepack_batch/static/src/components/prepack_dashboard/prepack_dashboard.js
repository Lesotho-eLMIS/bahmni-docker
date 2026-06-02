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
    const inventory = await this.orm.call("bahmni.prepack.batch", "fetch_bulk_inventory", []);
    this.state.inventory = inventory;
  }

  async selectBatch(batchId) {
    const details = await this.orm.call("bahmni.prepack.batch", "fetch_batch_details", [batchId]);
    if (details) {
      this.state.batchId = details.id;
      this.state.batchName = details.name;
      this.state.batchItems = details.items;
      this.state.isAuthorized = details.isAuthorized;
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

  addToBatch() {
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
  }

  removeFromBatch(key) {
    this.state.batchItems = this.state.batchItems.filter(i => i.key !== key);
  }

  goToStep(step) {
    if (step === 3 && !this.state.permissions.can_authorize) {
      this.notification.add("You do not have permission to authorize prepacks.", { type: "danger" });
      return;
    }
    this.state.step = step;
  }
  addTarget(item) {
    item.targets.push({ size: 0, qty: 0 });
  }

  removeTarget(item, index) {
    item.targets.splice(index, 1);
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
      this.notification.add("Please define at least one valid target size and quantity.", { type: "danger" });
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
    await this.orm.call("bahmni.prepack.batch", "action_authorize_batch", [[this.state.batchId]]);
    this.state.isAuthorized = true;
    this.notification.add("Document Authorized successfully!", { type: "success" });

    // Refresh pending batches if in authorize mode
    if (this.state.mode === "authorize") {
      this.state.pendingBatches = await this.orm.call("bahmni.prepack.batch", "fetch_pending_batches", []);
    }
  }

  printLabels() {
    this.notification.add("Printing Barcodes/Labels for all targets...", { type: "info" });
  }
}
PrepackDashboard.template = "lesotho_prepack_batch.PrepackDashboard";

registry.category("actions").add("prepack_dashboard_client_action", PrepackDashboard);
