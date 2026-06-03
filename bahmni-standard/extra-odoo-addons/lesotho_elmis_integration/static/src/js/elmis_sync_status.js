/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class ElmisSyncStatus extends Component {
  setup() {
    this.rpc = useService("rpc");
    this.action = useService("action");
    this.notification = useService("notification");
    this.state = useState({
      open: false,
      enabled: false,
      nextcallSeconds: false,
      lastRun: false,
      lastSuccess: false,
      outbox: false,
      intervalLabel: "",
      loading: true,
    });

    onWillStart(() => this.refresh());
    onMounted(() => {
      this.refreshTimer = setInterval(() => this.refresh(), 60000);
      this.countdownTimer = setInterval(() => this.tick(), 1000);
    });
    onWillUnmount(() => {
      clearInterval(this.refreshTimer);
      clearInterval(this.countdownTimer);
    });
  }

  async refresh() {
    try {
      const status = await this.rpc("/lesotho_elmis_integration/sync_status", {});
      this.state.enabled = status.enabled;
      this.state.nextcallSeconds = status.nextcall_seconds;
      this.state.lastRun = status.last_run;
      this.state.lastSuccess = status.last_success;
      this.state.outbox = status.outbox;
      this.state.intervalLabel = this.formatInterval(
        status.interval_number,
        status.interval_type
      );
      this.state.loading = false;
    } catch (error) {
      this.state.loading = false;
      this.notification.add("Could not load eLMIS sync status.", { type: "warning" });
    }
  }

  tick() {
    if (typeof this.state.nextcallSeconds === "number") {
      this.state.nextcallSeconds -= 1;
    }
  }

  toggle() {
    this.state.open = !this.state.open;
  }

  async openHistory() {
    this.state.open = false;
    await this.action.doAction("lesotho_elmis_integration.action_elmis_inventory_sync_run");
  }

  async openTrigger() {
    this.state.open = false;
    await this.action.doAction("lesotho_elmis_integration.action_elmis_inventory_sync_wizard");
  }

  async openOutbox() {
    this.state.open = false;
    await this.action.doAction("lesotho_elmis_integration.action_elmis_outbox");
  }

  get statusClass() {
    if (this.state.outbox && this.state.outbox.stuck_sent) {
      return "o_elmis_sync_status_failed";
    }
    if (this.state.outbox && this.state.outbox.failed) {
      return "o_elmis_sync_status_failed";
    }
    if (!this.state.enabled) {
      return "o_elmis_sync_status_off";
    }
    if (this.state.lastRun && this.state.lastRun.status === "failed") {
      return "o_elmis_sync_status_failed";
    }
    if (
      typeof this.state.nextcallSeconds === "number" &&
      this.state.nextcallSeconds < 0
    ) {
      return "o_elmis_sync_status_due";
    }
    return "o_elmis_sync_status_ok";
  }

  get summaryLabel() {
    if (this.state.loading) {
      return "Loading";
    }
    if (this.state.outbox && this.state.outbox.stuck_sent) {
      return `${this.state.outbox.stuck_sent} stuck`;
    }
    if (this.state.outbox && this.state.outbox.failed) {
      return `${this.state.outbox.failed} failed`;
    }
    if (this.state.outbox && this.state.outbox.pending) {
      return `${this.state.outbox.pending} pending`;
    }
    if (!this.state.enabled) {
      return "Off";
    }
    if (
      typeof this.state.nextcallSeconds === "number" &&
      this.state.nextcallSeconds < 0
    ) {
      return "Due";
    }
    return this.formatDuration(this.state.nextcallSeconds);
  }

  get lastSuccessLabel() {
    if (!this.state.lastSuccess || !this.state.lastSuccess.finished_at) {
      return "Never";
    }
    return this.formatDateTime(this.state.lastSuccess.finished_at);
  }

  get lastRunLabel() {
    if (!this.state.lastRun || !this.state.lastRun.finished_at) {
      return "No completed run";
    }
    return this.formatDateTime(this.state.lastRun.finished_at);
  }

  get oldestRetryableLabel() {
    if (!this.state.outbox || !this.state.outbox.oldest_retryable_age_seconds) {
      return "None";
    }
    return this.formatDuration(this.state.outbox.oldest_retryable_age_seconds);
  }

  get oldestStuckSentLabel() {
    if (!this.state.outbox || !this.state.outbox.oldest_stuck_sent_age_seconds) {
      return "None";
    }
    return this.formatDuration(this.state.outbox.oldest_stuck_sent_age_seconds);
  }

  get lastDeliveredLabel() {
    if (!this.state.outbox || !this.state.outbox.last_delivered_at) {
      return "Never";
    }
    return this.formatDateTime(this.state.outbox.last_delivered_at);
  }

  formatInterval(number, type) {
    if (!number || !type) {
      return "Not scheduled";
    }
    return `Every ${number} ${type}`;
  }

  formatDateTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "Unknown";
    }
    return date.toLocaleString();
  }

  formatDuration(seconds) {
    if (typeof seconds !== "number") {
      return "--";
    }
    const remaining = Math.max(seconds, 0);
    const days = Math.floor(remaining / 86400);
    const hours = Math.floor((remaining % 86400) / 3600);
    const minutes = Math.floor((remaining % 3600) / 60);
    const secondsPart = Math.floor(remaining % 60);
    if (days) {
      return `${days}d ${hours}h`;
    }
    if (hours) {
      return `${hours}h ${minutes}m`;
    }
    if (remaining < 300) {
      if (minutes) {
        return `${minutes}m ${secondsPart}s`;
      }
      return `${secondsPart}s`;
    }
    return `${minutes}m`;
  }
}

ElmisSyncStatus.template = "lesotho_elmis_integration.SyncStatus";

registry.category("systray").add("lesotho_elmis_integration.SyncStatus", {
  Component: ElmisSyncStatus,
  sequence: 5,
});
