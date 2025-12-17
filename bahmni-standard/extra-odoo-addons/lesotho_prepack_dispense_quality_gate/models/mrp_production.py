from odoo import models, _
from .qc_gate_utils import check_qc_gate

class MrpProduction(models.Model):
    _inherit = "mrp.production"

    def button_mark_done(self):
        for mo in self:
            check_qc_gate(
                mo,
                require_any=True,
                error_title=_("Prepacking blocked: Quality check required (MRP)."),
            )
        return super().button_mark_done()
