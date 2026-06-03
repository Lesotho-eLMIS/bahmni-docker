from odoo import fields, models


class StockLot(models.Model):
    _inherit = "stock.lot"

    prepack_source_lot_id = fields.Many2one(
        "stock.lot",
        string="Prepack Source Lot",
        index=True,
        copy=False,
        help="Bulk/source lot from which this local prepack lot was produced.",
    )
    prepack_source_product_id = fields.Many2one(
        "product.product",
        string="Source Bulk Product",
        related="prepack_source_lot_id.product_id",
        readonly=True,
        store=True,
    )
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

    def _get_prepack_source_lot(self):
        self.ensure_one()
        if self.prepack_source_lot_id:
            return self.prepack_source_lot_id

        production_model = self.env.registry.get("mrp.production")
        if production_model and "prepack_finished_lot_id" in self.env["mrp.production"]._fields:
            production = self.env["mrp.production"].search(
                [("prepack_finished_lot_id", "=", self.id)],
                order="id desc",
                limit=1,
            )
            if production.prepack_source_lot_id:
                return production.prepack_source_lot_id

        batch_line_model = self.env.registry.get("bahmni.prepack.batch.line")
        if batch_line_model:
            batch_line = self.env["bahmni.prepack.batch.line"].search(
                [("finished_lot_id", "=", self.id)],
                order="id desc",
                limit=1,
            )
            if batch_line.bulk_lot_id:
                return batch_line.bulk_lot_id

        return self.env["stock.lot"]

    def _get_elmis_accountability_lot(self):
        self.ensure_one()
        lot = self
        seen_ids = set()
        while lot and lot.id not in seen_ids:
            if lot.elmis_lot_id:
                return lot
            seen_ids.add(lot.id)
            lot = lot._get_prepack_source_lot()
        return self.env["stock.lot"]
