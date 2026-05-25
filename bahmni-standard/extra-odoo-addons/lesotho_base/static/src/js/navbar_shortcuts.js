/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";

patch(NavBar.prototype, "lesotho_base.NavBarShortcuts", {
  async openLesothoShortcut(actionXmlId) {
    try {
      await this.actionService.doAction(actionXmlId, { clearBreadcrumbs: true });
    } catch (error) {
      const message =
        (error && error.data && error.data.message) ||
        (error && error.message) ||
        "The selected shortcut could not be opened.";

      if (this.env.services.notification) {
        this.env.services.notification.add(message, { type: "danger" });
      }
      console.error(error);
    }
  },
});
