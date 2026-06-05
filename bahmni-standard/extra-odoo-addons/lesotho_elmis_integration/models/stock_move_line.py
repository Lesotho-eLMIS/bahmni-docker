from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare


ELMIS_TRANSFER_DEBIT_REASON_SELECTION = [
    ("Transfer Out", "Transfer Out"),
    ("Internal Transfer", "Internal Transfer"),
    ("Lost", "Lost"),
    ("Stolen", "Stolen"),
    ("Expiry", "Expiry"),
    ("Expired", "Expired"),
    ("Damaged", "Damaged"),
    ("Damage", "Damage"),
    ("Unusable", "Unusable"),
    ("Degraded", "Degraded"),
    ("Cold Chain Failure", "Cold Chain Failure"),
    ("Passed Open-vial Time Limit", "Passed Open-vial Time Limit"),
    ("Recalled", "Recalled"),
    ("Over Supply", "Over Supply"),
    ("POD Query", "POD Query"),
]

ELMIS_TRANSFER_CREDIT_REASON_SELECTION = [
    ("Transfer In", "Transfer In"),
    ("Facility Return", "Facility Return"),
    ("Receipts", "Receipts"),
    ("Beginning Balance Excess", "Beginning Balance Excess"),
]


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    available_source_lot_ids = fields.Many2many(
        "stock.lot",
        string="Available Source Lots",
        compute="_compute_source_lot_availability",
        help="Lots with stock available at the selected source location.",
    )
    selected_lot_available_qty = fields.Float(
        string="Available at Source",
        digits=(16, 4),
        compute="_compute_source_lot_availability",
        help="Quantity available for the selected lot at the selected source location.",
    )
    selected_lot_has_enough_qty = fields.Boolean(
        string="Selected Lot Has Enough Quantity",
        compute="_compute_source_lot_availability",
        help="Whether the selected source lot has enough available stock for the done quantity.",
    )
    elmis_transfer_program_id = fields.Many2one(
        "elmis.program",
        string="eLMIS Transfer Program",
        domain=[("active", "=", True)],
        index=True,
        copy=False,
        help="eLMIS program under which this internal transfer should be reported.",
    )
    elmis_transfer_debit_reason = fields.Selection(
        ELMIS_TRANSFER_DEBIT_REASON_SELECTION,
        string="eLMIS Source Debit Reason",
        default="Transfer Out",
        copy=False,
        help="Reason submitted to eLMIS for the source location debit.",
    )
    elmis_transfer_credit_reason = fields.Selection(
        ELMIS_TRANSFER_CREDIT_REASON_SELECTION,
        string="eLMIS Destination Credit Reason",
        default="Transfer In",
        copy=False,
        help="Reason submitted to eLMIS for the destination location credit.",
    )
    elmis_transfer_outbox_count = fields.Integer(
        string="eLMIS Outbox Events",
        compute="_compute_elmis_transfer_outbox_status",
        help="Number of eLMIS outbox events created from this stock move line.",
    )
    elmis_transfer_sync_status = fields.Selection(
        [
            ("not_applicable", "Not Applicable"),
            ("blocked", "Blocked"),
            ("pending", "Pending"),
            ("partial", "Partial"),
            ("complete", "Complete"),
        ],
        string="eLMIS Transfer Sync Status",
        compute="_compute_elmis_transfer_outbox_status",
        help="Operational status of eLMIS transfer outbox creation for this move line.",
    )

    @api.onchange("product_id")
    def _onchange_elmis_transfer_product_id(self):
        for line in self:
            if not line.product_id.is_elmis_product:
                line.elmis_transfer_program_id = False
                continue
            if not line.elmis_transfer_program_id and len(line.product_id.elmis_program_ids) == 1:
                line.elmis_transfer_program_id = line.product_id.elmis_program_ids[0]
            if line.lot_id and line.lot_id not in line.available_source_lot_ids:
                line.lot_id = False

    @api.onchange("location_id", "product_id")
    def _onchange_elmis_transfer_source_location_or_product(self):
        for line in self:
            if line.lot_id and line.available_source_lot_ids and line.lot_id not in line.available_source_lot_ids:
                line.lot_id = False

    @api.constrains("product_id", "lot_id")
    def _check_elmis_transfer_lot_matches_product(self):
        for line in self:
            if (
                line.product_id.is_elmis_product
                and line.lot_id
                and line.lot_id.product_id != line.product_id
            ):
                raise ValidationError(_("The selected eLMIS lot must belong to the eLMIS product."))

    @api.constrains("product_id", "elmis_transfer_program_id")
    def _check_elmis_transfer_program_matches_product(self):
        for line in self:
            if not line.product_id.is_elmis_product or not line.elmis_transfer_program_id:
                continue
            product_programs = line.product_id.elmis_program_ids
            if product_programs and line.elmis_transfer_program_id not in product_programs:
                raise ValidationError(
                    _("The selected eLMIS transfer program must be configured on the eLMIS product.")
                )

    @api.depends("product_id", "location_id", "lot_id", "qty_done", "product_uom_id")
    def _compute_source_lot_availability(self):
        Quant = self.env["stock.quant"]
        Lot = self.env["stock.lot"]
        for line in self:
            available_lots = Lot
            selected_available_qty = 0.0
            has_enough_qty = False

            if line.product_id and line.location_id and line.location_id.usage == "internal":
                available_by_lot = line._get_available_source_lot_quantities()
                available_lots = Lot.browse(available_by_lot.keys())
                if line.lot_id:
                    selected_available_qty = available_by_lot.get(line.lot_id.id, 0.0)
                    available_lots |= line.lot_id
                required_qty = line._get_source_lot_required_qty()
                has_enough_qty = float_compare(
                    selected_available_qty,
                    required_qty,
                    precision_rounding=line.product_id.uom_id.rounding or 0.0001,
                ) >= 0
            elif line.product_id:
                available_lots = Lot.search([("product_id", "=", line.product_id.id)])
                if line.lot_id and line.location_id:
                    selected_available_qty = Quant._get_available_quantity(
                        line.product_id,
                        line.location_id,
                        lot_id=line.lot_id,
                        strict=False,
                    )
                    has_enough_qty = True

            line.available_source_lot_ids = available_lots
            line.selected_lot_available_qty = selected_available_qty
            line.selected_lot_has_enough_qty = has_enough_qty

    def _compute_elmis_transfer_outbox_status(self):
        Outbox = self.env["elmis.outbox"].sudo()
        outbox_data = {}
        if self.ids:
            grouped = Outbox.read_group(
                [
                    ("source_stock_move_line_id", "in", self.ids),
                    ("stock_move_line_side", "in", ["source", "destination"]),
                ],
                ["source_stock_move_line_id"],
                ["source_stock_move_line_id"],
            )
            outbox_data = {
                row["source_stock_move_line_id"][0]: row["source_stock_move_line_id_count"]
                for row in grouped
            }

        for line in self:
            event_count = outbox_data.get(line.id, 0)
            expected_count = line._get_expected_elmis_transfer_outbox_count()
            line.elmis_transfer_outbox_count = event_count
            if expected_count == 0:
                line.elmis_transfer_sync_status = "not_applicable"
            elif line.state != "done":
                line.elmis_transfer_sync_status = "pending"
            elif event_count >= expected_count:
                line.elmis_transfer_sync_status = "complete"
            elif event_count:
                line.elmis_transfer_sync_status = "partial"
            else:
                line.elmis_transfer_sync_status = "blocked"

    def _check_elmis_internal_transfer_ready(self):
        for line in self:
            if not line._requires_elmis_internal_transfer_outbox():
                continue
            line._check_source_lot_quantity_available()
            line._check_elmis_internal_transfer_metadata()

    def _create_elmis_internal_transfer_outbox(self):
        lines = self.filtered(
            lambda line: line.state == "done" and line._requires_elmis_internal_transfer_outbox()
        )
        if not lines:
            return

        Outbox = self.env["elmis.outbox"].sudo()
        existing = Outbox.search(
            [
                ("source_stock_move_line_id", "in", lines.ids),
                ("stock_move_line_side", "in", ["source", "destination"]),
            ]
        )
        existing_keys = {
            (event.source_stock_move_line_id.id, event.stock_move_line_side)
            for event in existing
        }

        for line in lines:
            line._check_elmis_internal_transfer_metadata()
            for side, vals in line._prepare_elmis_internal_transfer_outbox_vals_by_side():
                if (line.id, side) in existing_keys:
                    continue
                Outbox.create(vals)

    def _requires_elmis_internal_transfer_outbox(self):
        self.ensure_one()
        if self.move_id.scrapped or self.move_id.scrap_ids or self._is_from_stock_scrap():
            return False
        if not self.product_id.is_elmis_product:
            return False
        if self.location_id == self.location_dest_id:
            return False
        if float_compare(
            self.qty_done,
            0,
            precision_rounding=self.product_uom_id.rounding,
        ) <= 0:
            return False
        return bool(self.location_id.elmis_facility_code or self.location_dest_id.elmis_facility_code)

    def _is_from_stock_scrap(self):
        self.ensure_one()
        if not self.move_id:
            return False
        return bool(self.env["stock.scrap"].sudo().search_count([("move_id", "=", self.move_id.id)]))

    def _get_expected_elmis_transfer_outbox_count(self):
        self.ensure_one()
        if not self.product_id.is_elmis_product:
            return 0
        if self.location_id == self.location_dest_id:
            return 0
        if float_compare(
            self.qty_done,
            0,
            precision_rounding=self.product_uom_id.rounding,
        ) <= 0:
            return 0
        expected_count = 0
        if self.location_id.elmis_facility_code:
            expected_count += 1
        if self.location_dest_id.elmis_facility_code:
            expected_count += 1
        return expected_count

    def _check_elmis_internal_transfer_metadata(self):
        self.ensure_one()
        if not self.product_id.elmis_orderable_id or not self.product_id.elmis_product_code:
            raise UserError(
                _("Configure the eLMIS orderable UUID and product code for %(product)s.")
                % {"product": self.product_id.display_name}
            )
        if not self.lot_id:
            raise UserError(
                _("Select an eLMIS lot before validating the transfer for %(product)s.")
                % {"product": self.product_id.display_name}
            )
        if self.lot_id.product_id != self.product_id:
            raise UserError(_("The selected eLMIS lot must belong to the eLMIS product."))
        if not (self.lot_id.elmis_lot_id or self.lot_id.elmis_lot_number):
            raise UserError(
                _("Configure the eLMIS lot UUID or lot number for %(lot)s.")
                % {"lot": self.lot_id.display_name}
            )

        program = self._get_elmis_transfer_program()
        if not program:
            raise UserError(
                _(
                    "Select an eLMIS transfer program before validating %(product)s. "
                    "Odoo can only infer the program when the product has exactly one eLMIS program."
                )
                % {"product": self.product_id.display_name}
            )

        if self.location_id.elmis_facility_code and not self.elmis_transfer_debit_reason:
            raise UserError(_("Select an eLMIS source debit reason before validating this transfer."))
        if self.location_dest_id.elmis_facility_code and not self.elmis_transfer_credit_reason:
            raise UserError(_("Select an eLMIS destination credit reason before validating this transfer."))

    def _get_available_source_lot_quantities(self):
        self.ensure_one()
        if not self.product_id or not self.location_id or self.location_id.usage != "internal":
            return {}

        quantities = {}
        rounding = self.product_id.uom_id.rounding or 0.0001
        quants = self.env["stock.quant"].search(
            [
                ("product_id", "=", self.product_id.id),
                ("location_id", "child_of", self.location_id.id),
                ("lot_id", "!=", False),
            ]
        )
        for quant in quants:
            available_qty = quant.quantity - quant.reserved_quantity
            if quant.lot_id == self.lot_id:
                available_qty += self._get_reserved_source_lot_qty()
            if float_compare(available_qty, 0.0, precision_rounding=rounding) <= 0:
                continue
            quantities[quant.lot_id.id] = quantities.get(quant.lot_id.id, 0.0) + available_qty
        return quantities

    def _get_reserved_source_lot_qty(self):
        self.ensure_one()
        reserved_qty = 0.0
        if "reserved_uom_qty" in self._fields and self.reserved_uom_qty:
            reserved_qty = self.product_uom_id._compute_quantity(
                self.reserved_uom_qty,
                self.product_id.uom_id,
            )
        return reserved_qty

    def _get_source_lot_required_qty(self):
        self.ensure_one()
        if not self.product_id:
            return 0.0
        return self.product_uom_id._compute_quantity(
            self.qty_done or 0.0,
            self.product_id.uom_id,
        )

    def _check_source_lot_quantity_available(self):
        for line in self:
            if (
                not line.product_id
                or not line.lot_id
                or not line.location_id
                or line.location_id.usage != "internal"
            ):
                continue
            required_qty = line._get_source_lot_required_qty()
            if float_compare(
                required_qty,
                0.0,
                precision_rounding=line.product_id.uom_id.rounding or 0.0001,
            ) <= 0:
                continue
            available_qty = line._get_available_source_lot_quantities().get(line.lot_id.id, 0.0)
            if float_compare(
                available_qty,
                required_qty,
                precision_rounding=line.product_id.uom_id.rounding or 0.0001,
            ) < 0:
                raise UserError(
                    _(
                        "Insufficient stock for %(product)s batch %(lot)s at %(location)s. "
                        "Requested: %(requested).2f %(uom)s. Available: %(available).2f %(uom)s."
                    )
                    % {
                        "product": line.product_id.display_name,
                        "lot": line.lot_id.name,
                        "location": line.location_id.display_name,
                        "requested": required_qty,
                        "available": available_qty,
                        "uom": line.product_id.uom_id.name,
                    }
                )

    def _get_elmis_transfer_program(self):
        self.ensure_one()
        if self.elmis_transfer_program_id:
            return self.elmis_transfer_program_id
        if len(self.product_id.elmis_program_ids) == 1:
            return self.product_id.elmis_program_ids[0]
        return self.env["elmis.program"]

    def _prepare_elmis_internal_transfer_outbox_vals_by_side(self):
        self.ensure_one()
        program = self._get_elmis_transfer_program()
        reference = self._get_elmis_transfer_reference()
        common_vals = {
            "transaction_type": "ADJUSTMENT",
            "program_id": program.id,
            "elmis_orderable_id": self.product_id.id,
            "lot_id": self.lot_id.id,
            "quantity": self.qty_done,
            "uom_id": self.product_uom_id.id,
            "transaction_date": self.date or fields.Datetime.now(),
            "prescription_ref": reference,
            "source_stock_move_line_id": self.id,
        }

        values = []
        if self.location_id.elmis_facility_code:
            vals = dict(
                common_vals,
                facility_code=self.location_id.elmis_facility_code,
                adjustment_reason=self.elmis_transfer_debit_reason,
                stock_move_line_side="source",
            )
            values.append(("source", vals))
        if self.location_dest_id.elmis_facility_code:
            vals = dict(
                common_vals,
                facility_code=self.location_dest_id.elmis_facility_code,
                adjustment_reason=self.elmis_transfer_credit_reason,
                stock_move_line_side="destination",
            )
            values.append(("destination", vals))
        return values

    def _get_elmis_transfer_reference(self):
        self.ensure_one()
        picking = self.picking_id
        reference = (
            picking.name
            or self.move_id.reference
            or self.reference
            or self.move_id.name
            or _("Stock Move Line %(id)s") % {"id": self.id}
        )
        return "%s / line %s" % (reference, self.id)
