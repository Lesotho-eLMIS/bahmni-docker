from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare


ELMIS_INVENTORY_DEBIT_REASON_SELECTION = [
    ("Beginning Balance Insufficiency", "Beginning Balance Insufficiency"),
    ("Lost", "Lost"),
    ("Stolen", "Stolen"),
    ("Expiry", "Expiry"),
    ("Damaged", "Damaged"),
    ("POD Query", "POD Query"),
]

ELMIS_INVENTORY_CREDIT_REASON_SELECTION = [
    ("Beginning Balance Excess", "Beginning Balance Excess"),
    ("Receipts", "Receipts"),
    ("Transfer In", "Transfer In"),
    ("Facility Return", "Facility Return"),
]

ELMIS_INVENTORY_REASON_SELECTION = (
    ELMIS_INVENTORY_DEBIT_REASON_SELECTION + ELMIS_INVENTORY_CREDIT_REASON_SELECTION
)
ELMIS_INVENTORY_DEBIT_REASONS = {reason for reason, _label in ELMIS_INVENTORY_DEBIT_REASON_SELECTION}
ELMIS_INVENTORY_CREDIT_REASONS = {reason for reason, _label in ELMIS_INVENTORY_CREDIT_REASON_SELECTION}


class StockQuant(models.Model):
    _inherit = "stock.quant"

    ELMIS_EXPIRING_SOON_DAYS = 90

    elmis_program_ids = fields.Many2many(
        "elmis.program",
        compute="_compute_elmis_program_ids",
        search="_search_elmis_program_ids",
        string="eLMIS Programs",
        readonly=True,
    )
    elmis_product_code = fields.Char(
        string="Product Code",
        related="product_id.default_code",
        readonly=True,
    )
    elmis_location_facility_code = fields.Char(
        string="eLMIS Facility Code",
        related="location_id.elmis_facility_code",
        readonly=True,
    )
    elmis_lot_expiration_date = fields.Datetime(
        string="Expiry Date",
        related="lot_id.expiration_date",
        readonly=True,
    )
    elmis_stock_status = fields.Selection(
        [
            ("available", "Available"),
            ("reserved", "Reserved"),
            ("out", "Out of Stock"),
        ],
        compute="_compute_elmis_inventory_display_status",
        string="Stock Status",
        readonly=True,
    )
    elmis_expiry_status = fields.Selection(
        [
            ("valid", "Valid"),
            ("expiring_soon", "Expiring Soon"),
            ("expired", "Expired"),
            ("not_tracked", "No Expiry"),
        ],
        compute="_compute_elmis_inventory_display_status",
        string="Expiry Status",
        readonly=True,
    )
    elmis_days_to_expiry = fields.Integer(
        compute="_compute_elmis_inventory_display_status",
        string="Days to Expiry",
        readonly=True,
    )
    elmis_inventory_program_id = fields.Many2one(
        "elmis.program",
        string="eLMIS Adjustment Program",
        domain=[("active", "=", True)],
        index=True,
        copy=False,
        help="eLMIS program under which this inventory adjustment should be reported.",
    )
    elmis_inventory_adjustment_reason = fields.Selection(
        ELMIS_INVENTORY_REASON_SELECTION,
        string="eLMIS Adjustment Reason",
        copy=False,
        help="Reason to submit to eLMIS when this counted quantity changes eLMIS stock.",
    )

    @api.depends("product_id.elmis_program_ids")
    def _compute_elmis_program_ids(self):
        for quant in self:
            quant.elmis_program_ids = quant.product_id.elmis_program_ids

    def _search_elmis_program_ids(self, operator, value):
        return [("product_id.elmis_program_ids", operator, value)]

    @api.depends("quantity", "reserved_quantity", "available_quantity", "lot_id.expiration_date")
    def _compute_elmis_inventory_display_status(self):
        today = fields.Date.context_today(self)
        for quant in self:
            if float_compare(
                quant.quantity,
                0,
                precision_rounding=quant.product_uom_id.rounding,
            ) <= 0:
                quant.elmis_stock_status = "out"
            elif float_compare(
                quant.available_quantity,
                0,
                precision_rounding=quant.product_uom_id.rounding,
            ) <= 0:
                quant.elmis_stock_status = "reserved"
            else:
                quant.elmis_stock_status = "available"

            if not quant.lot_id.expiration_date:
                quant.elmis_expiry_status = "not_tracked"
                quant.elmis_days_to_expiry = 0
                continue

            expiry_date = fields.Date.to_date(quant.lot_id.expiration_date)
            days_to_expiry = (expiry_date - today).days
            quant.elmis_days_to_expiry = days_to_expiry
            if days_to_expiry < 0:
                quant.elmis_expiry_status = "expired"
            elif days_to_expiry <= self.ELMIS_EXPIRING_SOON_DAYS:
                quant.elmis_expiry_status = "expiring_soon"
            else:
                quant.elmis_expiry_status = "valid"

    @api.onchange("product_id")
    def _onchange_elmis_inventory_product_id(self):
        for quant in self:
            if not quant.product_id.is_elmis_product:
                quant.elmis_inventory_program_id = False
                quant.elmis_inventory_adjustment_reason = False
                continue
            if not quant.elmis_inventory_program_id and len(quant.product_id.elmis_program_ids) == 1:
                quant.elmis_inventory_program_id = quant.product_id.elmis_program_ids[0]

    @api.constrains("product_id", "lot_id")
    def _check_elmis_inventory_lot_matches_product(self):
        for quant in self:
            if (
                quant.product_id.is_elmis_product
                and quant.lot_id
                and quant.lot_id.product_id != quant.product_id
            ):
                raise ValidationError(_("The selected eLMIS lot must belong to the eLMIS product."))

    @api.constrains("product_id", "elmis_inventory_program_id")
    def _check_elmis_inventory_program_matches_product(self):
        for quant in self:
            if not quant.product_id.is_elmis_product or not quant.elmis_inventory_program_id:
                continue
            product_programs = quant.product_id.elmis_program_ids
            if product_programs and quant.elmis_inventory_program_id not in product_programs:
                raise ValidationError(
                    _("The selected eLMIS adjustment program must be configured on the eLMIS product.")
                )

    def _apply_inventory(self):
        move_vals = []
        adjustment_quants = []
        if not self.user_has_groups("stock.group_stock_manager"):
            raise UserError(_("Only a stock manager can validate an inventory adjustment."))

        for quant in self:
            quant._check_elmis_inventory_adjustment_ready()
            diff = quant.inventory_diff_quantity
            if float_compare(diff, 0, precision_rounding=quant.product_uom_id.rounding) > 0:
                move_vals.append(
                    quant._get_inventory_move_values(
                        diff,
                        quant.product_id.with_company(quant.company_id).property_stock_inventory,
                        quant.location_id,
                    )
                )
            else:
                move_vals.append(
                    quant._get_inventory_move_values(
                        -diff,
                        quant.location_id,
                        quant.product_id.with_company(quant.company_id).property_stock_inventory,
                        out=True,
                    )
                )
            adjustment_quants.append(quant)

        moves = self.env["stock.move"].with_context(inventory_mode=False).create(move_vals)
        moves._action_done()
        self._create_elmis_inventory_adjustment_outbox(adjustment_quants, moves)

        self.location_id.write({"last_inventory_date": fields.Date.today()})
        date_by_location = {loc: loc._get_next_inventory_date() for loc in self.mapped("location_id")}
        for quant in self:
            quant.inventory_date = date_by_location[quant.location_id]
        self.write({"inventory_quantity": 0, "user_id": False})
        self.write({"inventory_diff_quantity": 0})

    def _check_elmis_inventory_adjustment_ready(self):
        self.ensure_one()
        if not self.product_id.is_elmis_product:
            return

        diff_sign = float_compare(
            self.inventory_diff_quantity,
            0,
            precision_rounding=self.product_uom_id.rounding,
        )
        if diff_sign == 0:
            return
        if not self.elmis_inventory_program_id:
            raise UserError(_("Select an eLMIS program before applying this inventory adjustment."))
        if not self.elmis_inventory_adjustment_reason:
            raise UserError(_("Select an eLMIS adjustment reason before applying this inventory adjustment."))
        if diff_sign > 0 and self.elmis_inventory_adjustment_reason not in ELMIS_INVENTORY_CREDIT_REASONS:
            raise UserError(_("Select an eLMIS credit reason for a positive inventory adjustment."))
        if diff_sign < 0 and self.elmis_inventory_adjustment_reason not in ELMIS_INVENTORY_DEBIT_REASONS:
            raise UserError(_("Select an eLMIS debit reason for a negative inventory adjustment."))

    def _create_elmis_inventory_adjustment_outbox(self, quants, moves):
        Outbox = self.env["elmis.outbox"].sudo()
        for quant, move in zip(quants, moves):
            if not quant.product_id.is_elmis_product:
                continue
            diff = quant.inventory_diff_quantity
            if float_compare(diff, 0, precision_rounding=quant.product_uom_id.rounding) == 0:
                continue
            if Outbox.search_count([("source_stock_move_id", "=", move.id)]):
                continue
            Outbox.create(quant._prepare_elmis_inventory_adjustment_outbox_vals(move))

    def _prepare_elmis_inventory_adjustment_outbox_vals(self, move):
        self.ensure_one()
        self._check_elmis_inventory_adjustment_ready()
        quantity = abs(self.inventory_diff_quantity)
        return {
            "transaction_type": "ADJUSTMENT",
            "facility_code": self._get_elmis_inventory_facility_code(),
            "program_id": self.elmis_inventory_program_id.id,
            "elmis_orderable_id": self.product_id.id,
            "lot_id": self.lot_id.id,
            "quantity": quantity,
            "uom_id": self.product_uom_id.id,
            "transaction_date": fields.Datetime.now(),
            "prescription_ref": move.reference or move.name,
            "adjustment_reason": self.elmis_inventory_adjustment_reason,
            "source_stock_move_id": move.id,
        }

    def _get_elmis_inventory_facility_code(self):
        self.ensure_one()
        if self.location_id.elmis_facility_code:
            return self.location_id.elmis_facility_code
        return self.env["elmis.inventory.sync"]._get_configured_mirror_location().elmis_facility_code
