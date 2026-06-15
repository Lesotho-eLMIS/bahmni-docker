/** @odoo-module **/

import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const QUANTITY_MODE_STORAGE_KEY =
  "lesotho_elmis_inventory_quantity_mode";

export class ElmisInventoryDashboard extends Component {
  setup() {
    this.orm = useService("orm");
    this.action = useService("action");
    this.notification = useService("notification");
    this.dashboardRef = useRef("dashboard");
    this.tableRef = useRef("table");
    this.searchTimer = null;
    this.state = useState({
      loading: true,
      locations: [],
      programs: [],
      selectedLocation: false,
      summary: {},
      products: [],
      sync: {},
      deliveredToday: 0,
      actions: {},
      page: 1,
      pages: 1,
      total: 0,
      search: "",
      programId: "",
      status: "all",
      sort: "name",
      quantityMode: this.getStoredQuantityMode(),
      expanded: {},
    });
    onWillStart(() => this.load());
  }

  async load() {
    this.state.loading = true;
    try {
      const data = await this.orm.call(
        "stock.quant",
        "get_elmis_inventory_dashboard",
        [
          {
            location_id: this.state.selectedLocation
              ? this.state.selectedLocation.id
              : false,
            search: this.state.search,
            program_id: this.state.programId || false,
            status: this.state.status,
            sort: this.state.sort,
            page: this.state.page,
            page_size: 25,
          },
        ]
      );
      this.state.locations = data.locations;
      this.state.programs = data.programs;
      this.state.selectedLocation = data.selected_location;
      this.state.summary = data.summary;
      this.state.products = data.products;
      this.state.sync = data.sync;
      this.state.deliveredToday = data.delivered_today;
      this.state.actions = data.actions;
      this.state.page = data.pagination.page;
      this.state.pages = data.pagination.pages;
      this.state.total = data.pagination.total;
    } catch (error) {
      this.notification.add(
        error.data?.message || "Could not load the eLMIS inventory dashboard.",
        { type: "danger" }
      );
    } finally {
      this.state.loading = false;
    }
  }

  onSearchInput(ev) {
    this.state.search = ev.target.value;
    this.state.page = 1;
    clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => this.load(), 300);
  }

  async onLocationChange(ev) {
    const id = Number(ev.target.value);
    this.state.selectedLocation =
      this.state.locations.find((location) => location.id === id) || false;
    this.state.page = 1;
    this.state.expanded = {};
    await this.load();
  }

  async onProgramChange(ev) {
    this.state.programId = ev.target.value ? Number(ev.target.value) : "";
    this.state.page = 1;
    await this.load();
  }

  async onSortChange(ev) {
    this.state.sort = ev.target.value;
    this.state.page = 1;
    await this.load();
  }

  async filterStatus(status) {
    this.state.status = this.state.status === status ? "all" : status;
    this.state.page = 1;
    await this.load();
  }

  async clearFilters() {
    this.state.search = "";
    this.state.programId = "";
    this.state.status = "all";
    this.state.sort = "name";
    this.state.page = 1;
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
    if (!["packs", "units"].includes(mode)) {
      return;
    }
    this.state.quantityMode = mode;
    try {
      window.localStorage.setItem(QUANTITY_MODE_STORAGE_KEY, mode);
    } catch {
      // Browser storage may be unavailable in private or restricted sessions.
    }
  }

  toggleProduct(productId) {
    this.state.expanded[productId] = !this.state.expanded[productId];
  }

  isExpanded(productId) {
    return Boolean(this.state.expanded[productId]);
  }

  async goToPage(page) {
    if (page < 1 || page > this.state.pages || page === this.state.page) {
      return;
    }
    this.state.page = page;
    await this.load();
    requestAnimationFrame(() => {
      const dashboard = this.dashboardRef.el;
      const table = this.tableRef.el;
      if (dashboard && table) {
        dashboard.scrollTo({
          top: Math.max(table.offsetTop - 12, 0),
          behavior: "smooth",
        });
      }
    });
  }

  get pageItems() {
    const pages = new Set([1, this.state.pages]);
    for (
      let page = Math.max(1, this.state.page - 2);
      page <= Math.min(this.state.pages, this.state.page + 2);
      page += 1
    ) {
      pages.add(page);
    }

    const items = [];
    let previousPage = 0;
    for (const page of [...pages].sort((left, right) => left - right)) {
      if (previousPage && page - previousPage > 1) {
        items.push({
          key: `ellipsis-${previousPage}-${page}`,
          type: "ellipsis",
        });
      }
      items.push({
        key: `page-${page}`,
        type: "page",
        page,
      });
      previousPage = page;
    }
    return items;
  }

  async openAction(key) {
    const action = this.state.actions[key];
    if (action) {
      await this.action.doAction(action);
    }
  }

  exportInventory() {
    const params = new URLSearchParams();
    if (this.state.selectedLocation) {
      params.set("location_id", this.state.selectedLocation.id);
    }
    if (this.state.programId) {
      params.set("program_id", this.state.programId);
    }
    if (this.state.search) {
      params.set("search", this.state.search);
    }
    if (this.state.status !== "all") {
      params.set("status", this.state.status);
    }
    if (this.state.sort !== "name") {
      params.set("sort", this.state.sort);
    }

    const link = document.createElement("a");
    link.href = `/lesotho_elmis_integration/inventory/export?${params.toString()}`;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  async openProduct(product) {
    await this.action.doAction({
      type: "ir.actions.act_window",
      name: product.name,
      res_model: "stock.quant",
      views: [[false, "tree"]],
      target: "current",
      domain: [
        ["location_id", "child_of", this.state.selectedLocation.id],
        ["product_id", "=", product.id],
      ],
      context: {
        search_default_internal_loc: 1,
      },
    });
  }

  async openStockCard(product, lot) {
    await this.action.doAction({
      type: "ir.actions.client",
      name: "Batch Stock Card",
      tag: "lesotho_elmis_integration.stock_card",
      target: "current",
      context: {
        product_id: product.id,
        lot_id: lot.id,
        location_id: this.state.selectedLocation.id,
      },
    });
  }

  get syncClass() {
    const outbox = this.state.sync.outbox || {};
    if (
      outbox.failed ||
      outbox.stuck_sent ||
      this.state.sync.last_run?.status === "failed"
    ) {
      return "is-error";
    }
    if (!this.state.sync.enabled) {
      return "is-off";
    }
    return "is-current";
  }

  get syncHeadline() {
    const outbox = this.state.sync.outbox || {};
    if (outbox.stuck_sent) {
      return `${outbox.stuck_sent} sent event(s) require attention`;
    }
    if (outbox.failed) {
      return `${outbox.failed} outbox event(s) failed`;
    }
    if (this.state.sync.last_run?.status === "failed") {
      return "The last eLMIS inventory sync failed";
    }
    if (!this.state.sync.enabled) {
      return "Scheduled eLMIS sync is off";
    }
    return "eLMIS stock mirror is current";
  }

  get syncMeta() {
    const outbox = this.state.sync.outbox || {};
    const lastSuccess = this.formatDateTime(
      this.state.sync.last_success?.finished_at
    );
    return `${lastSuccess} · ${outbox.pending || 0} pending · ${
      this.state.deliveredToday
    } delivered today`;
  }

  get hasBatchAlerts() {
    return [
      "expired_quantity",
      "expiring_0_30_quantity",
      "expiring_31_60_quantity",
      "expiring_61_90_quantity",
    ].some((key) => (this.state.summary[key] || 0) > 0);
  }

  stockLabel(status) {
    return {
      available: "Available",
      low: "Low stock",
      out: "Out of stock",
      reserved: "Fully reserved",
    }[status] || status;
  }

  expiryLabel(status) {
    return {
      valid: "Valid",
      expiring_soon: "Expiring soon",
      expired: "Expired",
      not_tracked: "No expiry",
    }[status] || status;
  }

  expiryWindowLabel(lot) {
    return {
      expired: "Expired",
      days_0_30: "Due in 0–30 days",
      days_31_60: "Due in 31–60 days",
      days_61_90: "Due in 61–90 days",
      later: "More than 90 days",
      not_tracked: "No expiry",
    }[lot.expiry_bucket] || this.expiryLabel(lot.expiry_status);
  }

  formatQuantity(value) {
    return new Intl.NumberFormat(undefined, {
      maximumFractionDigits: 2,
    }).format(value || 0);
  }

  formatStockQuantity(value, product) {
    const quantity = Number(value || 0);
    const packSize = Number(product.pack_size || 0);
    if (
      this.state.quantityMode === "units" ||
      packSize <= 0 ||
      quantity < 0
    ) {
      return `${this.formatQuantity(quantity)} ${this.unitLabel(quantity)}`;
    }

    const completePacks = Math.floor(quantity / packSize);
    const looseUnits = quantity - completePacks * packSize;
    const parts = [
      `${this.formatQuantity(completePacks)} ${
        completePacks === 1 ? "pack" : "packs"
      }`,
    ];
    if (looseUnits) {
      parts.push(
        `${this.formatQuantity(looseUnits)} ${this.unitLabel(looseUnits)}`
      );
    }
    return parts.join(" + ");
  }

  quantityUnitHint(product) {
    const packSize = Number(product.pack_size || 0);
    if (this.state.quantityMode === "units") {
      return product.uom || "Units";
    }
    if (packSize <= 0) {
      return "Pack size unavailable · shown in units";
    }
    return `${this.formatQuantity(packSize)} units per pack`;
  }

  canonicalQuantityTitle(value, product) {
    return `${this.formatQuantity(value)} ${this.unitLabel(value)}${
      product.uom ? ` (${product.uom})` : ""
    }`;
  }

  unitLabel(value) {
    return Number(value) === 1 ? "unit" : "units";
  }

  formatDate(value) {
    if (!value) {
      return "—";
    }
    const date = new Date(`${value}T00:00:00`);
    return date.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  formatDateTime(value) {
    if (!value) {
      return "Never synced";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "Sync time unavailable";
    }
    return `Last synced ${date.toLocaleString()}`;
  }
}

ElmisInventoryDashboard.template =
  "lesotho_elmis_integration.InventoryDashboard";

registry
  .category("actions")
  .add(
    "lesotho_elmis_integration.inventory_dashboard",
    ElmisInventoryDashboard
  );
