from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


ELMIS_ADJUSTMENT_REASON_SELECTION = [
    ("Damaged", "Damaged"),
    ("Expiry", "Expiry"),
    ("Stolen", "Stolen"),
    ("Lost", "Lost"),
    ("Cold Chain Failure", "Cold Chain Failure"),
    ("Passed Open-vial Time Limit", "Passed Open-vial Time Limit"),
    ("Recalled", "Recalled"),
    ("Unusable", "Unusable"),
    ("Degraded", "Degraded"),
]


class StockScrap(models.Model):
    _inherit = "stock.scrap"

    elmis_program_id = fields.Many2one(
        "elmis.program",
        string="eLMIS Program",
        domain=[("active", "=", True)],
        index=True,
        copy=False,
        help="eLMIS program under which this adjustment should be reported.",
    )
    elmis_adjustment_reason = fields.Selection(
        ELMIS_ADJUSTMENT_REASON_SELECTION,
        string="eLMIS Adjustment Reason",
        copy=False,
        help="Reason to submit to eLMIS when this eLMIS product is moved to unserviceable.",
    )

    @api.onchange("product_id")
    def _onchange_product_id(self):
        result = super()._onchange_product_id()
        for scrap in self:
            if not scrap.product_id.is_elmis_product:
                scrap.elmis_program_id = False
                scrap.elmis_adjustment_reason = False
                continue
            if not scrap.elmis_program_id and len(scrap.product_id.elmis_program_ids) == 1:
                scrap.elmis_program_id = scrap.product_id.elmis_program_ids[0]
        return result

    @api.constrains("product_id", "lot_id")
    def _check_elmis_lot_matches_product(self):
        for scrap in self:
            if (
                scrap.product_id.is_elmis_product
                and scrap.lot_id
                and scrap.lot_id.product_id != scrap.product_id
            ):
                raise ValidationError(_("The selected eLMIS lot must belong to the eLMIS product."))

    @api.constrains("product_id", "elmis_program_id")
    def _check_elmis_program_matches_product(self):
        for scrap in self:
            if not scrap.product_id.is_elmis_product or not scrap.elmis_program_id:
                continue
            product_programs = scrap.product_id.elmis_program_ids
            if product_programs and scrap.elmis_program_id not in product_programs:
                raise ValidationError(
                    _("The selected eLMIS program must be configured on the eLMIS product.")
                )

    def action_validate(self):
        for scrap in self.filtered(lambda item: item.product_id.is_elmis_product):
            scrap._check_elmis_adjustment_ready()
        result = super().action_validate()
        self._create_elmis_scrap_outbox_for_done_records()
        return result

    def _check_elmis_adjustment_ready(self):
        self.ensure_one()
        if not self.elmis_program_id:
            raise UserError(_("Select an eLMIS program before moving this product to unserviceable."))
        if not self.elmis_adjustment_reason:
            raise UserError(_("Select an eLMIS adjustment reason before moving this product to unserviceable."))
        if self.scrap_qty <= 0:
            raise UserError(_("eLMIS adjustments must have a positive quantity."))

    def _create_elmis_scrap_outbox_for_done_records(self):
        done_scraps = self.filtered(lambda item: item.state == "done" and item.product_id.is_elmis_product)
        if not done_scraps:
            return

        Outbox = self.env["elmis.outbox"].sudo()
        existing_scrap_ids = set(
            Outbox.search(
                [
                    ("source_stock_scrap_id", "in", done_scraps.ids),
                    ("transaction_type", "=", "ADJUSTMENT"),
                ]
            ).mapped("source_stock_scrap_id").ids
        )

        for scrap in done_scraps.filtered(lambda item: item.id not in existing_scrap_ids):
            Outbox.create(scrap._prepare_elmis_scrap_outbox_vals())

    def _prepare_elmis_scrap_outbox_vals(self):
        self.ensure_one()
        self._check_elmis_adjustment_ready()
        return {
            "transaction_type": "ADJUSTMENT",
            "facility_code": self._get_elmis_scrap_facility_code(),
            "program_id": self.elmis_program_id.id,
            "elmis_orderable_id": self.product_id.id,
            "lot_id": self.lot_id.id,
            "quantity": self.scrap_qty,
            "uom_id": self.product_uom_id.id,
            "transaction_date": self.date_done or fields.Datetime.now(),
            "prescription_ref": self.name,
            "adjustment_reason": self.elmis_adjustment_reason,
            "source_stock_scrap_id": self.id,
        }

    def _get_elmis_scrap_facility_code(self):
        self.ensure_one()
        if self.location_id.elmis_facility_code:
            return self.location_id.elmis_facility_code
        return self.env["elmis.inventory.sync"]._get_configured_mirror_location().elmis_facility_code
