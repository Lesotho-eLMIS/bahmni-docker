from odoo import _, models
from odoo.tools.safe_eval import safe_eval


class StockLocation(models.Model):
    _inherit = "stock.location"

    def action_view_quants(self):
        action = super().action_view_quants()
        self.ensure_one()
        if not self._is_prepack_unserviceable_location():
            return action

        view = self.env.ref(
            "lesotho_prepack_batch.view_stock_quant_tree_prepack_damaged",
            raise_if_not_found=False,
        )
        if view:
            action["views"] = [(view.id, "tree")]
            action["view_id"] = view.id
            action["view_mode"] = "tree"
        action["name"] = _("Damaged Products")
        action["display_name"] = _("Damaged Products")
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        context["group_by"] = []
        action["context"] = context
        return action

    def _is_prepack_unserviceable_location(self):
        self.ensure_one()
        return self.usage == "internal" and "unserviceable" in (self.name or "").lower()
