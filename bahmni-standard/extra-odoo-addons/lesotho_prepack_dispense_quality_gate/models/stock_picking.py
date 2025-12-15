from odoo import models, _
from .qc_gate_utils import check_qc_gate

class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        for picking in self:
            check_qc_gate(
                picking,
                require_any=True,
                error_title=_("Dispensing blocked: Quality check required (Delivery)."),
            )
        return super().button_validate()
