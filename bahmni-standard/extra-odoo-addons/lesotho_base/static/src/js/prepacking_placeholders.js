/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";

class PrepackingPlaceholder extends Component {}

PrepackingPlaceholder.template = "lesotho_base.PrepackingPlaceholder";

registry.category("actions").add("lesotho_base.create_prepacks_placeholder", PrepackingPlaceholder);
registry.category("actions").add("lesotho_base.authorise_prepacks_placeholder", PrepackingPlaceholder);
registry.category("actions").add("lesotho_base.view_prepacks_placeholder", PrepackingPlaceholder);
