from odoo import fields, models


class StockQuant(models.Model):
    _inherit = "stock.quant"

    prepack_damaged_quantity = fields.Float(
        string="Damaged Quantity",
        compute="_compute_prepack_damaged_quantities",
        digits="Product Unit of Measure",
    )
    prepack_damaged_on_hand_quantity = fields.Float(
        string="Quantity On Hand",
        compute="_compute_prepack_damaged_quantities",
        digits="Product Unit of Measure",
    )
    prepack_damage_source_location_id = fields.Many2one(
        "stock.location",
        string="Location Where Damage Occurred",
        compute="_compute_prepack_damaged_quantities",
    )
    prepack_damage_removal_date = fields.Datetime(
        string="Removal Date",
        compute="_compute_prepack_damaged_quantities",
    )
    prepack_damage_recorded_by_id = fields.Many2one(
        "res.users",
        string="Recorded By",
        compute="_compute_prepack_damaged_quantities",
    )

    def _compute_prepack_damaged_quantities(self):
        for quant in self:
            damage_details = quant._get_prepack_damage_details()
            quant.prepack_damaged_quantity = damage_details["quantity"]
            quant.prepack_damaged_on_hand_quantity = damage_details["quantity"]
            quant.prepack_damage_source_location_id = damage_details["source_location_id"]
            quant.prepack_damage_removal_date = damage_details["removal_date"]
            quant.prepack_damage_recorded_by_id = damage_details["recorded_by_id"]

    def _get_prepack_damage_details(self):
        self.ensure_one()
        empty_details = {
            "quantity": 0.0,
            "source_location_id": False,
            "removal_date": False,
            "recorded_by_id": False,
        }
        if not self.product_id or not self.location_id:
            return empty_details

        batches = self.env["bahmni.prepack.batch"].sudo().search(
            [
                "|",
                ("damage_move_ids", "!=", False),
                ("damage_scrap_ids", "!=", False),
            ]
        )
        if not batches:
            return empty_details

        damage_records = self._get_prepack_damage_move_details(batches)
        damage_records += self._get_prepack_damage_scrap_details(batches)
        if not damage_records:
            return empty_details

        latest_record = max(
            damage_records,
            key=lambda item: item["removal_date"] or fields.Datetime.to_datetime("1970-01-01 00:00:00"),
        )
        return {
            "quantity": sum(item["quantity"] for item in damage_records),
            "source_location_id": latest_record["source_location_id"],
            "removal_date": latest_record["removal_date"],
            "recorded_by_id": latest_record["recorded_by_id"],
        }

    def _get_prepack_damage_move_details(self, batches):
        move_ids = batches.mapped("damage_move_ids").ids
        if not move_ids:
            return []

        domain = [
            ("move_id", "in", move_ids),
            ("product_id", "=", self.product_id.id),
            ("location_dest_id", "=", self.location_id.id),
            ("qty_done", ">", 0),
        ]
        if self.lot_id:
            domain.append(("lot_id", "=", self.lot_id.id))
        else:
            domain.append(("lot_id", "=", False))
        move_lines = self.env["stock.move.line"].sudo().search(domain)
        details = []
        for move_line in move_lines:
            batch = batches.filtered(lambda item: move_line.move_id in item.damage_move_ids)[:1]
            details.append(
                {
                    "quantity": move_line.qty_done,
                    "source_location_id": move_line.location_id.id,
                    "removal_date": move_line.move_id.date,
                    "recorded_by_id": batch.responsible_id.id if batch else False,
                }
            )
        return details

    def _get_prepack_damage_scrap_details(self, batches):
        scraps = batches.mapped("damage_scrap_ids").filtered(
            lambda scrap: scrap.state == "done"
            and scrap.product_id == self.product_id
            and scrap.scrap_location_id == self.location_id
            and scrap.lot_id == self.lot_id
        )
        details = []
        for scrap in scraps:
            batch = batches.filtered(lambda item: scrap in item.damage_scrap_ids)[:1]
            details.append(
                {
                    "quantity": scrap.scrap_qty,
                    "source_location_id": scrap.location_id.id,
                    "removal_date": scrap.date_done or scrap.create_date,
                    "recorded_by_id": batch.responsible_id.id if batch else False,
                }
            )
        return details
