/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart } from "@odoo/owl";
import { session } from "@web/session";

export class PrepackDashboard extends Component {
  setup() {
    this.orm = useService("orm");
    this.notification = useService("notification");
    this.state = useState({
      step: 1,
      currentUser: session.name,
      inventory: [],
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
      await this.loadInventory();
    });
  }

  async loadInventory() {
    const inventory = await this.orm.call("bahmni.prepack.batch", "fetch_bulk_inventory", []);
    this.state.inventory = inventory;
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

    this.state.batchId = result.id;
    this.state.batchName = result.name;
    this.goToStep(3);
  }

  async authorizeBatch() {
    await this.orm.call("bahmni.prepack.batch", "action_authorize_batch", [[this.state.batchId]]);
    this.state.isAuthorized = true;
    this.notification.add("Document Authorized successfully!", { type: "success" });
  }
}

PrepackDashboard.template = "lesotho_prepack_batch.PrepackDashboard";

registry.category("actions").add("prepack_dashboard_client_action", PrepackDashboard);
