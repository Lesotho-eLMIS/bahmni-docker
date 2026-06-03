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
            and item._requires_elmis_dispense_outbox()
            and item.id not in existing_line_ids
        ):
            Outbox.create(line._prepare_elmis_dispense_outbox_vals())

    def _requires_elmis_dispense_outbox(self):
        self.ensure_one()
        return bool(
            self.elmis_product_id
            or (
                self.product_id
                and (
                    self.product_id.is_dispensing_pack
                    or self.product_id.is_elmis_product
                )
            )
        )

    def _is_prepack_dispense(self):
        self.ensure_one()
        return bool(self.product_id and self.product_id.is_dispensing_pack)

    def _get_source_bulk_product(self):
        self.ensure_one()
        if not self.product_id:
            return self.env["product.product"]
        return self.product_id.bulk_product_id or self.product_id.dispensing_base_product_id

    def _get_selected_dispensing_lot(self):
        self.ensure_one()
        for field_name in ("lot_id", "existing_lot_id", "batch_id", "prodlot_id"):
            if field_name not in self._fields:
                continue
            field = self._fields[field_name]
            if getattr(field, "comodel_name", False) == "stock.lot" and self[field_name]:
                return self[field_name]
            if field.type == "char" and self[field_name] and self.product_id:
                return self._find_dispensing_lot(self.product_id, self[field_name])
        if self.product_id and "dispensing_batch_number" in self._fields:
            return self._find_dispensing_lot(self.product_id, self.dispensing_batch_number)
        return self.env["stock.lot"]

    def _get_elmis_dispense_product(self):
        self.ensure_one()
        if self._is_prepack_dispense():
            return self.product_id._get_elmis_accountability_product() or self.elmis_product_id
        if self.elmis_product_id:
            return self.elmis_product_id
        if self.product_id and self.product_id.is_elmis_product:
            return self.product_id
        return self.elmis_product_id

    def _get_elmis_dispense_lot(self):
        self.ensure_one()
        if self._is_prepack_dispense():
            selected_lot = self._get_selected_dispensing_lot()
            if selected_lot:
                accountability_lot = selected_lot._get_elmis_accountability_lot()
                if accountability_lot and accountability_lot != selected_lot:
                    return accountability_lot
                if self.elmis_lot_id:
                    return self.elmis_lot_id
                return self.env["stock.lot"]
        if self.elmis_lot_id:
            return self.elmis_lot_id
        selected_lot = self._get_selected_dispensing_lot()
        if selected_lot and self._get_elmis_dispense_product() == selected_lot.product_id:
            return selected_lot
        return self.elmis_lot_id

    def _get_elmis_dispense_quantity(self):
        self.ensure_one()
        if self._is_prepack_dispense():
            if self.product_id.pack_unit_qty <= 0:
                raise UserError(_("The selected prepack is missing its pack unit quantity."))
            return self.product_uom_qty * self.product_id.pack_unit_qty
        return self.product_uom_qty

    def _get_elmis_dispense_uom(self, product):
        self.ensure_one()
        if self._is_prepack_dispense() and product:
            return product.uom_id
        return self.product_uom

    def _prepare_elmis_dispense_outbox_vals(self):
        self.ensure_one()
        resolved_product = self._get_elmis_dispense_product()
        if self._is_prepack_dispense() and not resolved_product:
            raise UserError(
                _("The selected prepack is not linked to a parent eLMIS product.")
            )
        if not resolved_product:
            raise UserError(_("Select an eLMIS product before marking this line dispensed."))

        selected_lot = self._get_selected_dispensing_lot()
        resolved_lot = self._get_elmis_dispense_lot()
        if self._is_prepack_dispense() and not selected_lot:
            raise UserError(_("Select a prepack batch before marking this line dispensed."))
        if self._is_prepack_dispense() and not resolved_lot:
            raise UserError(
                _(
                    "The selected prepack batch is not linked to a traced source bulk lot."
                )
            )

        program = self._get_elmis_dispense_program(product=resolved_product)
        location = self.env["elmis.inventory.sync"]._get_configured_mirror_location()
        quantity = self._get_elmis_dispense_quantity()
        if quantity <= 0:
            raise UserError(_("eLMIS dispenses must have a positive quantity."))
        uom = self._get_elmis_dispense_uom(resolved_product)

        return {
            "transaction_type": "DISPENSE",
            "facility_code": location.elmis_facility_code,
            "program_id": program.id,
            "elmis_orderable_id": resolved_product.id,
            "lot_id": resolved_lot.id,
            "quantity": quantity,
            "uom_id": uom.id,
            "transaction_date": fields.Datetime.now(),
            "prescription_ref": self._get_elmis_prescription_ref(),
            "adjustment_reason": "Consumed",
            "source_sale_order_line_id": self.id,
            "consumption_mode": "prepack" if self._is_prepack_dispense() else "direct",
            "dispensed_product_id": self.product_id.id,
            "dispensed_lot_id": selected_lot.id,
            "dispensed_quantity": self.product_uom_qty,
            "dispensed_uom_id": self.product_uom.id,
            "source_bulk_product_id": self._get_source_bulk_product().id,
            "source_bulk_lot_id": resolved_lot.id if self._is_prepack_dispense() else False,
            "prepack_conversion_factor": self.product_id.pack_unit_qty
            if self._is_prepack_dispense()
            else 1.0,
        }

    def _get_elmis_dispense_program(self, product=None):
        self.ensure_one()
        product = product or self._get_elmis_dispense_product()
        if self.elmis_program_id:
            if (
                product
                and product.elmis_program_ids
                and self.elmis_program_id not in product.elmis_program_ids
            ):
                raise UserError(
                    _(
                        "Select an eLMIS program configured on the accountable eLMIS product before marking this line dispensed."
                    )
                )
            return self.elmis_program_id
        product_programs = product.elmis_program_ids
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
