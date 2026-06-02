from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    elmis_product_id = fields.Many2one(
        "product.product",
        string="eLMIS Product",
        domain=[("is_elmis_product", "=", True)],
        index=True,
        copy=False,
        help="Actual eLMIS orderable selected for dispensing this prescription line.",
    )
    elmis_lot_id = fields.Many2one(
        "stock.lot",
        string="eLMIS Lot",
        domain="[('product_id', '=', elmis_product_id)]",
        index=True,
        copy=False,
        help="eLMIS lot selected for dispensing this prescription line.",
    )
    elmis_program_id = fields.Many2one(
        "elmis.program",
        string="eLMIS Program",
        domain=[("active", "=", True)],
        index=True,
        copy=False,
        help="eLMIS program under which the selected stock should be reported.",
    )

    def write(self, vals):
        was_undispensed = self.filtered(lambda line: not line.dispensed)
        result = super().write(vals)
        if vals.get("dispensed") is True:
            self._create_elmis_dispense_outbox_for_lines(was_undispensed)
        return result

    def _create_elmis_dispense_outbox_for_lines(self, candidate_lines):
        if not candidate_lines:
            return
        Outbox = self.env["elmis.outbox"].sudo()
        existing_line_ids = set(
            Outbox.search(
                [
                    ("source_sale_order_line_id", "in", candidate_lines.ids),
                    ("transaction_type", "=", "DISPENSE"),
                ]
            ).mapped("source_sale_order_line_id").ids
        )

        for line in candidate_lines.filtered(
            lambda item: item.dispensed
            and item.elmis_product_id
            and item.id not in existing_line_ids
        ):
            Outbox.create(line._prepare_elmis_dispense_outbox_vals())

    def _prepare_elmis_dispense_outbox_vals(self):
        self.ensure_one()
        program = self._get_elmis_dispense_program()
        location = self.env["elmis.inventory.sync"]._get_configured_mirror_location()
        quantity = self.product_uom_qty
        if quantity <= 0:
            raise UserError(_("eLMIS dispenses must have a positive quantity."))

        return {
            "transaction_type": "DISPENSE",
            "facility_code": location.elmis_facility_code,
            "program_id": program.id,
            "elmis_orderable_id": self.elmis_product_id.id,
            "lot_id": self.elmis_lot_id.id,
            "quantity": quantity,
            "uom_id": self.product_uom.id,
            "transaction_date": fields.Datetime.now(),
            "prescription_ref": self._get_elmis_prescription_ref(),
            "adjustment_reason": "Consumed",
            "source_sale_order_line_id": self.id,
        }

    def _get_elmis_dispense_program(self):
        self.ensure_one()
        if self.elmis_program_id:
            return self.elmis_program_id
        product_programs = self.elmis_product_id.elmis_program_ids
        if len(product_programs) == 1:
            return product_programs
        raise UserError(_("Select an eLMIS program before marking this line dispensed."))

    def _get_elmis_prescription_ref(self):
        self.ensure_one()
        return (
            self.external_order_uuid
            or self.order_number
            or self.order_id.name
            or "SOL-%s" % self.id
        )

    @api.onchange("elmis_product_id")
    def _onchange_elmis_product_id(self):
        for line in self:
            if not line.elmis_product_id:
                line.elmis_lot_id = False
                line.elmis_program_id = False
                continue
            if line.elmis_lot_id and line.elmis_lot_id.product_id != line.elmis_product_id:
                line.elmis_lot_id = False
            if (
                line.elmis_program_id
                and line.elmis_product_id.elmis_program_ids
                and line.elmis_program_id not in line.elmis_product_id.elmis_program_ids
            ):
                line.elmis_program_id = False
            if not line.elmis_program_id and len(line.elmis_product_id.elmis_program_ids) == 1:
                line.elmis_program_id = line.elmis_product_id.elmis_program_ids[0]

    @api.onchange("elmis_lot_id")
    def _onchange_elmis_lot_id(self):
        for line in self:
            if line.elmis_lot_id and not line.elmis_product_id:
                line.elmis_product_id = line.elmis_lot_id.product_id

    @api.constrains("elmis_product_id")
    def _check_elmis_product_id(self):
        for line in self:
            if line.elmis_product_id and not line.elmis_product_id.is_elmis_product:
                raise ValidationError(_("The selected eLMIS product must be an eLMIS product."))

    @api.constrains("elmis_product_id", "elmis_lot_id")
    def _check_elmis_lot_matches_product(self):
        for line in self:
            if line.elmis_lot_id and not line.elmis_product_id:
                raise ValidationError(_("Select an eLMIS product before selecting an eLMIS lot."))
            if (
                line.elmis_product_id
                and line.elmis_lot_id
                and line.elmis_lot_id.product_id != line.elmis_product_id
            ):
                raise ValidationError(_("The selected eLMIS lot must belong to the eLMIS product."))

    @api.constrains("elmis_product_id", "elmis_program_id")
    def _check_elmis_program_matches_product(self):
        for line in self:
            if not line.elmis_product_id or not line.elmis_program_id:
                continue
            product_programs = line.elmis_product_id.elmis_program_ids
            if product_programs and line.elmis_program_id not in product_programs:
                raise ValidationError(
                    _("The selected eLMIS program must be configured on the eLMIS product.")
                )
