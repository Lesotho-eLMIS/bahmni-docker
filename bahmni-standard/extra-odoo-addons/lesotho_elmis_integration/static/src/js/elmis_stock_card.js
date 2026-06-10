/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const QUANTITY_MODE_STORAGE_KEY =
  "lesotho_elmis_inventory_quantity_mode";

export class ElmisStockCard extends Component {
  setup() {
    this.orm = useService("orm");
    this.action = useService("action");
    this.notification = useService("notification");
    const context = this.props.action?.context || {};
    this.identifiers = {
      product_id: context.product_id,
      lot_id: context.lot_id,
      location_id: context.location_id,
    };
    this.state = useState({
      loading: true,
      product: {},
      lot: {},
      location: {},
      summary: {},
      movementTypes: [],
      rows: [],
      movementType: "all",
      dateFrom: "",
      dateTo: "",
      quantityMode: this.getStoredQuantityMode(),
    });
    onWillStart(() => this.load());
  }

  async load() {
    this.state.loading = true;
    try {
      const card = await this.orm.call(
        "stock.quant",
        "get_elmis_stock_card",
        [
          {
            ...this.identifiers,
            movement_type: this.state.movementType,
            date_from: this.state.dateFrom || false,
            date_to: this.state.dateTo || false,
          },
        ]
      );
      this.state.product = card.product;
      this.state.lot = card.lot;
      this.state.location = card.location;
      this.state.summary = card.summary;
      this.state.movementTypes = card.movement_types;
      this.state.rows = card.rows;
    } catch (error) {
      this.notification.add(
        error.data?.message || "Could not load this batch stock card.",
        { type: "danger" }
      );
    } finally {
      this.state.loading = false;
    }
  }

  async onMovementTypeChange(ev) {
    this.state.movementType = ev.target.value;
    await this.load();
  }

  async onDateFromChange(ev) {
    this.state.dateFrom = ev.target.value;
    await this.load();
  }

  async onDateToChange(ev) {
    this.state.dateTo = ev.target.value;
    await this.load();
  }

  async clearFilters() {
    this.state.movementType = "all";
    this.state.dateFrom = "";
    this.state.dateTo = "";
    await this.load();
  }

  getStoredQuantityMode() {
    try {
      return window.localStorage.getItem(QUANTITY_MODE_STORAGE_KEY) === "units"
        ? "units"
        : "packs";
    } catch {
      return "packs";
    }
  }

  setQuantityMode(mode) {
    this.state.quantityMode = mode;
    try {
      window.localStorage.setItem(QUANTITY_MODE_STORAGE_KEY, mode);
    } catch {
      // Browser storage may be unavailable in private or restricted sessions.
    }
  }

  async openSource(row) {
    if (!row.source?.model || !row.source?.id) {
      return;
    }
    await this.action.doAction({
      type: "ir.actions.act_window",
      name: row.source.label,
      res_model: row.source.model,
      res_id: row.source.id,
      views: [[false, "form"]],
      target: "current",
    });
  }

  async backToInventory() {
    await this.action.doAction(
      "lesotho_elmis_integration.action_elmis_inventory_dashboard"
    );
  }

  exportStockCard() {
    const params = new URLSearchParams({
      ...this.identifiers,
      movement_type: this.state.movementType,
    });
    if (this.state.dateFrom) {
      params.set("date_from", this.state.dateFrom);
    }
    if (this.state.dateTo) {
      params.set("date_to", this.state.dateTo);
    }
    const link = document.createElement("a");
    link.href = `/lesotho_elmis_integration/stock-card/export?${params.toString()}`;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  formatQuantity(value) {
    const quantity = Number(value || 0);
    const packSize = Number(this.state.product.pack_size || 0);
    if (
      this.state.quantityMode === "units" ||
      packSize <= 0 ||
      quantity < 0
    ) {
      return `${this.formatNumber(quantity)} ${this.unitLabel(quantity)}`;
    }
    const completePacks = Math.floor(quantity / packSize);
    const looseUnits = quantity - completePacks * packSize;
    const parts = [
      `${this.formatNumber(completePacks)} ${
        completePacks === 1 ? "pack" : "packs"
      }`,
    ];
    if (looseUnits) {
      parts.push(
        `${this.formatNumber(looseUnits)} ${this.unitLabel(looseUnits)}`
      );
    }
    return parts.join(" + ");
  }

  formatNumber(value) {
    return new Intl.NumberFormat(undefined, {
      maximumFractionDigits: 2,
    }).format(value || 0);
  }

  unitLabel(value) {
    return Number(value) === 1 ? "unit" : "units";
  }

  formatDate(value) {
    if (!value) {
      return "—";
    }
    return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  formatDateTime(value) {
    if (!value) {
      return "—";
    }
    return new Date(value.replace(" ", "T")).toLocaleString();
  }
}

ElmisStockCard.template = "lesotho_elmis_integration.StockCard";

registry
  .category("actions")
  .add("lesotho_elmis_integration.stock_card", ElmisStockCard);
