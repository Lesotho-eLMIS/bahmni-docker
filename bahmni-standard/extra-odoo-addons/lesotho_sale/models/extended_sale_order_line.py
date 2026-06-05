import json
import logging

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_is_zero

_logger = logging.getLogger(__name__)


class ExtendedSaleOrderLine(models.Model):
    """
    Extends sale.order.line with prescription details from Bahmni
    """

    _name = "sale.order.line"
    _inherit = "sale.order.line"

    # ============ PRESCRIPTION DOSING FIELDS ============
    frequency = fields.Char(
        string="Frequency",
        help="How often to take (e.g., Thrice a day, Once daily)",
        index=True,
    )

    route = fields.Char(
        string="Route",
        help="Administration route (e.g., Intravenous, Oral, Topical)",
        index=True,
    )

    dose = fields.Float(
        string="Dose", help="Amount per dose (e.g., 3.0, 500.0)", digits=(12, 4)
    )

    dose_units = fields.Char(
        string="Dose Units", help="Units for dose (e.g., mg, ml, tablets)"
    )

    # ============ DURATION FIELDS ============
    duration = fields.Integer(
        string="Duration", help="Duration of treatment (numeric value)"
    )

    duration_units = fields.Char(
        string="Duration Units", help="Units for duration (e.g., Days, Weeks, Months)"
    )

    # ============ NEW FIELDS FROM JAVA SERVICE ============
    num_refills = fields.Integer(
        string="Number of Refills",
        help="Number of times prescription can be refilled",
        default=0,
    )

    # ============ SPECIAL INSTRUCTIONS ============
    as_needed = fields.Boolean(
        string="PRN", help="Take as needed (Pro Re Nata)", default=False
    )

    administration_instructions = fields.Text(
        string="Administration Instructions",
        help="Special instructions for administration",
    )

    # ============ DRUG DETAILS ============
    drug_form = fields.Char(
        string="Drug Form",
        help="Physical form of drug (e.g., Injection, Tablet, Capsule)",
    )

    drug_uuid = fields.Char(string="Drug UUID", help="Bahmni Drug UUID for reference")

    # ============ ORDER REFERENCES ============
    external_order_uuid = fields.Char(
        string="External Order UUID",
        help="Bahmni Order UUID for this line item",
        index=True,
    )

    previous_order_uuid = fields.Char(
        string="Previous Order UUID",
        help="UUID of previous order if this is a revision",
        index=True,
    )

    order_number = fields.Char(
        string="Order Number", help="Bahmni Order Number (e.g., ORD-11)", index=True
    )

    # ============ ADDITIONAL FIELDS ============
    concept_name = fields.Char(string="Concept Name", help="Concept name from Bahmni")

    dispensed = fields.Boolean(
        string="Dispensed",
        help="Whether this medication has been dispensed",
        default=False,
    )

    barcode_scan = fields.Char(
        string="Barcode",
        copy=False,
        help="Scan a product barcode to populate the dispensed product, quantity and batch.",
    )

    dispensing_batch_number = fields.Char(
        string="Batch Number",
        copy=False,
        help="Batch/lot selected for this dispensed product.",
    )

    served_internally = fields.Boolean(
        string="Served Internally",
        default=True,
        copy=False,
    )

    additional_instructions = fields.Text(
        string="Additional Instructions",
        help="Dispensing notes to print with this medication.",
    )

    dispensing_comments = fields.Text(
        string="Dispensing Comments",
        copy=False,
        help="Comments added by the dispenser during prescription dispensing.",
    )
    balance_resolution = fields.Selection(
        selection=[
            ("external_referral", "External Referral"),
            ("other", "Other"),
        ],
        string="Balance Resolution",
        copy=False,
        help="Reason captured when an undispensed balance is closed without a back order.",
    )
    balance_resolution_note = fields.Text(
        string="Balance Resolution Explanation",
        copy=False,
        help="Free-text explanation for balance resolutions that need additional context.",
    )
    prescription_backorder_origin_line_id = fields.Many2one(
        "sale.order.line",
        string="Original Prescription Line",
        copy=False,
        readonly=True,
        index=True,
        help="Original prescription line that created this back-order line.",
    )
    prescription_backorder_line_ids = fields.One2many(
        "sale.order.line",
        "prescription_backorder_origin_line_id",
        string="Linked Back-Order Lines",
        readonly=True,
        help="Back-order lines created from this original prescription line.",
    )
    dispensing_component_ids = fields.One2many(
        "sale.order.line.dispensing.component",
        "sale_order_line_id",
        string="Dispensing Components",
        copy=False,
        help="Prepack/product components used to serve this prescription line.",
    )

    is_existing_prescription = fields.Boolean(
        string="Existing Prescription",
        compute="_compute_is_existing_prescription",
        store=True,
    )

    prescribed_product_id = fields.Many2one(
        "product.product",
        string="Prescribed Product",
        readonly=True,
        copy=False,
    )

    prescribed_qty_base_units = fields.Float(
        string="Prescribed Qty (Base Units)",
        digits=(16, 4),
        readonly=True,
        copy=False,
    )

    prescribed_uom_id = fields.Many2one(
        "uom.uom",
        string="Prescribed UoM",
        readonly=True,
        copy=False,
    )

    base_product_id = fields.Many2one(
        "product.product",
        string="Base Product",
        readonly=True,
        copy=False,
    )

    is_pack_substituted = fields.Boolean(
        string="Pack Substituted",
        default=False,
        readonly=True,
        copy=False,
    )

    substitution_timestamp = fields.Datetime(
        string="Substitution Timestamp",
        readonly=True,
        copy=False,
    )

    substitution_user_id = fields.Many2one(
        "res.users",
        string="Substituted By",
        readonly=True,
        copy=False,
    )

    substitution_note = fields.Text(
        string="Substitution Note",
        readonly=True,
        copy=False,
    )

    substitution_source_line_ref = fields.Char(
        string="Substitution Source Ref",
        readonly=True,
        copy=False,
    )

    can_suggest_pack = fields.Boolean(
        string="Can Suggest Pack",
        compute="_compute_pack_substitution_state",
    )

    dispensing_pack_candidate_count = fields.Integer(
        string="Dispensing Pack Candidate Count",
        compute="_compute_pack_substitution_state",
    )

    stock_on_hand = fields.Float(
        string="Stock On Hand",
        digits=(16, 4),
        compute="_compute_stock_on_hand",
    )
    selected_batch_available_qty = fields.Float(
        string="Selected Batch Available",
        digits=(16, 4),
        compute="_compute_selected_batch_available_qty",
    )

    # ============ COMPUTED FIELDS ============
    out_of_stock = fields.Boolean(
        string="Out of Stock",
        compute="_compute_out_of_stock",
        store=True
    )

    has_prescription_data = fields.Boolean(
        string="Has Prescription Data",
        compute="_compute_has_prescription_data",
        store=True,
        help="True if this line has prescription dosing data",
    )

    full_prescription_text = fields.Char(
        string="Prescription",
        compute="_compute_full_prescription_text",
        store=True,
        help="Complete prescription instruction as text",
    )

    prescription_summary = fields.Char(
        string="Prescription Summary",
        compute="_compute_prescription_summary",
        store=True,
        help="Short summary of prescription details",
    )

    prescription_status = fields.Selection(
        selection=[
            ("awaiting_dispensing", "Not Dispensed"),
            ("partially_fulfilled", "Partially Dispensed"),
            ("fully_served", "Fully Dispensed"),
            ("served_externally", "Served Externally"),
            ("balance_waived", "Balance Waived"),
        ],
        string="Prescription Status",
        compute="_compute_prescription_status",
        store=True,
        readonly=True,
        copy=False,
    )

    @api.depends("external_order_uuid", "order_number")
    def _compute_is_existing_prescription(self):
        for line in self:
            line.is_existing_prescription = bool(
                line.external_order_uuid or line.order_number
            )

    @api.model_create_multi
    def create(self, vals_list):
        normalized_vals_list = [
            self._normalize_prescription_serving_vals(vals) for vals in vals_list
        ]
        lines = super().create(normalized_vals_list)
        if not self.env.context.get("skip_prescription_init"):
            lines._initialize_prescribed_metadata(force_missing_only=True)
        return lines

    def write(self, vals):
        normalized_vals = vals
        if "served_internally" in vals or "product_uom_qty" in vals:
            normalized_vals = self._normalize_prescription_serving_vals(
                vals, current_line=self[:1] if len(self) == 1 else None
            )
        result = super().write(normalized_vals)
        tracked_fields = {"product_id", "product_uom_qty", "product_uom"}
        if (
            not self.env.context.get("skip_prescription_init")
            and tracked_fields.intersection(normalized_vals)
        ):
            self._initialize_prescribed_metadata(force_missing_only=True)
        return result

    def _normalize_prescription_serving_vals(self, vals, current_line=None):
        vals = dict(vals)
        current_line = current_line or self[:1]
        current_line = current_line[:1] if current_line else self.env["sale.order.line"]

        incoming_served = vals.get("served_internally", None)
        current_qty = current_line.product_uom_qty if current_line else 0.0
        incoming_qty = vals.get("product_uom_qty", current_qty)
        current_prescribed_qty = (
            current_line.prescribed_qty_base_units if current_line else 0.0
        )

        if incoming_served is False:
            if "prescribed_qty_base_units" not in vals and float_is_zero(
                current_prescribed_qty, precision_digits=6
            ):
                vals["prescribed_qty_base_units"] = incoming_qty or current_qty or 0.0
            vals["product_uom_qty"] = 0.0
            vals["dispensed"] = True
        elif incoming_served is True and current_line and not current_line.served_internally:
            vals.setdefault("dispensed", False)
        elif (
            incoming_qty is not None
            and float_compare(incoming_qty, 0.0, precision_digits=6) > 0
            and incoming_served is not False
            and current_line
            and not current_line.served_internally
        ):
            vals.setdefault("served_internally", True)
            vals.setdefault("dispensed", False)

        return vals

    def _initialize_prescribed_metadata(self, force_missing_only=True):
        for line in self.filtered(lambda l: not l.display_type):
            base_product = line._get_dispensing_base_product()
            vals = {}
            if not force_missing_only or not line.prescribed_product_id:
                vals["prescribed_product_id"] = line.product_id.id
            if (
                not force_missing_only
                or float_is_zero(line.prescribed_qty_base_units, precision_digits=6)
            ):
                vals["prescribed_qty_base_units"] = line.product_uom_qty
            if not force_missing_only or not line.prescribed_uom_id:
                vals["prescribed_uom_id"] = line.product_uom.id
            if not force_missing_only or not line.base_product_id:
                vals["base_product_id"] = base_product.id
            if not force_missing_only or not line.substitution_source_line_ref:
                vals["substitution_source_line_ref"] = (
                    line.external_order_uuid
                    or line.order_number
                    or f"SOL-{line.id}"
                )
            if vals:
                super(ExtendedSaleOrderLine, line).write(vals)

    # ============ COMPUTE METHODS ============
    @api.depends(
        "frequency",
        "route",
        "dose",
        "duration",
        "num_refills",
        "administration_instructions",
    )
    def _compute_has_prescription_data(self):
        """Check if any prescription data exists"""
        for line in self:
            line.has_prescription_data = any(
                [
                    line.frequency,
                    line.route,
                    line.dose,
                    line.duration,
                    line.num_refills,
                    line.administration_instructions,
                ]
            )

    @api.depends(
        "product_id",
        "prescribed_product_id",
        "frequency",
        "route",
        "dose",
        "dose_units",
        "duration",
        "duration_units",
        "num_refills",
        "as_needed",
        "administration_instructions",
        "drug_form",
    )
    def _compute_full_prescription_text(self):
        """Create complete prescription instruction text"""
        for line in self:
            parts = []
            prescription_product = line.prescribed_product_id or line.product_id

            # Drug name
            if prescription_product:
                parts.append(prescription_product.name)

            # Dosage
            if line.dose and line.dose_units:
                parts.append(f"{line.dose} {line.dose_units}")

            # Frequency and route
            if line.frequency:
                parts.append(line.frequency)

            if line.route:
                parts.append(f"via {line.route}")

            # Duration
            if line.duration and line.duration_units:
                parts.append(f"for {line.duration} {line.duration_units}")

            # PRN
            if line.as_needed:
                parts.append("(as needed)")

            # Refills
            if line.num_refills and line.num_refills > 0:
                parts.append(f"with {line.num_refills} refill(s)")

            # Instructions
            if line.administration_instructions:
                parts.append(f"Instructions: {line.administration_instructions}")

            line.full_prescription_text = (
                " ".join(parts) if parts else "No prescription details"
            )

    @api.depends("frequency", "route", "dose", "dose_units")
    def _compute_prescription_summary(self):
        """Create a short summary for display"""
        for line in self:
            parts = []

            if line.dose and line.dose_units:
                parts.append(f"{line.dose}{line.dose_units}")

            if line.frequency:
                parts.append(line.frequency)

            if line.route:
                parts.append(line.route)

            line.prescription_summary = " | ".join(parts) if parts else "-"

    def _get_dosage_instruction_text(self):
        self.ensure_one()
        parts = []

        if self.dose and self.dose_units:
            parts.append(f"{self.dose} {self.dose_units}")
        elif self.dose:
            parts.append(str(self.dose))

        if self.frequency:
            parts.append(self.frequency)

        if self.route:
            parts.append(f"via {self.route}")

        if self.duration and self.duration_units:
            parts.append(f"for {self.duration} {self.duration_units}")

        if self.as_needed:
            parts.append("(as needed)")

        if self.administration_instructions:
            parts.append(self.administration_instructions)

        return " ".join(parts) if parts else (self.full_prescription_text or "")

    @api.depends(
        "display_type",
        "served_internally",
        "product_uom_qty",
        "dispensing_component_ids.dispensed_qty_base_units",
        "prescribed_qty_base_units",
        "balance_resolution",
        "balance_resolution_note",
    )
    def _compute_prescription_status(self):
        for line in self:
            line.prescription_status = line._get_prescription_status()

    def _get_prescription_quantities(self):
        self.ensure_one()
        prescribed_qty = self.prescribed_qty_base_units or self.product_uom_qty or 0.0
        served_qty = self._get_total_component_dispensed_qty()
        if not self.dispensing_component_ids:
            served_qty = self.product_uom_qty or 0.0
        return prescribed_qty, served_qty

    def _get_total_component_dispensed_qty(self):
        self.ensure_one()
        if not self.served_internally:
            return 0.0
        return sum(self.dispensing_component_ids.mapped("dispensed_qty_base_units"))

    def _sync_component_total_to_line(self):
        for line in self:
            if not line.dispensing_component_ids:
                continue
            total_qty = line._get_total_component_dispensed_qty()
            vals = {
                "product_uom_qty": total_qty,
                "served_internally": True,
                "is_pack_substituted": any(
                    component.is_substitution
                    for component in line.dispensing_component_ids
                ),
            }
            first_component = line.dispensing_component_ids.sorted("id")[:1]
            vals["dispensing_batch_number"] = first_component.batch_number or False
            vals["barcode_scan"] = first_component.barcode or False
            parent_lot = line._get_default_dispensing_lot(line.product_id, total_qty or 1.0)
            if parent_lot:
                vals.update(
                    line._prepare_dispensing_batch_selection_vals(
                        parent_lot.name,
                        product=line.product_id,
                    )
                )
            line.with_context(skip_prescription_init=True).write(vals)

    def _has_outstanding_prescription_balance(self):
        self.ensure_one()
        if self.display_type or not self.served_internally:
            return False
        prescribed_qty, served_qty = self._get_prescription_quantities()
        return float_compare(
            served_qty,
            prescribed_qty,
            precision_rounding=self.product_uom.rounding or 0.0001,
        ) < 0

    def _has_balance_resolution(self):
        self.ensure_one()
        if not self.balance_resolution:
            return False
        if self.balance_resolution == "other" and not (self.balance_resolution_note or "").strip():
            return False
        return True

    def _get_prescription_status(self):
        self.ensure_one()
        if self.display_type:
            return False
        if not self.served_internally:
            return "served_externally"

        prescribed_qty, served_qty = self._get_prescription_quantities()
        qty_rounding = self.product_uom.rounding or 0.0001
        if (
            float_compare(served_qty, prescribed_qty, precision_rounding=qty_rounding) < 0
            and self._has_balance_resolution()
        ):
            return "balance_waived"
        if float_compare(served_qty, 0.0, precision_rounding=qty_rounding) <= 0:
            return "awaiting_dispensing"
        if float_compare(served_qty, prescribed_qty, precision_rounding=qty_rounding) < 0:
            return "partially_fulfilled"
        return "fully_served"

    def _is_effectively_fully_served(self):
        self.ensure_one()
        if self.display_type:
            return False
        return self._get_prescription_status() in (
            "fully_served",
            "served_externally",
            "balance_waived",
        )

    def _has_positive_internal_service(self):
        self.ensure_one()
        if self.display_type or not self.served_internally:
            return False
        _, served_qty = self._get_prescription_quantities()
        qty_rounding = self.product_uom.rounding or 0.0001
        return float_compare(served_qty, 0.0, precision_rounding=qty_rounding) > 0
    
    @api.depends(
        "product_id",
        "product_uom_qty",
        "order_id.shop_id",
        "order_id.shop_id.location_id",
        "order_id.warehouse_id",
        "order_id.warehouse_id.lot_stock_id",
    )
    def _compute_out_of_stock(self):
        for line in self:
            if line.product_id:
                available_qty = line.stock_on_hand
                required_qty = line.product_uom._compute_quantity(
                    line.product_uom_qty or 0.0,
                    line.product_id.uom_id,
                )
                line.out_of_stock = float_compare(
                    available_qty,
                    required_qty,
                    precision_rounding=line.product_id.uom_id.rounding or 0.0001,
                ) < 0
            else:
                line.out_of_stock = False

    @api.onchange("barcode_scan")
    def _onchange_barcode_scan(self):
        Product = self.env["product.product"]
        for line in self:
            barcode = (line.barcode_scan or "").strip()
            if not barcode:
                continue
            prepack_line = line._get_prepack_line_from_barcode(barcode)
            if prepack_line:
                line._apply_prepack_line_barcode(prepack_line, barcode, use_write=False)
                continue
            product = Product.search([("barcode", "=", barcode)], limit=1)
            if not product:
                product = Product.search([("default_code", "=", barcode)], limit=1)
            if not product:
                return {
                    "warning": {
                        "title": _("Barcode Not Found"),
                        "message": _("No product was found for barcode %s.") % barcode,
                    }
                }

            qty = product.pack_unit_qty if product.is_dispensing_pack and product.pack_unit_qty else 1.0
            line.product_id = product
            line.product_uom = product.uom_id
            line.product_uom_qty = qty
            line.served_internally = True

            lot = line._get_default_dispensing_lot(product, qty)
            batch_vals = line._prepare_dispensing_batch_selection_vals(
                lot.name if lot else False,
                product=product,
            )
            for field_name, field_value in batch_vals.items():
                line[field_name] = field_value

    def action_apply_barcode_scan(self, barcode):
        self.ensure_one()
        barcode = (barcode or "").strip()
        if not barcode:
            return False

        prepack_line = self._get_prepack_line_from_barcode(barcode)
        if prepack_line:
            self._apply_prepack_line_barcode(prepack_line, barcode, use_write=True)
            return self.order_id._serialize_dispensing_line(self)

        product = self.env["product.product"].search([("barcode", "=", barcode)], limit=1)
        if not product:
            product = self.env["product.product"].search([("default_code", "=", barcode)], limit=1)
        if not product:
            raise UserError(_("No product was found for barcode %s.") % barcode)

        qty = (
            product.pack_unit_qty
            if product.is_dispensing_pack and product.pack_unit_qty
            else 1.0
        )
        lot = self._get_default_dispensing_lot(product, qty)
        write_vals = {
            "barcode_scan": barcode,
            "product_id": product.id,
            "product_uom": product.uom_id.id,
            "product_uom_qty": qty,
            "served_internally": True,
        }
        write_vals.update(
            self._prepare_dispensing_batch_selection_vals(
                lot.name if lot else False,
                product=product,
            )
        )
        self.with_context(skip_prescription_init=True).write(write_vals)
        return self.order_id._serialize_dispensing_line(self)

    def _get_prepack_line_from_barcode(self, barcode):
        if "bahmni.prepack.batch.line" not in self.env:
            return False
        return self.env["bahmni.prepack.batch.line"].search(
            [("label_barcode", "=", barcode), ("state", "=", "done")],
            limit=1,
        )

    def _apply_prepack_line_barcode(self, prepack_line, barcode, use_write=False):
        self.ensure_one()
        product = prepack_line.product_id
        qty = (
            product.pack_unit_qty
            if product.is_dispensing_pack and product.pack_unit_qty
            else 1.0
        )
        lot = prepack_line.finished_lot_id or self._get_default_dispensing_lot(product, qty)
        values = {
            "barcode_scan": barcode,
            "product_id": product.id,
            "product_uom": product.uom_id.id,
            "product_uom_qty": qty,
            "served_internally": True,
        }
        values.update(
            self._prepare_dispensing_batch_selection_vals(
                lot.name if lot else False,
                product=product,
            )
        )
        if use_write:
            self.with_context(skip_prescription_init=True).write(values)
            return
        for field_name, field_value in values.items():
            self[field_name] = field_value

    def _get_dispensing_batch_clear_vals(self):
        self.ensure_one()
        vals = {"dispensing_batch_number": False}
        for field_name in (
            "lot_id",
            "existing_lot_id",
            "batch_id",
            "prodlot_id",
            "expire_date",
            "expiry_date",
            "expiration_date",
        ):
            if field_name in self._fields:
                vals[field_name] = False
        for field_name in ("elmis_product_id", "elmis_lot_id", "elmis_program_id"):
            if field_name in self._fields:
                vals[field_name] = False
        return vals

    def _prepare_dispensing_product_change_vals(self, product):
        self.ensure_one()
        vals = {
            "product_id": product.id,
            "product_uom": product.uom_id.id,
            "name": product.get_product_multiline_description_sale() or product.display_name,
        }
        vals.update(self._get_dispensing_batch_clear_vals())
        vals.update(self._prepare_elmis_dispensing_product_vals(product))
        return vals

    def _prepare_elmis_dispensing_product_vals(self, product):
        self.ensure_one()
        if "elmis_product_id" not in self._fields:
            return {}
        if not product or not product.is_elmis_product:
            return {
                "elmis_product_id": False,
                "elmis_lot_id": False,
                "elmis_program_id": False,
            }

        vals = {
            "elmis_product_id": product.id,
            "elmis_lot_id": False,
            "elmis_program_id": False,
        }
        if len(product.elmis_program_ids) == 1:
            vals["elmis_program_id"] = product.elmis_program_ids.id
        return vals

    def _get_available_lot_entries(self, product=None):
        self.ensure_one()
        product = product or self.product_id
        if not product:
            return []

        source_location = self._get_dispensing_source_location()
        quant_domain = [
            ("product_id", "=", product.id),
            ("location_id.usage", "=", "internal"),
            ("lot_id", "!=", False),
        ]
        if source_location:
            quant_domain.append(("location_id", "child_of", source_location.id))

        lot_entries = {}
        rounding = product.uom_id.rounding or 0.0001
        for quant in self.env["stock.quant"].search(quant_domain):
            available_qty = quant.quantity - quant.reserved_quantity
            if float_compare(available_qty, 0.0, precision_rounding=rounding) <= 0:
                continue
            lot = quant.lot_id
            entry = lot_entries.setdefault(
                lot.id,
                {
                    "lot": lot,
                    "available_qty": 0.0,
                },
            )
            entry["available_qty"] += available_qty

        return sorted(
            lot_entries.values(),
            key=lambda item: (
                item["available_qty"] <= 0,
                self._get_lot_expiry_key(item["lot"]),
                item["lot"].name or "",
            ),
        )

    def _find_dispensing_lot(self, product, batch_number):
        self.ensure_one()
        batch_number = (batch_number or "").strip()
        if not product or not batch_number:
            return self.env["stock.lot"]
        return self.env["stock.lot"].search(
            [
                ("name", "=", batch_number),
                ("product_id", "=", product.id),
            ],
            limit=1,
        )

    def _get_dispensing_source_location(self):
        self.ensure_one()
        shop = self.order_id.shop_id
        if shop and "location_id" in shop._fields and shop.location_id:
            return shop.location_id
        warehouse = self.order_id.warehouse_id
        if warehouse and warehouse.lot_stock_id:
            return warehouse.lot_stock_id
        return self.env["stock.location"]

    def _get_dispensing_lot_available_qty(self, product=None, lot=None):
        self.ensure_one()
        product = product or self.product_id
        lot = lot or self._find_dispensing_lot(product, self.dispensing_batch_number)
        source_location = self._get_dispensing_source_location()
        if not product or not lot or not source_location:
            return 0.0
        quants = self.env["stock.quant"].search(
            [
                ("product_id", "=", product.id),
                ("location_id", "child_of", source_location.id),
                ("lot_id", "=", lot.id),
            ]
        )
        return sum(quant.quantity - quant.reserved_quantity for quant in quants)

    def _format_dispensing_lot_expiry(self, lot):
        self.ensure_one()
        for field_name in ("expiration_date", "use_date", "removal_date", "alert_date"):
            if field_name in lot._fields and getattr(lot, field_name):
                field = lot._fields[field_name]
                value = getattr(lot, field_name)
                if field.type == "date":
                    return fields.Date.to_string(value)
                return fields.Datetime.to_string(value)
        return ""

    def _get_dispensing_batch_options(self, product=None):
        self.ensure_one()
        product = product or self.product_id
        if not product:
            return []

        options = []
        seen_lot_ids = set()
        for entry in self._get_available_lot_entries(product):
            lot = entry["lot"]
            options.append(
                {
                    "batch_number": lot.name or "",
                    "expiry_date": self._format_dispensing_lot_expiry(lot),
                    "available_qty": entry["available_qty"],
                }
            )
            seen_lot_ids.add(lot.id)

        current_lot = self._find_dispensing_lot(product, self.dispensing_batch_number)
        if current_lot and current_lot.id not in seen_lot_ids:
            options.append(
                {
                    "batch_number": current_lot.name or "",
                    "expiry_date": self._format_dispensing_lot_expiry(current_lot),
                    "available_qty": 0.0,
                }
            )
            seen_lot_ids.add(current_lot.id)
        current_batch_number = (self.dispensing_batch_number or "").strip()
        if current_batch_number and current_batch_number not in [option["batch_number"] for option in options]:
            options.append(
                {
                    "batch_number": current_batch_number,
                    "expiry_date": "",
                    "available_qty": 0.0,
                }
            )
        return options

    @api.depends(
        "product_id",
        "dispensing_batch_number",
        "order_id.shop_id",
        "order_id.shop_id.location_id",
        "order_id.warehouse_id",
        "order_id.warehouse_id.lot_stock_id",
    )
    def _compute_selected_batch_available_qty(self):
        for line in self:
            line.selected_batch_available_qty = line._get_dispensing_lot_available_qty()

    def _prepare_dispensing_batch_selection_vals(self, batch_number=False, product=None):
        self.ensure_one()
        product = product or self.product_id
        batch_number = (batch_number or "").strip()
        if not product or not batch_number:
            return self._get_dispensing_batch_clear_vals()

        lot = self._find_dispensing_lot(product, batch_number)
        if not lot:
            vals = self._get_dispensing_batch_clear_vals()
            vals["dispensing_batch_number"] = batch_number
            return vals

        vals = self._prepare_batch_sync_vals(lot)
        vals["dispensing_batch_number"] = lot.name
        return vals

    def _sync_dispensing_batch_selection(self, batch_number=False, product=None):
        self.ensure_one()
        vals = self._prepare_dispensing_batch_selection_vals(batch_number, product=product)
        self.with_context(skip_prescription_init=True).write(vals)
        return vals

    def _get_fefo_available_lot(self, product, required_qty=1.0):
        self.ensure_one()
        rounding = product.uom_id.rounding or 0.0001
        for entry in self._get_available_lot_entries(product):
            if float_compare(
                entry["available_qty"],
                required_qty,
                precision_rounding=rounding,
            ) >= 0:
                return entry["lot"]
        return self.env["stock.lot"]

    def _get_default_dispensing_lot(self, product, required_qty=1.0):
        self.ensure_one()
        lot = self._get_fefo_available_lot(product, required_qty=required_qty)
        if lot:
            return lot
        for entry in self._get_available_lot_entries(product):
            if float_compare(
                entry["available_qty"],
                0.0,
                precision_rounding=product.uom_id.rounding or 0.0001,
            ) > 0:
                return entry["lot"]
        return self.env["stock.lot"]

    @api.depends(
        "display_type",
        "dispensed",
        "order_id.state",
        "qty_delivered",
        "qty_invoiced",
        "prescribed_qty_base_units",
        "base_product_id",
        "prescribed_product_id",
        "product_id",
    )
    def _compute_pack_substitution_state(self):
        for line in self:
            count = 0
            if line._is_pack_substitution_allowed():
                count = line._count_available_pack_products()
            line.dispensing_pack_candidate_count = count
            line.can_suggest_pack = bool(count)

    @api.depends(
        "product_id",
        "order_id.shop_id",
        "order_id.shop_id.location_id",
        "order_id.warehouse_id",
        "order_id.warehouse_id.lot_stock_id",
    )
    def _compute_stock_on_hand(self):
        Quant = self.env["stock.quant"]
        for line in self:
            stock_on_hand = 0.0
            if line.product_id:
                domain = [
                    ("product_id", "=", line.product_id.id),
                    ("location_id.usage", "=", "internal"),
                ]
                source_location = line._get_dispensing_source_location()
                if source_location:
                    domain.append(("location_id", "child_of", source_location.id))
                quants = Quant.search(domain)
                stock_on_hand = sum(quants.mapped("available_quantity"))
            line.stock_on_hand = stock_on_hand

    # ============ UTILITY METHODS ============
    def get_prescription_data_dict(self):
        """Return prescription data as dictionary"""
        self.ensure_one()
        return {
            "frequency": self.frequency,
            "route": self.route,
            "dose": self.dose,
            "dose_units": self.dose_units,
            "duration": self.duration,
            "duration_units": self.duration_units,
            "num_refills": self.num_refills,
            "as_needed": self.as_needed,
            "administration_instructions": self.administration_instructions,
            "drug_form": self.drug_form,
            "drug_uuid": self.drug_uuid,
            "external_order_uuid": self.external_order_uuid,
            "previous_order_uuid": self.previous_order_uuid,
            "order_number": self.order_number,
            "concept_name": self.concept_name,
            "dispensed": self.dispensed,
            "full_prescription": self.full_prescription_text,
            "prescription_summary": self.prescription_summary,
            "prescribed_product_id": self.prescribed_product_id.id,
            "prescribed_qty_base_units": self.prescribed_qty_base_units,
            "prescribed_uom_id": self.prescribed_uom_id.id,
            "base_product_id": self.base_product_id.id,
            "is_pack_substituted": self.is_pack_substituted,
            "substitution_timestamp": self.substitution_timestamp,
            "substitution_user_id": self.substitution_user_id.id,
            "substitution_note": self.substitution_note,
            "substitution_source_line_ref": self.substitution_source_line_ref,
        }

    @api.model
    def search_by_external_order_uuid(self, order_uuid):
        """Find order line by Bahmni order UUID"""
        return self.search([("external_order_uuid", "=", order_uuid)], limit=1)

    @api.model
    def search_by_order_number(self, order_number):
        """Find order line by Bahmni order number"""
        return self.search([("order_number", "=", order_number)], limit=1)
        # ============ ALLERGY FIELDS ============

    allergy_checked = fields.Boolean(
        string="Allergy Checked",
        default=False,
        help="Indicates if allergy check was performed",
    )

    has_allergy_warning = fields.Boolean(
        string="Allergy Warning",
        compute="_compute_has_allergy_warning",
        store=True,
        help="True if patient has allergy to this drug",
    )

    allergy_warning_details = fields.Text(
        string="Allergy Details", help="Details of drug allergy if present"
    )

    allergy_override = fields.Boolean(
        string="Allergy Override",
        default=False,
        help="Manual override for allergy warning",
    )

    allergy_override_reason = fields.Text(
        string="Override Reason", help="Reason for overriding allergy warning"
    )

    # ============ COMPUTE METHODS ============
    @api.depends("order_id.partner_id", "product_id.name")
    def _compute_has_allergy_warning(self):
        """Check if drug has allergy warning"""
        for line in self:
            if not line.order_id or not line.order_id.partner_id or not line.product_id:
                line.has_allergy_warning = False
                continue

            # Only check if line hasn't been overridden
            if line.allergy_override:
                line.has_allergy_warning = False
                continue

            # Perform allergy check
            has_allergy, allergy_details = line.order_id.partner_id.check_drug_allergy(
                line.product_id.name
            )

            line.has_allergy_warning = has_allergy

            if has_allergy and allergy_details:
                # Store allergy details
                line.allergy_warning_details = json.dumps(allergy_details, indent=2)
            else:
                line.allergy_warning_details = False

    # ============ BUSINESS METHODS ============
    def check_allergy(self):
        """Manually trigger allergy check"""
        self.ensure_one()

        if not self.order_id or not self.order_id.partner_id or not self.product_id:
            return {
                "warning": {
                    "title": "Missing Information",
                    "message": "Cannot check allergy - missing patient or drug information.",
                }
            }

        has_allergy, allergy_details = self.order_id.partner_id.check_drug_allergy(
            self.product_id.name
        )

        self.allergy_checked = True
        self.allergy_override = False  # Reset override on new check

        if has_allergy:
            self.allergy_warning_details = json.dumps(allergy_details, indent=2)
            self.has_allergy_warning = True

            warnings = self.order_id.partner_id.get_allergy_warnings(
                self.product_id.name
            )

            return {
                "warning": {
                    "title": "DRUG ALLERGY WARNING",
                    "message": f"Patient has allergy to {self.product_id.name}",
                    "details": warnings,
                }
            }
        else:
            self.allergy_warning_details = False
            self.has_allergy_warning = False

            return {
                "info": {
                    "title": "Allergy Check Complete",
                    "message": f"No allergies found for {self.product_id.name}",
                }
            }

    def override_allergy_warning(self):
        """Override allergy warning with reason"""
        self.ensure_one()

        return {
            "name": "Override Allergy Warning",
            "type": "ir.actions.act_window",
            "res_model": "allergy.override.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_line_id": self.id,
                "default_patient_id": self.order_id.partner_id.id
                if self.order_id
                else False,
                "default_drug_name": self.product_id.name if self.product_id else "",
                "default_order_id": self.order_id.id if self.order_id else False,
            },
        }

    def _get_dispensing_base_product(self):
        self.ensure_one()
        return (
            self.base_product_id
            or self.prescribed_product_id
            or self.product_id.dispensing_base_product_id
            or self.product_id
        )

    def _is_pack_substitution_allowed(self):
        self.ensure_one()
        if self.display_type or self.dispensed:
            return False
        if self.order_id.state in ("cancel", "done"):
            return False
        qty_rounding = self.product_uom.rounding or 0.0001
        if float_compare(self.qty_delivered, 0.0, precision_rounding=qty_rounding) > 0:
            return False
        if float_compare(self.qty_invoiced, 0.0, precision_rounding=qty_rounding) > 0:
            return False
        if float_compare(self.prescribed_qty_base_units or self.product_uom_qty, 0.0, precision_rounding=qty_rounding) <= 0:
            return False
        return bool(self._count_available_pack_products())

    def _get_dispensing_pack_domain(self):
        self.ensure_one()
        base_product = self._get_dispensing_base_product()
        if not base_product:
            return [("id", "=", 0)]
        return [
            ("active", "=", True),
            ("sale_ok", "=", True),
            ("is_dispensing_pack", "=", True),
            ("dispensing_pack_enabled", "=", True),
            ("dispensing_base_product_id", "=", base_product.id),
        ]

    def _count_available_pack_products(self):
        self.ensure_one()
        qty = self.prescribed_qty_base_units or self.product_uom_qty
        if qty <= 0:
            return 0
        packs = self.env["product.product"].search(self._get_dispensing_pack_domain())
        return len([candidate for candidate in packs if self._build_pack_candidate(candidate)])

    def _get_pack_batch_summary(self, pack_product):
        self.ensure_one()
        quants = self._get_available_pack_quants(pack_product)
        lots = []
        for quant in quants:
            available_qty = quant.quantity - quant.reserved_quantity
            if float_compare(
                available_qty,
                0.0,
                precision_rounding=pack_product.uom_id.rounding or 0.0001,
            ) <= 0:
                continue
            lots.append((quant.lot_id.name, available_qty))
        if not lots:
            return _("No tracked batches available")
        lots.sort(key=lambda item: item[0])
        summary = ", ".join(
            _("%(lot)s (%(qty)s)", lot=lot_name, qty=qty)
            for lot_name, qty in lots[:5]
        )
        if len(lots) > 5:
            summary = _(
                "%(summary)s, +%(count)s more",
                summary=summary,
                count=len(lots) - 5,
            )
        return summary

    def _get_available_pack_quants(self, pack_product):
        self.ensure_one()
        quant_domain = [
            ("product_id", "=", pack_product.id),
            ("location_id.usage", "=", "internal"),
            ("lot_id", "!=", False),
        ]
        source_location = self._get_dispensing_source_location()
        if source_location:
            quant_domain.append(("location_id", "child_of", source_location.id))
        return self.env["stock.quant"].search(quant_domain)

    def _check_dispensing_batch_available(self):
        for line in self:
            if line.dispensing_component_ids:
                line.dispensing_component_ids._check_dispensing_batch_available()
                continue
            if (
                line.display_type
                or not line.served_internally
                or not line.product_id
                or line.product_id.tracking == "none"
            ):
                continue
            required_qty = line.product_uom._compute_quantity(
                line.product_uom_qty or 0.0,
                line.product_id.uom_id,
            )
            if float_compare(
                required_qty,
                0.0,
                precision_rounding=line.product_id.uom_id.rounding or 0.0001,
            ) <= 0:
                continue
            lot = line._find_dispensing_lot(line.product_id, line.dispensing_batch_number)
            if not lot:
                raise UserError(
                    _("Select an available batch before serving %(product)s.")
                    % {"product": line.product_id.display_name}
                )
            available_qty = line._get_dispensing_lot_available_qty(line.product_id, lot)
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
                        "lot": lot.name,
                        "location": line._get_dispensing_source_location().display_name,
                        "requested": required_qty,
                        "available": available_qty,
                        "uom": line.product_id.uom_id.name,
                    }
                )

    def _get_lot_expiry_key(self, lot):
        self.ensure_one()
        for field_name in ("removal_date", "use_date", "expiration_date", "alert_date"):
            if field_name in lot._fields and getattr(lot, field_name):
                return getattr(lot, field_name)
        return fields.Datetime.to_datetime("9999-12-31 00:00:00")

    def _get_fefo_pack_lot(self, pack_product, required_qty=None):
        self.ensure_one()
        required_qty = required_qty or self.product_uom_qty or 0.0
        candidates = []
        for quant in self._get_available_pack_quants(pack_product):
            available_qty = quant.quantity - quant.reserved_quantity
            if float_compare(
                available_qty,
                required_qty,
                precision_rounding=pack_product.uom_id.rounding or 0.0001,
            ) < 0:
                continue
            lot = quant.lot_id
            candidates.append(
                (
                    self._get_lot_expiry_key(lot),
                    lot.name or "",
                    lot,
                )
            )
        if not candidates:
            return self.env["stock.lot"]
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    def _prepare_batch_sync_vals(self, lot):
        self.ensure_one()
        vals = {}
        if not lot:
            return vals
        lot_field_map = (
            "lot_id",
            "existing_lot_id",
            "batch_id",
            "prodlot_id",
        )
        for field_name in lot_field_map:
            if field_name in self._fields:
                field = self._fields[field_name]
                if field.comodel_name == "stock.lot":
                    vals[field_name] = lot.id
                elif field.type == "char":
                    vals[field_name] = lot.name
        for field_name in ("expire_date", "expiry_date", "expiration_date"):
            if field_name in self._fields:
                expiry_value = False
                for source_name in ("expiration_date", "use_date", "removal_date", "alert_date"):
                    if source_name in lot._fields and getattr(lot, source_name):
                        expiry_value = getattr(lot, source_name)
                        break
                vals[field_name] = expiry_value
        if "elmis_product_id" in self._fields:
            vals.update(self._prepare_elmis_dispensing_product_vals(lot.product_id))
            if lot.product_id.is_elmis_product:
                vals["elmis_lot_id"] = lot.id
        return vals

    def _sync_lot_from_reserved_stock(self, lot):
        for line in self:
            if not lot or not line.product_id or lot.product_id != line.product_id:
                continue
            vals = line._prepare_batch_sync_vals(lot)
            vals["dispensing_batch_number"] = lot.name
            if vals:
                line.with_context(skip_prescription_init=True).write(vals)

    def unlink(self):
        if self.env.context.get("allow_prescription_serve_cleanup"):
            return super().unlink()
        protected = self.filtered("is_existing_prescription")
        if protected:
            raise UserError(
                _("Pre-existing prescription panels cannot be removed. Only products added in this screen can be closed.")
            )
        return super().unlink()

    def _build_pack_candidate(self, pack_product):
        self.ensure_one()
        prescribed_qty = self.prescribed_qty_base_units or self.product_uom_qty
        if prescribed_qty <= 0 or pack_product.pack_unit_qty <= 0:
            return False
        quotient = prescribed_qty / pack_product.pack_unit_qty
        rounded_quotient = int(round(quotient))
        exact_fit = float_is_zero(quotient - rounded_quotient, precision_digits=6)
        if not exact_fit or rounded_quotient <= 0:
            return False
        available_qty = pack_product.free_qty
        if float_compare(
            available_qty,
            rounded_quotient,
            precision_rounding=pack_product.uom_id.rounding or 0.0001,
        ) < 0:
            return False
        return {
            "product_id": pack_product.id,
            "pack_unit_qty": pack_product.pack_unit_qty,
            "packs_needed": rounded_quotient,
            "covered_qty": rounded_quotient * pack_product.pack_unit_qty,
            "available_qty": available_qty,
            "batch_summary": self._get_pack_batch_summary(pack_product),
            "exact_fit": exact_fit,
            "substitution_priority": pack_product.substitution_priority,
        }

    def get_pack_substitution_candidates(self):
        self.ensure_one()
        pack_products = self.env["product.product"].search(
            self._get_dispensing_pack_domain()
        )
        candidates = []
        for pack_product in pack_products:
            candidate = self._build_pack_candidate(pack_product)
            if candidate:
                candidates.append(candidate)
        return sorted(
            candidates,
            key=lambda candidate: (
                candidate["substitution_priority"],
                candidate["pack_unit_qty"],
                candidate["available_qty"],
            ),
            reverse=True,
        )

    def action_open_pack_selection_wizard(self):
        self.ensure_one()
        if not self._is_pack_substitution_allowed():
            raise UserError(
                _("Pack substitution is not allowed for this line in its current state.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Select Dispensing Pack"),
            "res_model": "dispensing.pack.selection.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_sale_order_line_id": self.id,
                "active_id": self.id,
                "active_model": "sale.order.line",
            },
        }

    def _validate_pack_substitution(self, pack_product, packs_needed):
        self.ensure_one()
        base_product = self._get_dispensing_base_product()
        if not self._is_pack_substitution_allowed():
            raise UserError(
                _("This line can no longer be changed for pack substitution.")
            )
        if not pack_product.is_dispensing_pack or not pack_product.dispensing_pack_enabled:
            raise UserError(_("The selected product is not enabled as a dispensing pack."))
        if pack_product.dispensing_base_product_id != base_product:
            raise UserError(
                _("The selected pack does not belong to the same base product.")
            )
        if packs_needed <= 0:
            raise UserError(_("The selected pack quantity must be greater than zero."))
        expected_qty = self.prescribed_qty_base_units or self.product_uom_qty
        covered_qty = packs_needed * pack_product.pack_unit_qty
        if not float_is_zero(covered_qty - expected_qty, precision_digits=6):
            raise UserError(_("Only exact-fit pack substitutions are supported in v1."))
        if float_compare(
            pack_product.free_qty,
            packs_needed,
            precision_rounding=pack_product.uom_id.rounding or 0.0001,
        ) < 0:
            raise UserError(_("Insufficient stock is available for the selected pack."))
        return base_product

    def action_apply_pack_substitution(self, pack_product, packs_needed):
        self.ensure_one()
        pack_product = self.env["product.product"].browse(
            pack_product.id if isinstance(pack_product, models.BaseModel) else pack_product
        )
        packs_needed = int(packs_needed)
        base_product = self._validate_pack_substitution(pack_product, packs_needed)
        self._initialize_prescribed_metadata(force_missing_only=True)

        prescribed_qty = self.prescribed_qty_base_units or self.product_uom_qty
        prescribed_uom_name = self.prescribed_uom_id.display_name or self.product_uom.display_name
        note = _(
            "Prescribed: %(qty)s %(uom)s | Dispensed: %(packs)s x %(pack)s",
            qty=prescribed_qty,
            uom=prescribed_uom_name,
            packs=packs_needed,
            pack=pack_product.display_name,
        )

        write_vals = {
            "product_id": pack_product.id,
            "product_uom_qty": packs_needed,
            "product_uom": pack_product.uom_id.id,
            "name": pack_product.get_product_multiline_description_sale() or pack_product.display_name,
            "base_product_id": base_product.id,
            "is_pack_substituted": True,
            "substitution_timestamp": fields.Datetime.now(),
            "substitution_user_id": self.env.user.id,
            "substitution_note": note,
        }
        # Drop any lot/batch metadata carried from the original loose-product line.
        # The selected pack must use a lot that belongs to the pack SKU itself.
        for field_name in (
            "lot_id",
            "existing_lot_id",
            "batch_id",
            "prodlot_id",
            "expire_date",
            "expiry_date",
            "expiration_date",
        ):
            if field_name in self._fields:
                write_vals[field_name] = False

        self.with_context(skip_prescription_init=True).write(write_vals)
        fefo_lot = self._get_fefo_pack_lot(pack_product, packs_needed)
        if fefo_lot:
            self._sync_lot_from_reserved_stock(fefo_lot)
        self.order_id.message_post(
            body=_(
                "Line %(line)s substituted from %(base)s qty %(qty)s to %(pack)s qty %(packs)s by %(user)s on %(timestamp)s",
                line=self.display_name,
                base=base_product.display_name,
                qty=prescribed_qty,
                pack=pack_product.display_name,
                packs=packs_needed,
                user=self.env.user.display_name,
                timestamp=fields.Datetime.to_string(self.substitution_timestamp),
            )
        )
        return True


class SaleOrderLineDispensingComponent(models.Model):
    _name = "sale.order.line.dispensing.component"
    _description = "Prescription Dispensing Component"
    _order = "sale_order_line_id, id"

    sale_order_line_id = fields.Many2one(
        "sale.order.line",
        string="Prescription Line",
        required=True,
        ondelete="cascade",
        index=True,
    )
    order_id = fields.Many2one(
        "sale.order",
        string="Prescription",
        related="sale_order_line_id.order_id",
        store=True,
        readonly=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Dispensed Product",
        required=True,
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="UoM",
        required=True,
    )
    pack_count = fields.Float(
        string="Pack/Unit Count",
        digits=(16, 4),
        default=1.0,
        help="Number of prepacks, or loose units when the product is not a prepack.",
    )
    pack_unit_qty = fields.Float(
        string="Pack Size",
        digits=(16, 4),
        default=1.0,
        help="Base prescribed units covered by one selected pack/unit.",
    )
    dispensed_qty_base_units = fields.Float(
        string="Dispensed Qty",
        digits=(16, 4),
        compute="_compute_dispensed_qty_base_units",
        store=True,
    )
    barcode = fields.Char(string="Barcode", copy=False)
    batch_number = fields.Char(string="Batch Number", copy=False)
    comments = fields.Text(string="Comments", copy=False)
    selected_batch_available_qty = fields.Float(
        string="Selected Batch Available",
        digits=(16, 4),
        compute="_compute_selected_batch_available_qty",
    )
    stock_on_hand = fields.Float(
        string="Stock on Hand",
        digits=(16, 4),
        compute="_compute_stock_on_hand",
    )
    out_of_stock = fields.Boolean(
        string="Out of Stock",
        compute="_compute_out_of_stock",
    )
    is_substitution = fields.Boolean(
        string="Substitution",
        compute="_compute_is_substitution",
        store=True,
    )

    @api.depends("pack_count", "pack_unit_qty")
    def _compute_dispensed_qty_base_units(self):
        for component in self:
            component.dispensed_qty_base_units = (
                (component.pack_count or 0.0) * (component.pack_unit_qty or 0.0)
            )

    @api.depends("product_id", "sale_order_line_id.prescribed_product_id")
    def _compute_is_substitution(self):
        for component in self:
            prescribed_product = (
                component.sale_order_line_id.prescribed_product_id
                or component.sale_order_line_id.product_id
            )
            component.is_substitution = bool(
                component.product_id
                and prescribed_product
                and component.product_id != prescribed_product
            )

    @api.depends("product_id", "batch_number")
    def _compute_selected_batch_available_qty(self):
        for component in self:
            component.selected_batch_available_qty = (
                component._get_dispensing_lot_available_qty()
            )

    @api.depends(
        "product_id",
        "sale_order_line_id.order_id.shop_id",
        "sale_order_line_id.order_id.shop_id.location_id",
        "sale_order_line_id.order_id.warehouse_id",
        "sale_order_line_id.order_id.warehouse_id.lot_stock_id",
    )
    def _compute_stock_on_hand(self):
        Quant = self.env["stock.quant"]
        for component in self:
            stock_on_hand = 0.0
            if component.product_id:
                domain = [
                    ("product_id", "=", component.product_id.id),
                    ("location_id.usage", "=", "internal"),
                ]
                source_location = (
                    component.sale_order_line_id._get_dispensing_source_location()
                )
                if source_location:
                    domain.append(("location_id", "child_of", source_location.id))
                quants = Quant.search(domain)
                stock_on_hand = sum(quants.mapped("available_quantity"))
            component.stock_on_hand = stock_on_hand

    @api.depends("product_id", "pack_count", "stock_on_hand")
    def _compute_out_of_stock(self):
        for component in self:
            if component.product_id:
                component.out_of_stock = float_compare(
                    component.stock_on_hand or 0.0,
                    component.pack_count or 0.0,
                    precision_rounding=component.product_uom_id.rounding or 0.0001,
                ) < 0
            else:
                component.out_of_stock = False

    @api.model_create_multi
    def create(self, vals_list):
        normalized_vals = [self._normalize_vals(vals) for vals in vals_list]
        components = super().create(normalized_vals)
        components.mapped("sale_order_line_id")._sync_component_total_to_line()
        return components

    def write(self, vals):
        normalized_vals = self._normalize_vals(vals, current_component=self[:1])
        result = super().write(normalized_vals)
        self.mapped("sale_order_line_id")._sync_component_total_to_line()
        return result

    def unlink(self):
        lines = self.mapped("sale_order_line_id")
        result = super().unlink()
        lines._sync_component_total_to_line()
        for line in lines.filtered(lambda item: not item.dispensing_component_ids):
            line.with_context(skip_prescription_init=True).write(
                {
                    "product_uom_qty": 0.0,
                    "dispensing_batch_number": False,
                    "barcode_scan": False,
                }
            )
        return result

    def _normalize_vals(self, vals, current_component=None):
        vals = dict(vals)
        product = False
        if vals.get("product_id"):
            product = self.env["product.product"].browse(vals["product_id"]).exists()
            if not product:
                raise UserError(_("Selected product was not found."))
            vals.setdefault("product_uom_id", product.uom_id.id)
            vals.setdefault(
                "pack_unit_qty",
                product.pack_unit_qty
                if product.is_dispensing_pack and product.pack_unit_qty
                else 1.0,
            )
        return vals

    def _get_parent_line(self):
        self.ensure_one()
        return self.sale_order_line_id

    def _find_dispensing_lot(self):
        self.ensure_one()
        if not self.product_id or not self.batch_number:
            return self.env["stock.lot"]
        return self.sale_order_line_id._find_dispensing_lot(
            self.product_id,
            self.batch_number,
        )

    def _get_dispensing_lot_available_qty(self):
        self.ensure_one()
        lot = self._find_dispensing_lot()
        return self.sale_order_line_id._get_dispensing_lot_available_qty(
            self.product_id,
            lot,
        )

    def _get_dispensing_batch_options(self):
        self.ensure_one()
        return self.sale_order_line_id._get_dispensing_batch_options(self.product_id)

    def _get_default_dispensing_lot(self):
        self.ensure_one()
        return self.sale_order_line_id._get_default_dispensing_lot(
            self.product_id,
            self.pack_count or 1.0,
        )

    def _sync_batch_selection(self, batch_number=False):
        self.ensure_one()
        batch_number = (batch_number or "").strip()
        lot = self.sale_order_line_id._find_dispensing_lot(
            self.product_id,
            batch_number,
        )
        self.write({"batch_number": lot.name if lot else batch_number or False})

    def action_apply_barcode_scan(self, barcode):
        self.ensure_one()
        barcode = (barcode or "").strip()
        if not barcode:
            return False

        prepack_line = self.sale_order_line_id._get_prepack_line_from_barcode(barcode)
        if prepack_line:
            product = prepack_line.product_id
            lot = prepack_line.finished_lot_id
        else:
            product = self.env["product.product"].search([("barcode", "=", barcode)], limit=1)
            if not product:
                product = self.env["product.product"].search([("default_code", "=", barcode)], limit=1)
            if not product:
                raise UserError(_("No product was found for barcode %s.") % barcode)
            lot = False

        pack_unit_qty = (
            product.pack_unit_qty
            if product.is_dispensing_pack and product.pack_unit_qty
            else 1.0
        )
        if not lot:
            lot = self.sale_order_line_id._get_default_dispensing_lot(product, 1.0)
        self.write(
            {
                "barcode": barcode,
                "product_id": product.id,
                "product_uom_id": product.uom_id.id,
                "pack_count": 1.0,
                "pack_unit_qty": pack_unit_qty,
                "batch_number": lot.name if lot else False,
            }
        )
        return self.sale_order_line_id.order_id._serialize_dispensing_component(self)

    def _check_dispensing_batch_available(self):
        for component in self:
            if (
                not component.product_id
                or component.product_id.tracking == "none"
                or float_compare(
                    component.pack_count or 0.0,
                    0.0,
                    precision_rounding=component.product_uom_id.rounding or 0.0001,
                )
                <= 0
            ):
                continue
            lot = component._find_dispensing_lot()
            if not lot:
                raise UserError(
                    _("Select an available batch before serving %(product)s.")
                    % {"product": component.product_id.display_name}
                )
            available_qty = component._get_dispensing_lot_available_qty()
            if float_compare(
                available_qty,
                component.pack_count or 0.0,
                precision_rounding=component.product_uom_id.rounding or 0.0001,
            ) < 0:
                raise UserError(
                    _(
                        "Insufficient stock for %(product)s batch %(lot)s at %(location)s. "
                        "Requested: %(requested).2f %(uom)s. Available: %(available).2f %(uom)s."
                    )
                    % {
                        "product": component.product_id.display_name,
                        "lot": lot.name,
                        "location": component.sale_order_line_id._get_dispensing_source_location().display_name,
                        "requested": component.pack_count or 0.0,
                        "available": available_qty,
                        "uom": component.product_uom_id.name,
                    }
                )


class DispensingPackSelectionWizard(models.TransientModel):
    _name = "dispensing.pack.selection.wizard"
    _description = "Dispensing Pack Selection Wizard"

    sale_order_line_id = fields.Many2one(
        "sale.order.line",
        string="Prescription Line",
        required=True,
        readonly=True,
    )
    prescribed_product_id = fields.Many2one(
        "product.product",
        string="Prescribed Product",
        readonly=True,
    )
    prescribed_qty_base_units = fields.Float(
        string="Prescribed Qty",
        digits=(16, 4),
        readonly=True,
    )
    prescribed_uom_id = fields.Many2one(
        "uom.uom",
        string="Prescribed UoM",
        readonly=True,
    )
    base_product_id = fields.Many2one(
        "product.product",
        string="Base Product",
        readonly=True,
    )
    candidate_line_ids = fields.One2many(
        "dispensing.pack.selection.wizard.line",
        "wizard_id",
        string="Pack Options",
        readonly=True,
    )
    candidate_product_ids = fields.Many2many(
        "product.product",
        string="Candidate Products",
        compute="_compute_candidate_product_ids",
    )
    has_candidates = fields.Boolean(
        string="Has Candidates",
        compute="_compute_has_candidates",
    )
    empty_message = fields.Char(
        string="No Pack Options",
        readonly=True,
        default="No exact-fit pack options are available for this prescription line.",
    )
    selected_pack_product_id = fields.Many2one(
        "product.product",
        string="Selected Pack",
        domain="[('id', 'in', candidate_product_ids)]",
    )
    selected_pack_unit_qty = fields.Float(
        string="Selected Pack Size",
        digits=(16, 4),
        compute="_compute_selected_candidate_details",
    )
    selected_packs_needed = fields.Integer(
        string="Selected Packs Needed",
        compute="_compute_selected_candidate_details",
    )
    selected_available_qty = fields.Float(
        string="Selected Available Qty",
        digits=(16, 4),
        compute="_compute_selected_candidate_details",
    )
    selected_batch_summary = fields.Text(
        string="Selected Batches",
        compute="_compute_selected_candidate_details",
    )

    @api.depends("candidate_line_ids")
    def _compute_has_candidates(self):
        for wizard in self:
            wizard.has_candidates = bool(wizard.candidate_line_ids)

    @api.depends(
        "selected_pack_product_id",
        "candidate_line_ids",
        "candidate_line_ids.product_id",
        "candidate_line_ids.pack_unit_qty",
        "candidate_line_ids.packs_needed",
        "candidate_line_ids.available_qty",
        "candidate_line_ids.batch_summary",
    )
    def _compute_selected_candidate_details(self):
        for wizard in self:
            candidate = wizard.candidate_line_ids.filtered(
                lambda line: line.product_id == wizard.selected_pack_product_id
            )[:1]
            wizard.selected_pack_unit_qty = candidate.pack_unit_qty or 0.0
            wizard.selected_packs_needed = candidate.packs_needed or 0
            wizard.selected_available_qty = candidate.available_qty or 0.0
            wizard.selected_batch_summary = candidate.batch_summary or False

    @api.depends("candidate_line_ids.product_id")
    def _compute_candidate_product_ids(self):
        for wizard in self:
            wizard.candidate_product_ids = wizard.candidate_line_ids.mapped("product_id")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        line_id = values.get("sale_order_line_id") or self.env.context.get("default_sale_order_line_id") or self.env.context.get("active_id")
        if not line_id:
            return values
        line = self.env["sale.order.line"].browse(line_id)
        line._initialize_prescribed_metadata(force_missing_only=True)
        values.update(
            {
                "sale_order_line_id": line.id,
                "prescribed_product_id": line.prescribed_product_id.id,
                "prescribed_qty_base_units": line.prescribed_qty_base_units,
                "prescribed_uom_id": line.prescribed_uom_id.id,
                "base_product_id": line.base_product_id.id,
                "candidate_line_ids": [
                    Command.create(candidate)
                    for candidate in line.get_pack_substitution_candidates()
                ],
            }
        )
        return values

    def action_apply_selected(self):
        self.ensure_one()
        candidate = self.candidate_line_ids.filtered(
            lambda line: line.product_id == self.selected_pack_product_id
        )[:1]
        if not candidate:
            raise UserError(_("Select a pack option first."))
        self.sale_order_line_id.action_apply_pack_substitution(
            candidate.product_id,
            candidate.packs_needed,
        )
        return {"type": "ir.actions.act_window_close"}


class DispensingPackSelectionWizardLine(models.TransientModel):
    _name = "dispensing.pack.selection.wizard.line"
    _description = "Dispensing Pack Selection Wizard Line"
    _rec_name = "display_name"

    wizard_id = fields.Many2one(
        "dispensing.pack.selection.wizard",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Pack Product",
        required=True,
        readonly=True,
    )
    pack_unit_qty = fields.Float(
        string="Pack Size",
        digits=(16, 4),
        readonly=True,
    )
    packs_needed = fields.Integer(
        string="Packs Needed",
        readonly=True,
    )
    covered_qty = fields.Float(
        string="Covered Qty",
        digits=(16, 4),
        readonly=True,
    )
    available_qty = fields.Float(
        string="Available Qty",
        digits=(16, 4),
        readonly=True,
    )
    batch_summary = fields.Text(
        string="Batches",
        readonly=True,
    )
    exact_fit = fields.Boolean(
        string="Exact Fit",
        readonly=True,
    )
    substitution_priority = fields.Integer(
        string="Priority",
        readonly=True,
    )
    display_name = fields.Char(
        string="Display Name",
        compute="_compute_display_name",
    )

    @api.depends("product_id", "packs_needed", "pack_unit_qty")
    def _compute_display_name(self):
        for line in self:
            if line.product_id:
                line.display_name = _(
                    "%(product)s | %(packs)s pack(s) of %(size)s",
                    product=line.product_id.display_name,
                    packs=line.packs_needed,
                    size=line.pack_unit_qty,
                )
            else:
                line.display_name = False
