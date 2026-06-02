from odoo import fields, models


class StockLot(models.Model):
    _inherit = "stock.lot"

    elmis_lot_id = fields.Char(
        string="eLMIS Lot UUID",
        index=True,
        copy=False,
        help="OpenLMIS lot UUID. This is the canonical lot mapping key when available.",
    )
    elmis_lot_number = fields.Char(
        string="eLMIS Lot Number",
        index=True,
        copy=False,
        help="OpenLMIS lot code or lot number.",
    )

    _sql_constraints = [
        (
            "elmis_lot_id_unique",
            "UNIQUE(elmis_lot_id)",
            "An eLMIS lot UUID must map to exactly one Odoo lot.",
        ),
    ]
