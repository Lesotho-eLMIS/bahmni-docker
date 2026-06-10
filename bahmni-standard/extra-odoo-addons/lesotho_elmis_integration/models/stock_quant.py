from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression
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

    @api.model
    def get_elmis_inventory_dashboard(self, options=None):
        if not self.env.user.has_group("stock.group_stock_user"):
            raise UserError(_("You need Inventory access to view the inventory dashboard."))

        options = options or {}
        locations, location, rows = self._get_dashboard_rows(options)
        summary = self._get_dashboard_summary(rows)
        rows = self._filter_dashboard_rows(rows, options.get("status"))
        rows = self._sort_dashboard_rows(rows, options.get("sort"))

        page_size = min(max(int(options.get("page_size") or 25), 10), 100)
        page = max(int(options.get("page") or 1), 1)
        total_rows = len(rows)
        max_page = max((total_rows + page_size - 1) // page_size, 1)
        page = min(page, max_page)
        offset = (page - 1) * page_size
        page_rows = rows[offset : offset + page_size]

        return {
            "locations": [
                {
                    "id": candidate.id,
                    "name": candidate.complete_name,
                    "facility_code": candidate.elmis_facility_code,
                }
                for candidate in locations
            ],
            "selected_location": {
                "id": location.id,
                "name": location.complete_name,
                "facility_code": location.elmis_facility_code,
            },
            "programs": self._get_dashboard_program_options(),
            "summary": summary,
            "products": page_rows,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total_rows,
                "pages": max_page,
            },
            "sync": self.env["elmis.inventory.sync"].get_sync_status(),
            "delivered_today": self._get_dashboard_delivered_today(),
            "actions": {
                "detail": "lesotho_elmis_integration.action_location_inventory",
                "outbox": "lesotho_elmis_integration.action_elmis_outbox",
                "dlq": "lesotho_elmis_integration.action_elmis_outbox_dlq",
                "sync": "lesotho_elmis_integration.action_elmis_inventory_sync_wizard",
                "history": "lesotho_elmis_integration.action_elmis_inventory_sync_run",
                "adjustments": (
                    "lesotho_elmis_integration.action_elmis_inventory_adjustments"
                ),
            },
        }

    @api.model
    def get_elmis_inventory_export_data(self, options=None):
        if not self.env.user.has_group("stock.group_stock_user"):
            raise UserError(_("You need Inventory access to export inventory."))

        options = options or {}
        _locations, location, rows = self._get_dashboard_rows(options)
        rows = self._filter_dashboard_rows(rows, options.get("status"))
        rows = self._sort_dashboard_rows(rows, options.get("sort"))
        export_rows = []
        for product in rows:
            lots = product["lots"] or [False]
            for lot in lots:
                stock_breakdown = self._get_dashboard_pack_breakdown(
                    product["quantity"],
                    product["pack_size"],
                )
                reserved_breakdown = self._get_dashboard_pack_breakdown(
                    product["reserved"],
                    product["pack_size"],
                )
                available_breakdown = self._get_dashboard_pack_breakdown(
                    product["available"],
                    product["pack_size"],
                )
                batch_stock_breakdown = self._get_dashboard_pack_breakdown(
                    lot["quantity"] if lot else 0.0,
                    product["pack_size"],
                )
                batch_reserved_breakdown = self._get_dashboard_pack_breakdown(
                    lot["reserved"] if lot else 0.0,
                    product["pack_size"],
                )
                batch_available_breakdown = self._get_dashboard_pack_breakdown(
                    lot["available"] if lot else 0.0,
                    product["pack_size"],
                )
                export_rows.append(
                    {
                        "location": location.complete_name,
                        "facility_code": location.elmis_facility_code or "",
                        "product_code": product["code"],
                        "product": product["name"],
                        "generic_name": product["generic_name"],
                        "dosage_form": product["dosage_form"],
                        "programs": ", ".join(product["programs"]),
                        "stock_status": self._get_dashboard_stock_status_label(
                            product["stock_status"]
                        ),
                        "stock_on_hand": product["quantity"],
                        "reserved": product["reserved"],
                        "available": product["available"],
                        "reorder_minimum": product["reorder_minimum"],
                        "reorder_maximum": product["reorder_maximum"],
                        "unit": product["uom"],
                        "pack_size": product["pack_size"] or "",
                        "pack_size_unit": product["pack_size_unit"],
                        "stock_on_hand_complete_packs": stock_breakdown[
                            "complete_packs"
                        ],
                        "stock_on_hand_loose_units": stock_breakdown["loose_units"],
                        "reserved_complete_packs": reserved_breakdown["complete_packs"],
                        "reserved_loose_units": reserved_breakdown["loose_units"],
                        "available_complete_packs": available_breakdown[
                            "complete_packs"
                        ],
                        "available_loose_units": available_breakdown["loose_units"],
                        "batch": lot["name"] if lot else "",
                        "batch_stock_on_hand": lot["quantity"] if lot else 0.0,
                        "batch_reserved": lot["reserved"] if lot else 0.0,
                        "batch_available": lot["available"] if lot else 0.0,
                        "batch_stock_on_hand_complete_packs": batch_stock_breakdown[
                            "complete_packs"
                        ],
                        "batch_stock_on_hand_loose_units": batch_stock_breakdown[
                            "loose_units"
                        ],
                        "batch_reserved_complete_packs": batch_reserved_breakdown[
                            "complete_packs"
                        ],
                        "batch_reserved_loose_units": batch_reserved_breakdown[
                            "loose_units"
                        ],
                        "batch_available_complete_packs": batch_available_breakdown[
                            "complete_packs"
                        ],
                        "batch_available_loose_units": batch_available_breakdown[
                            "loose_units"
                        ],
                        "expiry_date": lot["expiry"] if lot else "",
                        "days_to_expiry": lot["days_to_expiry"]
                        if lot and lot["days_to_expiry"] is not False
                        else "",
                        "expiry_status": self._get_dashboard_expiry_status_label(
                            lot["expiry_status"] if lot else "not_tracked"
                        ),
                        "expiry_bucket": self._get_dashboard_expiry_bucket_label(
                            lot["expiry_bucket"] if lot else "not_tracked"
                        ),
                    }
                )
        return {
            "location": location.complete_name,
            "facility_code": location.elmis_facility_code or "",
            "rows": export_rows,
        }

    def action_open_elmis_batch_stock_card(self):
        self.ensure_one()
        if not self.lot_id:
            raise UserError(_("A batch is required to open a stock card."))
        return {
            "type": "ir.actions.client",
            "name": _("Batch Stock Card"),
            "tag": "lesotho_elmis_integration.stock_card",
            "target": "current",
            "context": {
                "product_id": self.product_id.id,
                "lot_id": self.lot_id.id,
                "location_id": self.location_id.id,
            },
        }

    @api.model
    def get_elmis_stock_card(self, options=None):
        if not self.env.user.has_group("stock.group_stock_user"):
            raise UserError(_("You need Inventory access to view stock cards."))

        options = options or {}
        product, lot, location = self._get_stock_card_records(options)
        location_ids = self.env["stock.location"].search(
            [("id", "child_of", location.id)]
        ).ids
        move_lines = self.env["stock.move.line"].search(
            [
                ("state", "=", "done"),
                ("product_id", "=", product.id),
                ("lot_id", "=", lot.id),
                "|",
                ("location_id", "in", location_ids),
                ("location_dest_id", "in", location_ids),
            ],
            order="date asc, id asc",
        )
        events_by_line, events_by_move, events_by_sale, events_by_scrap, scraps_by_move = (
            self._get_stock_card_source_records(move_lines)
        )
        rows = []
        current_balance = self._get_stock_card_current_balance(
            product, lot, location
        )
        for line in move_lines:
            source_inside = line.location_id.id in location_ids
            destination_inside = line.location_dest_id.id in location_ids
            if source_inside == destination_inside:
                continue

            side = "source" if source_inside else "destination"
            quantity = line.product_uom_id._compute_quantity(
                line.qty_done,
                product.uom_id,
            )
            signed_quantity = -quantity if source_inside else quantity
            scrap = scraps_by_move.get(line.move_id.id)
            event = self._get_stock_card_event(
                line,
                side,
                scrap,
                events_by_line,
                events_by_move,
                events_by_sale,
                events_by_scrap,
            )
            movement_type = self._get_stock_card_movement_type(
                line,
                scrap,
                event,
            )
            rows.append(
                {
                    "id": line.id,
                    "date": fields.Datetime.to_string(line.date),
                    "movement_type": movement_type,
                    "movement_label": self._get_stock_card_movement_label(
                        movement_type
                    ),
                    "direction": "out" if source_inside else "in",
                    "quantity_in": quantity if destination_inside else 0.0,
                    "quantity_out": quantity if source_inside else 0.0,
                    "signed_quantity": signed_quantity,
                    "reference": self._get_stock_card_reference(
                        line, scrap
                    ),
                    "reason": self._get_stock_card_reason(
                        line,
                        side,
                        movement_type,
                        event,
                    ),
                    "counterparty_location": (
                        line.location_dest_id.complete_name
                        if source_inside
                        else line.location_id.complete_name
                    ),
                    "performed_by": line.write_uid.name,
                    "elmis_status": event.status.lower() if event else "not_required",
                    "elmis_status_label": (
                        dict(event._fields["status"].selection).get(
                            event.status, event.status
                        )
                        if event
                        else _("Not Required")
                    ),
                    "message_id": event.message_id if event else "",
                    "source": self._get_stock_card_source(line, scrap),
                }
            )

        running_balance = current_balance
        for row in reversed(rows):
            row["balance_after"] = running_balance
            row["balance_before"] = running_balance - row["signed_quantity"]
            running_balance = row["balance_before"]

        filtered_rows = self._filter_stock_card_rows(rows, options)
        opening_balance = (
            filtered_rows[0]["balance_before"]
            if filtered_rows
            else current_balance
        )
        return {
            "product": {
                "id": product.id,
                "name": product.name,
                "code": product.elmis_product_code
                or product.default_code
                or "",
                "uom": product.uom_id.name,
                "pack_size": product.elmis_pack_size or 0,
                "pack_size_unit": product.elmis_pack_size_unit
                or product.uom_id.name,
            },
            "lot": {
                "id": lot.id,
                "name": lot.name,
                "expiry": self._dashboard_date_to_iso(lot.expiration_date),
                "elmis_lot_id": lot.elmis_lot_id or "",
            },
            "location": {
                "id": location.id,
                "name": location.complete_name,
                "facility_code": location.elmis_facility_code or "",
            },
            "summary": {
                "opening_balance": opening_balance,
                "current_balance": current_balance,
                "quantity_in": sum(
                    row["quantity_in"] for row in filtered_rows
                ),
                "quantity_out": sum(
                    row["quantity_out"] for row in filtered_rows
                ),
                "movement_count": len(filtered_rows),
            },
            "movement_types": [
                {"value": key, "label": label}
                for key, label in self._get_stock_card_movement_labels().items()
                if any(row["movement_type"] == key for row in rows)
            ],
            "rows": list(reversed(filtered_rows)),
        }

    @api.model
    def get_elmis_stock_card_export_data(self, options=None):
        card = self.get_elmis_stock_card(options)
        return {
            "filename": "stock_card_%s_%s_%s.csv"
            % (
                card["product"]["code"] or card["product"]["id"],
                card["lot"]["name"],
                card["location"]["facility_code"] or card["location"]["id"],
            ),
            "card": card,
        }

    @api.model
    def _get_stock_card_records(self, options):
        try:
            product_id = int(options.get("product_id"))
            lot_id = int(options.get("lot_id"))
            location_id = int(options.get("location_id"))
        except (TypeError, ValueError):
            raise UserError(
                _("Select a product, batch, and location to view a stock card.")
            )

        product = self.env["product.product"].browse(product_id).exists()
        lot = self.env["stock.lot"].browse(lot_id).exists()
        location = self.env["stock.location"].browse(location_id).exists()
        if not product or not lot or not location:
            raise UserError(
                _("The selected product, batch, or location no longer exists.")
            )
        if lot.product_id != product:
            raise UserError(_("The selected batch does not belong to this product."))
        if not product.is_elmis_product:
            raise UserError(_("Stock cards are available only for eLMIS products."))
        return product, lot, location

    @api.model
    def _get_stock_card_current_balance(self, product, lot, location):
        groups = self.read_group(
            [
                ("product_id", "=", product.id),
                ("lot_id", "=", lot.id),
                ("location_id", "child_of", location.id),
            ],
            ["quantity:sum"],
            [],
        )
        return groups[0].get("quantity", 0.0) if groups else 0.0

    @api.model
    def _get_stock_card_source_records(self, move_lines):
        line_ids = move_lines.ids
        move_ids = move_lines.mapped("move_id").ids
        sale_ids = move_lines.mapped("move_id.sale_line_id").ids
        scraps = self.env["stock.scrap"].search([("move_id", "in", move_ids)])
        conditions = []
        if line_ids:
            conditions.append([("source_stock_move_line_id", "in", line_ids)])
        if move_ids:
            conditions.append([("source_stock_move_id", "in", move_ids)])
        if sale_ids:
            conditions.append([("source_sale_order_line_id", "in", sale_ids)])
        if scraps:
            conditions.append([("source_stock_scrap_id", "in", scraps.ids)])
        events = (
            self.env["elmis.outbox"].sudo().search(expression.OR(conditions))
            if conditions
            else self.env["elmis.outbox"]
        )

        events_by_line = {}
        events_by_move = {}
        events_by_sale = {}
        events_by_scrap = {}
        for event in events:
            if event.source_stock_move_line_id:
                events_by_line[
                    (
                        event.source_stock_move_line_id.id,
                        event.stock_move_line_side,
                    )
                ] = event
            if event.source_stock_move_id:
                events_by_move[event.source_stock_move_id.id] = event
            if event.source_sale_order_line_id:
                events_by_sale[event.source_sale_order_line_id.id] = event
            if event.source_stock_scrap_id:
                events_by_scrap[
                    (
                        event.source_stock_scrap_id.id,
                        event.stock_scrap_side,
                    )
                ] = event
        return (
            events_by_line,
            events_by_move,
            events_by_sale,
            events_by_scrap,
            {scrap.move_id.id: scrap for scrap in scraps},
        )

    @api.model
    def _get_stock_card_event(
        self,
        line,
        side,
        scrap,
        events_by_line,
        events_by_move,
        events_by_sale,
        events_by_scrap,
    ):
        event = events_by_scrap.get((scrap.id, side)) if scrap else False
        if not event:
            event = events_by_line.get((line.id, side))
        if not event:
            event = events_by_move.get(line.move_id.id)
        if not event and line.move_id.sale_line_id:
            event = events_by_sale.get(line.move_id.sale_line_id.id)
        return event

    @api.model
    def _get_stock_card_movement_type(self, line, scrap, event):
        if line.move_id.sale_line_id:
            return "dispense"
        if scrap or line.move_id.scrapped:
            return "unserviceable"
        if event and event.source_stock_move_id:
            return "inventory_adjustment"
        if line.picking_id and line.picking_id.picking_type_id.code == "internal":
            return "internal_transfer"
        if (
            line.location_id.usage == "supplier"
            and line.location_dest_id.usage == "internal"
        ):
            return "receipt"
        if (
            line.location_id.usage == "internal"
            and line.location_dest_id.usage == "customer"
        ):
            return "issue"
        if line.location_dest_id.usage == "internal":
            return "stock_in"
        return "stock_out"

    @api.model
    def _get_stock_card_movement_labels(self):
        return {
            "dispense": _("Dispensed"),
            "internal_transfer": _("Internal Transfer"),
            "unserviceable": _("Moved to Unserviceable"),
            "inventory_adjustment": _("Inventory Adjustment"),
            "receipt": _("Receipt"),
            "issue": _("Issue"),
            "stock_in": _("Stock In"),
            "stock_out": _("Stock Out"),
        }

    @api.model
    def _get_stock_card_movement_label(self, movement_type):
        return self._get_stock_card_movement_labels().get(
            movement_type, movement_type
        )

    @api.model
    def _get_stock_card_reason(self, line, side, movement_type, event):
        if event and event.adjustment_reason:
            return event.adjustment_reason
        if movement_type == "internal_transfer":
            return (
                line.elmis_transfer_debit_reason
                if side == "source"
                else line.elmis_transfer_credit_reason
            )
        return {
            "dispense": _("Consumed"),
            "unserviceable": _("Moved to Unserviceable"),
            "inventory_adjustment": _("Inventory Count"),
            "receipt": _("Receipts"),
            "issue": _("Issued"),
            "stock_in": _("Stock In"),
            "stock_out": _("Stock Out"),
        }.get(movement_type, "")

    @api.model
    def _get_stock_card_reference(self, line, scrap):
        if line.move_id.sale_line_id:
            return line.move_id.sale_line_id.order_id.name
        if scrap:
            return scrap.name
        return line.reference or line.move_id.reference or line.move_id.name

    @api.model
    def _get_stock_card_source(self, line, scrap):
        if line.move_id.sale_line_id:
            order = line.move_id.sale_line_id.order_id
            return {
                "model": "sale.order",
                "id": order.id,
                "label": order.name,
            }
        if scrap:
            return {
                "model": "stock.scrap",
                "id": scrap.id,
                "label": scrap.name,
            }
        if line.picking_id:
            return {
                "model": "stock.picking",
                "id": line.picking_id.id,
                "label": line.picking_id.name,
            }
        return {
            "model": "stock.move.line",
            "id": line.id,
            "label": line.reference or line.move_id.name,
        }

    @api.model
    def _filter_stock_card_rows(self, rows, options):
        movement_type = options.get("movement_type")
        date_from = fields.Date.to_date(options.get("date_from"))
        date_to = fields.Date.to_date(options.get("date_to"))
        filtered = []
        for row in rows:
            movement_date = fields.Date.to_date(row["date"])
            if movement_type and movement_type != "all":
                if row["movement_type"] != movement_type:
                    continue
            if date_from and movement_date < date_from:
                continue
            if date_to and movement_date > date_to:
                continue
            filtered.append(row)
        return filtered

    @api.model
    def _get_dashboard_rows(self, options):
        locations = self.env["elmis.inventory.sync"]._get_configured_mirror_locations()
        location = self._get_dashboard_location(locations, options.get("location_id"))
        products = self._get_dashboard_products(options)
        inventory = self._get_dashboard_inventory(products, location)
        reorder_levels = self._get_dashboard_reorder_levels(products, location)
        rows = self._build_dashboard_rows(products, inventory, reorder_levels)
        return locations, location, rows

    @api.model
    def _get_dashboard_location(self, locations, location_id):
        if location_id:
            location = locations.filtered(lambda candidate: candidate.id == int(location_id))
            if not location:
                raise UserError(_("Select a configured eLMIS mirror location."))
            return location[:1]
        return locations[:1]

    @api.model
    def _get_dashboard_products(self, options):
        domain = [("is_elmis_product", "=", True), ("active", "=", True)]
        program_id = options.get("program_id")
        if program_id:
            domain.append(("elmis_program_ids", "in", int(program_id)))

        search_term = (options.get("search") or "").strip()
        if search_term:
            product_search = [
                "|",
                "|",
                "|",
                ("name", "ilike", search_term),
                ("default_code", "ilike", search_term),
                ("elmis_product_code", "ilike", search_term),
                ("elmis_generic_name", "ilike", search_term),
            ]
            lot_product_ids = self.env["stock.lot"].search(
                [("name", "ilike", search_term)]
            ).mapped("product_id").ids
            if lot_product_ids:
                product_search = expression.OR(
                    [product_search, [("id", "in", lot_product_ids)]]
                )
            domain = expression.AND([domain, product_search])
        return self.env["product.product"].search(domain)

    @api.model
    def _get_dashboard_inventory(self, products, location):
        inventory = defaultdict(
            lambda: {
                "quantity": 0.0,
                "reserved": 0.0,
                "lots": {},
            }
        )
        if not products:
            return inventory

        groups = self.read_group(
            [
                ("location_id", "child_of", location.id),
                ("product_id", "in", products.ids),
            ],
            ["product_id", "lot_id", "quantity:sum", "reserved_quantity:sum"],
            ["product_id", "lot_id"],
            lazy=False,
        )
        lot_ids = [group["lot_id"][0] for group in groups if group.get("lot_id")]
        lots = {lot.id: lot for lot in self.env["stock.lot"].browse(lot_ids)}
        for group in groups:
            product_id = group["product_id"][0]
            quantity = group.get("quantity", 0.0)
            reserved = group.get("reserved_quantity", 0.0)
            inventory[product_id]["quantity"] += quantity
            inventory[product_id]["reserved"] += reserved
            if not group.get("lot_id") or not quantity:
                continue
            lot_id = group["lot_id"][0]
            lot = lots.get(lot_id)
            inventory[product_id]["lots"][lot_id] = {
                "id": lot_id,
                "name": lot.name if lot else group["lot_id"][1],
                "quantity": quantity,
                "reserved": reserved,
                "available": quantity - reserved,
                "expiry": self._dashboard_date_to_iso(
                    lot.expiration_date if lot else False
                ),
                "expiry_status": self._get_dashboard_expiry_status(
                    lot.expiration_date if lot else False
                ),
                "days_to_expiry": self._get_dashboard_days_to_expiry(
                    lot.expiration_date if lot else False
                ),
                "expiry_bucket": self._get_dashboard_expiry_bucket(
                    lot.expiration_date if lot else False
                ),
            }
        return inventory

    @api.model
    def _get_dashboard_reorder_levels(self, products, location):
        levels = {}
        if not products:
            return levels
        orderpoints = self.env["stock.warehouse.orderpoint"].search(
            [
                ("active", "=", True),
                ("location_id", "=", location.id),
                ("product_id", "in", products.ids),
            ]
        )
        for orderpoint in orderpoints:
            current = levels.setdefault(
                orderpoint.product_id.id,
                {"minimum": 0.0, "maximum": 0.0},
            )
            current["minimum"] = max(current["minimum"], orderpoint.product_min_qty)
            current["maximum"] = max(current["maximum"], orderpoint.product_max_qty)
        return levels

    @api.model
    def _build_dashboard_rows(self, products, inventory, reorder_levels):
        dosage_labels = dict(
            self.env["product.product"]._fields["elmis_dosage_form"].selection
        )
        rows = []
        for product in products:
            values = inventory[product.id]
            quantity = values["quantity"]
            reserved = values["reserved"]
            available = quantity - reserved
            reorder = reorder_levels.get(product.id, {"minimum": 0.0, "maximum": 0.0})
            lots = sorted(
                values["lots"].values(),
                key=lambda lot: (not lot["expiry"], lot["expiry"] or "", lot["name"]),
            )
            expiry_status = self._get_dashboard_product_expiry_status(lots)
            stock_status = self._get_dashboard_stock_status(
                quantity,
                available,
                reorder["minimum"],
            )
            expiry_quantities = self._get_dashboard_expiry_quantities(lots)
            rows.append(
                {
                    "id": product.id,
                    "name": product.name,
                    "code": product.elmis_product_code or product.default_code or "",
                    "generic_name": product.elmis_generic_name or "",
                    "dosage_form": dosage_labels.get(product.elmis_dosage_form, "")
                    if product.elmis_dosage_form
                    else "",
                    "programs": product.elmis_program_ids.mapped("name"),
                    "quantity": quantity,
                    "reserved": reserved,
                    "available": available,
                    "uom": product.uom_id.name,
                    "pack_size": product.elmis_pack_size or 0,
                    "pack_size_unit": product.elmis_pack_size_unit
                    or product.uom_id.name,
                    "lot_count": len(lots),
                    "lots": lots,
                    "earliest_expiry": lots[0]["expiry"] if lots else False,
                    "expiry_status": expiry_status,
                    "stock_status": stock_status,
                    "reorder_minimum": reorder["minimum"],
                    "reorder_maximum": reorder["maximum"],
                    "expiry_quantities": expiry_quantities,
                }
            )
        return rows

    @api.model
    def _get_dashboard_pack_breakdown(self, quantity, pack_size):
        if not pack_size or pack_size <= 0 or quantity < 0:
            return {
                "complete_packs": "",
                "loose_units": quantity,
            }

        complete_packs = int(quantity // pack_size)
        loose_units = quantity - (complete_packs * pack_size)
        return {
            "complete_packs": complete_packs,
            "loose_units": loose_units,
        }

    @api.model
    def _get_dashboard_stock_status(self, quantity, available, minimum):
        if float_compare(quantity, 0, precision_digits=4) <= 0:
            return "out"
        if float_compare(available, 0, precision_digits=4) <= 0:
            return "reserved"
        if minimum and float_compare(available, minimum, precision_digits=4) <= 0:
            return "low"
        return "available"

    @api.model
    def _get_dashboard_product_expiry_status(self, lots):
        statuses = {lot["expiry_status"] for lot in lots}
        if "expired" in statuses:
            return "expired"
        if "expiring_soon" in statuses:
            return "expiring_soon"
        if lots:
            return "valid"
        return "not_tracked"

    @api.model
    def _filter_dashboard_rows(self, rows, status):
        if not status or status == "all":
            return rows
        if status in {"available", "low", "out", "reserved"}:
            return [row for row in rows if row["stock_status"] == status]
        if status in {"expired", "expiring_soon"}:
            return [row for row in rows if row["expiry_status"] == status]
        expiry_filters = {
            "expiring_0_30": "days_0_30",
            "expiring_31_60": "days_31_60",
            "expiring_61_90": "days_61_90",
        }
        if status in expiry_filters:
            bucket = expiry_filters[status]
            return [
                row
                for row in rows
                if row["expiry_quantities"].get(bucket, 0.0) > 0
            ]
        return rows

    @api.model
    def _sort_dashboard_rows(self, rows, sort):
        if sort == "quantity_desc":
            return sorted(rows, key=lambda row: (-row["quantity"], row["name"]))
        if sort == "expiry":
            return sorted(
                rows,
                key=lambda row: (
                    not row["earliest_expiry"],
                    row["earliest_expiry"] or "",
                    row["name"],
                ),
            )
        return sorted(rows, key=lambda row: row["name"])

    @api.model
    def _get_dashboard_summary(self, rows):
        return {
            "total": len(rows),
            "in_stock": sum(row["quantity"] > 0 for row in rows),
            "available": sum(row["stock_status"] == "available" for row in rows),
            "low": sum(row["stock_status"] == "low" for row in rows),
            "out": sum(row["stock_status"] == "out" for row in rows),
            "reserved": sum(row["stock_status"] == "reserved" for row in rows),
            "expiring_soon": sum(
                row["expiry_status"] == "expiring_soon" for row in rows
            ),
            "expired": sum(row["expiry_status"] == "expired" for row in rows),
            "expired_quantity": sum(
                row["expiry_quantities"]["expired"] for row in rows
            ),
            "expiring_0_30_quantity": sum(
                row["expiry_quantities"]["days_0_30"] for row in rows
            ),
            "expiring_31_60_quantity": sum(
                row["expiry_quantities"]["days_31_60"] for row in rows
            ),
            "expiring_61_90_quantity": sum(
                row["expiry_quantities"]["days_61_90"] for row in rows
            ),
        }

    @api.model
    def _get_dashboard_expiry_quantities(self, lots):
        quantities = {
            "expired": 0.0,
            "days_0_30": 0.0,
            "days_31_60": 0.0,
            "days_61_90": 0.0,
        }
        for lot in lots:
            bucket = lot["expiry_bucket"]
            if bucket in quantities:
                quantities[bucket] += max(lot["quantity"], 0.0)
        return quantities

    @api.model
    def _get_dashboard_program_options(self):
        return [
            {"id": program.id, "name": program.name, "code": program.code}
            for program in self.env["elmis.program"].search(
                [("active", "=", True)], order="name"
            )
        ]

    @api.model
    def _get_dashboard_delivered_today(self):
        start = fields.Datetime.to_datetime(fields.Date.context_today(self))
        return self.env["elmis.outbox"].sudo().search_count(
            [("status", "=", "DELIVERED"), ("delivered_at", ">=", start)]
        )

    @api.model
    def _get_dashboard_expiry_status(self, expiration_date):
        days = self._get_dashboard_days_to_expiry(expiration_date)
        if days is False:
            return "not_tracked"
        if days < 0:
            return "expired"
        if days <= self.ELMIS_EXPIRING_SOON_DAYS:
            return "expiring_soon"
        return "valid"

    @api.model
    def _get_dashboard_expiry_bucket(self, expiration_date):
        days = self._get_dashboard_days_to_expiry(expiration_date)
        if days is False:
            return "not_tracked"
        if days < 0:
            return "expired"
        if days <= 30:
            return "days_0_30"
        if days <= 60:
            return "days_31_60"
        if days <= self.ELMIS_EXPIRING_SOON_DAYS:
            return "days_61_90"
        return "later"

    @api.model
    def _get_dashboard_stock_status_label(self, status):
        return {
            "available": _("Available"),
            "low": _("Low Stock"),
            "out": _("Out of Stock"),
            "reserved": _("Fully Reserved"),
        }.get(status, status)

    @api.model
    def _get_dashboard_expiry_status_label(self, status):
        return {
            "valid": _("Valid"),
            "expiring_soon": _("Expiring Soon"),
            "expired": _("Expired"),
            "not_tracked": _("No Expiry"),
        }.get(status, status)

    @api.model
    def _get_dashboard_expiry_bucket_label(self, bucket):
        return {
            "expired": _("Expired"),
            "days_0_30": _("0-30 Days"),
            "days_31_60": _("31-60 Days"),
            "days_61_90": _("61-90 Days"),
            "later": _("More Than 90 Days"),
            "not_tracked": _("No Expiry"),
        }.get(bucket, bucket)

    @api.model
    def _get_dashboard_days_to_expiry(self, expiration_date):
        if not expiration_date:
            return False
        expiry = fields.Date.to_date(expiration_date)
        return (expiry - fields.Date.context_today(self)).days

    @api.model
    def _dashboard_date_to_iso(self, value):
        return fields.Date.to_string(fields.Date.to_date(value)) if value else False

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
