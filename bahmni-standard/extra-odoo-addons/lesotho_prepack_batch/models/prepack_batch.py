import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)


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


class BahmniPrepackBatch(models.Model):
    _name = "bahmni.prepack.batch"
    _description = "Bahmni Prepack Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "planned_date desc, id desc"

    name = fields.Char(
        string="Batch Reference",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("New"),
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending_auth", "Pending Release"),
            ("rejected", "Rejected"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        required=True,
        copy=False,
    )
    planned_date = fields.Datetime(
        string="Planned Date",
        required=True,
        default=fields.Datetime.now,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    responsible_id = fields.Many2one(
        "res.users",
        string="Responsible",
        default=lambda self: self.env.user,
    )
    picking_type_id = fields.Many2one(
        "stock.picking.type",
        string="Manufacturing Operation Type",
        required=True,
        domain="[('code', '=', 'mrp_operation')]",
        default=lambda self: self._default_picking_type_id(),
    )
    location_src_id = fields.Many2one(
        "stock.location",
        string="Components Location",
        required=True,
        default=lambda self: self._default_location_src_id(),
    )
    location_dest_id = fields.Many2one(
        "stock.location",
        string="Finished Goods Location",
        required=True,
        default=lambda self: self._default_location_dest_id(),
    )
    line_ids = fields.One2many(
        "bahmni.prepack.batch.line",
        "batch_id",
        string="Lines",
        copy=True,
    )
    note = fields.Text()
    line_count = fields.Integer(compute="_compute_counts")
    draft_data = fields.Text(
        string="Draft Data",
        help="JSON representation of the prepacking list for draft persistence.",
    )
    mrp_production_ids = fields.Many2many(
        "mrp.production",
        compute="_compute_mrp_production_ids",
        string="Manufacturing Orders",
    )
    mrp_production_count = fields.Integer(compute="_compute_counts")
    has_damaged_products = fields.Boolean(
        string="Has Damaged Products",
        tracking=True,
    )
    damage_scrap_ids = fields.Many2many(
        "stock.scrap",
        "bahmni_prepack_batch_stock_scrap_rel",
        "batch_id",
        "scrap_id",
        string="Damage Records",
        copy=False,
        readonly=True,
    )
    damage_move_ids = fields.Many2many(
        "stock.move",
        "bahmni_prepack_batch_stock_move_rel",
        "batch_id",
        "move_id",
        string="Damage Stock Moves",
        copy=False,
        readonly=True,
    )
    damage_scrap_count = fields.Integer(compute="_compute_counts")

    @api.model
    def _default_picking_type_id(self):
        picking_type = self.env["stock.picking.type"].search(
            [
                ("code", "=", "mrp_operation"),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.env.company.id),
            ],
            order="company_id desc, sequence, id",
            limit=1,
        )
        return picking_type.id

    @api.model
    def _default_location_src_id(self):
        picking_type_id = self._default_picking_type_id()
        if picking_type_id:
            return (
                self.env["stock.picking.type"]
                .browse(picking_type_id)
                .default_location_src_id.id
            )
        return False

    @api.model
    def _default_location_dest_id(self):
        picking_type_id = self._default_picking_type_id()
        if picking_type_id:
            return (
                self.env["stock.picking.type"]
                .browse(picking_type_id)
                .default_location_dest_id.id
            )
        return False

    @api.model
    def fetch_prepack_locations(self):
        """Fetch internal stock locations available for prepacking."""
        default_location_id = self._default_location_src_id()
        domain = [
            ("usage", "=", "internal"),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", self.env.company.id),
        ]
        if default_location_id:
            domain.append(("id", "child_of", default_location_id))
        locations = self.env["stock.location"].search(domain, order="complete_name, id")
        if default_location_id and default_location_id not in locations.ids:
            locations |= self.env["stock.location"].browse(default_location_id)
        return {
            "default_location_id": default_location_id,
            "locations": [
                {
                    "id": location.id,
                    "name": location.display_name,
                }
                for location in locations
            ],
        }

    @api.model
    def fetch_bulk_inventory(self, include_prepacks=False, location_src_id=False, location_id=False):
        """Fetch bulk products with stock on hand and lot details for the prepacking UI."""
        domain = [
            ("quantity", ">", 0),
            ("product_id.detailed_type", "=", "product"),
        ]
        if not include_prepacks:
            domain.append(("product_id.is_prepack", "=", False))

        location_src_id = int(location_src_id or location_id or 0) or self._default_location_src_id()
        if location_src_id:
            domain.append(("location_id", "child_of", location_src_id))

        # Find products that are NOT prepacks (bulk) and have stock
        quants = self.env["stock.quant"].search(domain)

        inventory_by_key = {}
        for quant in quants:
            key = (
                quant.product_id.id,
                quant.lot_id.id if quant.lot_id else False,
                quant.location_id.id,
            )
            if key in inventory_by_key:
                inventory_by_key[key]["soh"] += quant.quantity
                continue

            inventory_by_key[key] = {
                "key": f"{quant.product_id.id}_{quant.lot_id.id if quant.lot_id else 0}_{quant.location_id.id}",
                "id": quant.product_id.id,
                "name": quant.product_id.display_name,
                "batch": quant.lot_id.name if quant.lot_id else _("No Lot"),
                "lot_id": quant.lot_id.id if quant.lot_id else False,
                "location_id": quant.location_id.id,
                "location_name": quant.location_id.display_name,
                "exp": quant.lot_id.expiration_date.strftime("%Y-%m")
                if quant.lot_id and quant.lot_id.expiration_date
                else "N/A",
                "soh": quant.quantity,
                "uom": quant.product_id.uom_id.name,
                "reqLiquidCheck": any(
                    word in quant.product_id.uom_id.name.lower()
                    for word in [
                        "ml",
                        "l",
                        "gram",
                        " g",
                        "ointment",
                        "cream",
                        "syrup",
                    ]
                ),
            }
        inventory = list(inventory_by_key.values())
        # Sort by name
        inventory.sort(key=lambda x: x["name"])
        return inventory

    @api.model
    def fetch_packaging_materials(self, location_id=False):
        """Fetch products available for packaging material selection."""
        product_domain = [
            ("active", "=", True),
            ("detailed_type", "in", ["product", "consu"]),
            ("is_prepack", "=", False),
        ]
        if "company_id" in self.env["product.product"]._fields:
            product_domain.extend(
                [
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", self.env.company.id),
                ]
            )
        products = self.env["product.product"].search(product_domain, order="display_name, id")
        material_by_product = {
            product.id: {
                "id": product.id,
                "name": product.display_name,
                "uom": product.uom_id.name,
                "soh": 0,
            }
            for product in products
        }
        quant_domain = [
            ("quantity", ">", 0),
            ("product_id", "in", products.ids),
        ]
        location_src_id = int(location_id) if location_id else self._default_location_src_id()
        if location_src_id:
            quant_domain.append(("location_id", "child_of", location_src_id))
        quants = self.env["stock.quant"].search(quant_domain)
        for quant in quants:
            product = quant.product_id
            material_by_product[product.id]["soh"] += quant.quantity
        materials = list(material_by_product.values())
        materials.sort(key=lambda material: material["name"])
        return materials

    @api.model
    def fetch_draft_batch(self):
        """Fetch the current user's draft batch if one exists."""
        batch = self.search(
            [
                ("state", "in", ["draft", "rejected"]),
                ("responsible_id", "=", self.env.user.id),
            ],
            limit=1,
        )
        if batch and batch.draft_data:
            return {
                "id": batch.id,
                "name": batch.name,
                "location_id": batch.location_src_id.id,
                "location_src_id": batch.location_src_id.id,
                "location_src_name": batch.location_src_id.display_name,
                "items": json.loads(batch.draft_data),
            }
        return None

    @api.model
    def save_prepack_batch(self, payload, location_src_id=False, location_id=False):
        """Save the current prepack list as a draft batch for the user."""
        selected_location_id = int(location_src_id or location_id or 0)
        batch = self.search(
            [
                ("state", "in", ["draft", "rejected"]),
                ("responsible_id", "=", self.env.user.id),
            ],
            limit=1,
        )

        if not batch:
            values = {"state": "draft"}
            if selected_location_id:
                values["location_src_id"] = selected_location_id
            batch = self.create(values)
        elif selected_location_id:
            batch.location_src_id = selected_location_id

        batch.write({"draft_data": json.dumps(payload)})
        return {
            "id": batch.id,
            "name": batch.name,
            "location_src_id": batch.location_src_id.id,
            "location_src_name": batch.location_src_id.display_name,
            "location_dest_id": batch.location_dest_id.id,
            "location_dest_name": batch.location_dest_id.display_name,
        }

    @api.model
    def submit_prepack_batch(self, payload, location_src_id=False, location_id=False):
        """Submit the batch for release, generating MOs for all lines."""
        if not payload:
            raise UserError(_("No data submitted."))
        selected_location_id = int(location_src_id or location_id or 0)
        if not selected_location_id:
            raise UserError(_("Please select a prepacking location before submitting for release."))

        # Re-save to ensure we have the latest data before converting to lines
        res = self.save_prepack_batch(payload, location_src_id=selected_location_id)
        batch = self.browse(res["id"])
        batch.location_src_id = selected_location_id
        if not batch.location_src_id:
            raise UserError(_("Please select a prepacking location before submitting for release."))
        _logger.info(
            "Prepack batch %s submitted with source location %s (%s) and release location %s (%s)",
            batch.name,
            batch.location_src_id.display_name,
            batch.location_src_id.id,
            batch.location_dest_id.display_name,
            batch.location_dest_id.id,
        )
        batch._check_damage_records_before_submission()

        # Clear existing lines to rebuild the list for submission
        batch.line_ids.unlink()

        for item in payload:
            bulk_product = self.env["product.product"].browse(item["id"])
            lot = self.env["stock.lot"].browse(item["lot_id"])

            for target in item["targets"]:
                size = float(target.get("size", 0))
                qty = float(target.get("qty", 0))
                if size <= 0 or qty <= 0:
                    continue
                packaging_material_id = target.get("packaging_material_id")
                if not packaging_material_id:
                    raise UserError(
                        _("Please select packaging material for every prepack line.")
                    )

                prepack_product = self._get_or_create_prepack_product(
                    bulk_product, size
                )
                bom = self._get_or_create_prepack_bom(
                    prepack_product, bulk_product, size
                )

                line = self.env["bahmni.prepack.batch.line"].create(
                    {
                        "batch_id": batch.id,
                        "product_id": prepack_product.id,
                        "bom_id": bom.id,
                        "bulk_lot_id": lot.id,
                        "bulk_location_id": item.get("location_id") or False,
                        "package_qty": qty,
                        "release_expected_qty": int(qty),
                        "release_actual_qty": int(qty),
                        "packaging_material_id": packaging_material_id,
                    }
                )
                line._generate_manufacturing_order()

        batch.state = "pending_auth"
        batch.draft_data = False  # Clear draft data after successful submission
        return res

    def _check_damage_records_before_submission(self):
        for batch in self:
            if (
                batch.has_damaged_products
                and not batch.damage_scrap_ids
                and not batch.damage_move_ids
            ):
                raise UserError(
                    _(
                        "Please record and confirm damaged products before submitting this prepack job for release."
                    )
                )

    def action_record_damaged_products(self):
        self.ensure_one()
        self._check_can_edit_workflow()
        return {
            "type": "ir.actions.act_window",
            "name": _("Record Damaged Products"),
            "res_model": "prepack.damage.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_prepack_job_id": self.id,
                "default_unserviceable_location_id": self._default_scrap_location_id(),
            },
        }

    def _default_scrap_location_id(self):
        location = self.env["stock.location"].search(
            [
                ("usage", "=", "internal"),
                ("name", "ilike", "Unserviceable"),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.env.company.id),
            ],
            limit=1,
        )
        if location:
            return location.id
        location = self.env["stock.location"].search(
            [
                "|",
                ("scrap_location", "=", True),
                ("usage", "=", "internal"),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.env.company.id),
            ],
            limit=1,
        )
        return location.id

    def _get_or_create_prepack_product(self, bulk_product, size):
        if bulk_product.is_prepack:
            raise UserError(
                _("Prepacks must be created from bulk products, not from another prepack.")
            )

        bulk_uom = bulk_product.uom_id.name
        name = f"{bulk_product.name} - Pack of {int(size)} {bulk_uom}"
        parent_elmis_product = self.env["product.product"]
        resolver = getattr(bulk_product, "_get_elmis_accountability_product", None)
        if resolver:
            parent_elmis_product = resolver()
        if not parent_elmis_product:
            raise UserError(
                _(
                    "Bulk product %(product)s must be linked to an eLMIS product before it can be prepacked."
                )
                % {"product": bulk_product.display_name}
            )

        # Use discrete 'Units' UoM for the prepack itself
        unit_uom = self.env.ref("uom.product_uom_unit")

        prepack_product = self.env["product.product"].search(
            [
                ("is_prepack", "=", True),
                ("bulk_product_id", "=", bulk_product.id),
                ("pack_unit_qty", "=", size),
            ],
            limit=1,
        )

        if not prepack_product:
            tmpl = bulk_product.product_tmpl_id.copy(
                {
                    "name": name,
                    "is_prepack": True,
                    "bulk_product_id": bulk_product.id,
                    "is_dispensing_pack": True,
                    "dispensing_base_product_id": bulk_product.id,
                    "pack_unit_qty": size,
                    "dispensing_pack_enabled": True,
                    "uom_id": unit_uom.id,
                    "uom_po_id": unit_uom.id,
                    "standard_price": bulk_product.standard_price * size,
                    "list_price": bulk_product.list_price * size,
                }
            )
            prepack_product = tmpl.product_variant_id
        write_vals = {}
        if prepack_product.name != name:
            write_vals["name"] = name
        if prepack_product.bulk_product_id != bulk_product:
            write_vals["bulk_product_id"] = bulk_product.id
        if (
            "prepack_parent_elmis_product_id" in prepack_product._fields
            and prepack_product.prepack_parent_elmis_product_id != parent_elmis_product
        ):
            write_vals["prepack_parent_elmis_product_id"] = parent_elmis_product.id
        if write_vals:
            prepack_product.write(write_vals)

        self._ensure_prepack_product_barcode(prepack_product)
        return prepack_product

    def _ensure_prepack_product_barcode(self, prepack_product):
        if prepack_product.barcode:
            return prepack_product.barcode
        barcode = f"PREPACK-{prepack_product.id}"
        product_model = self.env["product.product"]
        existing_product = product_model.search(
            [("barcode", "=", barcode), ("id", "!=", prepack_product.id)],
            limit=1,
        )
        if existing_product:
            barcode = f"PREPACK-{prepack_product.id}-{self.env.company.id}"
        prepack_product.barcode = barcode
        return barcode

    def _get_or_create_prepack_bom(self, prepack_product, bulk_product, size):
        bom = self.env["mrp.bom"].search(
            [
                ("product_tmpl_id", "=", prepack_product.product_tmpl_id.id),
                ("type", "=", "normal"),
            ],
            limit=1,
        )

        if not bom:
            bom = self.env["mrp.bom"].create(
                {
                    "product_tmpl_id": prepack_product.product_tmpl_id.id,
                    "product_qty": 1,
                    "type": "normal",
                    "bom_line_ids": [
                        (
                            0,
                            0,
                            {
                                "product_id": bulk_product.id,
                                "product_qty": size,
                            },
                        )
                    ],
                }
            )
        return bom

    def action_release_batch(self):
        for batch in self:
            if batch.state != "pending_auth":
                raise UserError(
                    _("Only batches pending release can be released.")
                )
            _logger.info(
                "Releasing prepack batch %s with source location %s (%s) and release location %s (%s)",
                batch.name,
                batch.location_src_id.display_name,
                batch.location_src_id.id,
                batch.location_dest_id.display_name,
                batch.location_dest_id.id,
            )
            batch.line_ids._check_release_ready()
            for line in batch.line_ids:
                line._post_release_discrepancy_message()
                line._complete_manufacturing_order()
            batch.state = "done"
        return self.action_print_prepack_labels()

    def action_authorize_batch(self):
        return self.action_release_batch()

    def action_reject_batch(self, comment):
        """Reject a pending authorization batch and send it back to the creator with a comment.

        This will cancel any in-progress manufacturing orders for the batch (not done/cancelled),
        post a message to the responsible user with the rejection comment and set the batch
        back to draft so the creator can modify and resubmit.
        """
        for batch in self:
            if batch.state != "pending_auth":
                raise UserError(_("Only pending authorization batches can be rejected."))

            # Cancel any running manufacturing orders created for this batch
            for mo in batch.line_ids.mapped("mrp_production_id").filtered(
                lambda m: m.state not in ("done", "cancel")
            ):
                mo.sudo().action_cancel()

            # Reconstruct draft_data so the creator can edit the rejected items
            details = self.fetch_batch_details(batch.id)
            if details:
                batch.draft_data = json.dumps(details.get("items", []))

            # Post a message for the responsible user with the rejection comment
            body = _("Prepack batch rejected by %s: \n\n%s") % (self.env.user.name, comment or "")
            try:
                batch.message_post(body=body, partner_ids=[batch.responsible_id.partner_id.id])
            except Exception:
                # If message posting fails, still proceed to set state
                pass

            # Save the comment to the note field and revert to draft
            batch.note = (batch.note or "") + "\n\nRejection comment: " + (comment or "")
            batch.state = "rejected"
        return True

    @api.model
    def fetch_pending_batches(self, location_id=False):
        """Fetch all batches in pending_auth state for the release UI."""
        domain = [("state", "=", "pending_auth")]
        if location_id:
            domain.append(("location_src_id", "=", int(location_id)))
        batches = self.search(domain)
        result = []
        for batch in batches:
            result.append(
                {
                    "id": batch.id,
                    "name": batch.name,
                    "date": batch.planned_date.strftime("%Y-%m-%d %H:%M"),
                    "responsible": batch.responsible_id.name,
                    "line_count": batch.line_count,
                    "location_src_id": batch.location_src_id.id,
                    "location_src_name": batch.location_src_id.display_name,
                }
            )
        return result

    @api.model
    def fetch_batch_history(self):
        """Fetch all batches for the history UI."""
        # Use sudo() to ensure the history/audit trail is visible across users.
        # We filter out 'draft' to keep history clean, but explicitly include 'rejected', 'done', and 'cancel'.
        batches = self.sudo().search([("state", "!=", "draft")], order="planned_date desc, id desc", limit=100)
        result = []
        selection_map = dict(self.fields_get(["state"])["state"]["selection"])
        for batch in batches:
            result.append(
                {
                    "id": batch.id,
                    "name": batch.name,
                    "date": batch.planned_date.strftime("%Y-%m-%d %H:%M"),
                    "responsible": batch.responsible_id.name,
                    "line_count": batch.line_count,
                    "state": selection_map.get(batch.state, batch.state),
                    "state_raw": batch.state,
                    "location_src_id": batch.location_src_id.id,
                    "location_src_name": batch.location_src_id.display_name,
                }
            )
        return result

    @api.model
    def fetch_batch_details(self, batch_id):
        """Fetch details for a specific batch to load into the dashboard."""
        batch = self.browse(batch_id)
        if not batch.exists():
            return None

        # Reconstruct the batchItems structure used in the frontend JS
        grouped = {}
        for line in batch.line_ids:
            bulk_product = line.bulk_lot_id.product_id
            lot = line.bulk_lot_id
            bulk_location = line._get_component_source_location()
            key = f"{bulk_product.id}_{lot.id if lot else 0}_{bulk_location.id if bulk_location else 0}"
            packaging_soh = 0
            if line.packaging_material_id and batch.location_src_id:
                packaging_quants = self.env["stock.quant"].search(
                    [
                        ("product_id", "=", line.packaging_material_id.id),
                        ("location_id", "child_of", batch.location_src_id.id),
                    ]
                )
                packaging_soh = sum(packaging_quants.mapped("quantity"))

            if key not in grouped:
                # Get current SOH for context
                quant = self.env["stock.quant"].search(
                    [
                        ("product_id", "=", bulk_product.id),
                        ("lot_id", "=", lot.id),
                        ("location_id", "=", bulk_location.id),
                    ],
                    limit=1,
                )

                grouped[key] = {
                    "key": key,
                    "id": bulk_product.id,
                    "name": bulk_product.display_name,
                    "batch": lot.name if lot else _("No Lot"),
                    "lot_id": lot.id,
                    "location_id": bulk_location.id,
                    "location_name": bulk_location.display_name,
                    "exp": lot.expiration_date.strftime("%Y-%m")
                    if lot and lot.expiration_date
                    else "N/A",
                    "soh": quant.quantity if quant else 0,
                    "uom": bulk_product.uom_id.name,
                    "targets": [],
                }

            grouped[key]["targets"].append(
                {
                    "line_id": line.id,
                    "size": line.product_id.pack_unit_qty,
                    "qty": line.package_qty,
                    "packaging_material_id": line.packaging_material_id.id,
                    "packaging_material_name": line.packaging_material_id.display_name,
                    "packaging_material_qty": line.packaging_material_qty,
                    "packaging_material_soh": packaging_soh,
                    "packaging_material_uom": line.packaging_material_id.uom_id.name,
                    "expected_qty": line.release_expected_qty or line.package_qty,
                    "actual_qty": line._get_release_actual_qty(),
                    "actual_bulk_usage": line.release_actual_bulk_usage,
                    "has_release_discrepancy": line.has_release_discrepancy,
                    "release_discrepancy_reason": line.release_discrepancy_reason or "",
                    "quality_check_completed": line.release_quality_check_completed,
                    "state": line.state,
                }
            )

        return {
            "id": batch.id,
            "name": batch.name,
            "items": list(grouped.values()),
            "state": batch.state,
            "isAuthorized": batch.state == "done",
            "location_src_id": batch.location_src_id.id,
            "location_src_name": batch.location_src_id.display_name,
        }

    @api.model
    def check_prepack_permissions(self):
        """Check if the current user has permissions to create or authorize prepacks."""
        return {
            "can_create": self.env.user.has_group("mrp.group_mrp_user"),
            "can_authorize": self.env.user.has_group("mrp.group_mrp_manager"),
        }

    @api.depends("line_ids", "line_ids.mrp_production_id", "damage_scrap_ids", "damage_move_ids")
    def _compute_counts(self):
        for batch in self:
            batch.line_count = len(batch.line_ids)
            batch.mrp_production_count = len(batch.line_ids.mapped("mrp_production_id"))
            batch.damage_scrap_count = len(batch.damage_scrap_ids) + len(batch.damage_move_ids)

    @api.depends("line_ids.mrp_production_id")
    def _compute_mrp_production_ids(self):
        for batch in self:
            batch.mrp_production_ids = batch.line_ids.mapped("mrp_production_id")

    @api.onchange("picking_type_id")
    def _onchange_picking_type_id(self):
        for batch in self:
            batch.location_src_id = batch.picking_type_id.default_location_src_id
            batch.location_dest_id = batch.picking_type_id.default_location_dest_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bahmni.prepack.batch"
                ) or _("New")
        records = super().create(vals_list)
        for record in records:
            values = {}
            if not record.location_src_id:
                values["location_src_id"] = record.picking_type_id.default_location_src_id.id
            if not record.location_dest_id:
                values["location_dest_id"] = record.picking_type_id.default_location_dest_id.id
            if values:
                record.write(values)
        return records

    def unlink(self):
        for batch in self:
            if (
                batch.state not in ("draft", "rejected")
                or batch.line_ids.filtered("mrp_production_id")
            ):
                raise UserError(
                    _(
                        "Only draft batches without generated manufacturing orders can be deleted."
                    )
                )
        return super().unlink()

    def action_cancel(self):
        for batch in self:
            for mo in batch.line_ids.mapped("mrp_production_id").filtered(
                lambda m: m.state not in ("done", "cancel")
            ):
                mo.action_cancel()
            batch.state = "cancel"
        return True

    def action_view_manufacturing_orders(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("mrp.mrp_production_action")
        action["domain"] = [("id", "in", self.line_ids.mapped("mrp_production_id").ids)]
        action["context"] = {
            "default_prepack_batch_id": self.id,
            "default_origin": self.name,
        }
        return action

    def action_print_prepack_labels(self):
        for batch in self:
            if batch.state != "done":
                raise UserError(_("Prepack labels can only be printed after release."))
            batch.line_ids._ensure_label_barcodes()
        return self.env.ref(
            "lesotho_prepack_batch.action_report_prepack_labels"
        ).report_action(self)

    def _check_can_edit_workflow(self):
        for batch in self:
            if batch.state in ("done", "cancel"):
                raise UserError(
                    _("This batch is already closed and cannot be changed.")
                )


class BahmniPrepackBatchLine(models.Model):
    _name = "bahmni.prepack.batch.line"
    _description = "Bahmni Prepack Batch Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one(
        "bahmni.prepack.batch",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(related="batch_id.company_id", store=True)
    product_id = fields.Many2one(
        "product.product",
        string="Finished Pack",
        required=True,
        domain="[('detailed_type', '=', 'product')]",
    )
    product_tmpl_id = fields.Many2one(
        "product.template",
        string="Finished Product Template",
        related="product_id.product_tmpl_id",
        store=True,
        readonly=True,
    )
    product_barcode = fields.Char(
        string="Barcode",
        related="product_id.barcode",
        readonly=True,
    )
    label_barcode = fields.Char(
        string="Label Barcode",
        copy=False,
        index=True,
        readonly=True,
    )
    bom_id = fields.Many2one(
        "mrp.bom",
        string="Prepack Template",
        required=True,
        domain="[('type', '=', 'normal'), ('product_tmpl_id', '=', product_tmpl_id)]",
    )
    bulk_lot_id = fields.Many2one(
        "stock.lot",
        string="Source Bulk Lot",
        required=True,
        domain="[('id', 'in', available_bulk_lot_ids)]",
    )
    available_bulk_lot_ids = fields.Many2many(
        "stock.lot",
        compute="_compute_available_bulk_lot_ids",
        string="Available Bulk Lots",
    )
    bulk_product_id = fields.Many2one(
        "product.product",
        string="Bulk Product",
        related="bulk_lot_id.product_id",
        store=True,
    )
    parent_elmis_product_id = fields.Many2one(
        "product.product",
        string="Parent eLMIS Product",
        related="product_id.prepack_parent_elmis_product_id",
        store=True,
        readonly=True,
    )
    source_elmis_lot_number = fields.Char(
        string="Source eLMIS Lot Number",
        related="bulk_lot_id.elmis_lot_number",
        store=True,
        readonly=True,
    )
    package_qty = fields.Integer(
        string="Number of Prepacks",
        required=True,
        default=1,
    )
    release_expected_qty = fields.Integer(
        string="Expected Prepacks",
        copy=False,
        readonly=True,
    )
    release_actual_qty = fields.Integer(
        string="Actual Prepacks",
        copy=False,
    )
    release_actual_bulk_usage = fields.Float(
        string="Actual Bulk Usage",
        compute="_compute_release_actual_bulk_usage",
        store=True,
        digits="Product Unit of Measure",
        copy=False,
    )
    release_quality_check_completed = fields.Boolean(
        string="Quality Check Completed",
        copy=False,
    )
    released_by_id = fields.Many2one(
        "res.users",
        string="Released By",
        readonly=True,
        copy=False,
    )
    release_date = fields.Datetime(
        string="Release Date",
        readonly=True,
        copy=False,
    )
    packaging_material_id = fields.Many2one(
        "product.product",
        string="Packaging Material",
        domain="[('detailed_type', 'in', ('product', 'consu'))]",
    )
    packaging_material_qty = fields.Integer(
        string="Packaging Material Qty",
        compute="_compute_packaging_material_qty",
    )
    packaging_material_move_id = fields.Many2one(
        "stock.move",
        string="Packaging Material Move",
        readonly=True,
        copy=False,
    )
    bulk_location_id = fields.Many2one(
        "stock.location",
        string="Bulk Source Location",
        copy=False,
        help="Precise internal location of the selected bulk lot quant.",
    )
    has_release_discrepancy = fields.Boolean(
        string="Physical Prepack Count Does Not Tally",
        copy=False,
    )
    release_discrepancy_reason = fields.Text(
        string="Discrepancy Explanation",
        copy=False,
    )
    component_qty_per_pack = fields.Float(
        string="Units per Pack",
        compute="_compute_component_metrics",
        digits="Product Unit of Measure",
    )
    component_required_qty = fields.Float(
        string="Bulk Units Required",
        compute="_compute_component_metrics",
        digits="Product Unit of Measure",
    )
    bulk_qty_available = fields.Float(
        string="Lot Stock On Hand",
        compute="_compute_bulk_qty_available",
        digits="Product Unit of Measure",
    )
    has_sufficient_bulk_stock = fields.Boolean(
        string="Has Sufficient Bulk Stock",
        compute="_compute_bulk_qty_available",
    )
    finished_lot_id = fields.Many2one(
        "stock.lot",
        string="Finished Pack Lot",
        readonly=True,
        copy=False,
    )
    mrp_production_id = fields.Many2one(
        "mrp.production",
        string="Manufacturing Order",
        readonly=True,
        copy=False,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending_auth", "Pending Release"),
            ("rejected", "Rejected"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        compute="_compute_state",
    )
    note = fields.Char()

    @api.depends("package_qty", "release_actual_qty", "packaging_material_id")
    def _compute_packaging_material_qty(self):
        for line in self:
            line.packaging_material_qty = line._get_release_actual_qty() if line.packaging_material_id else 0

    @api.depends("product_id.pack_unit_qty", "release_actual_qty", "package_qty")
    def _compute_release_actual_bulk_usage(self):
        for line in self:
            line.release_actual_bulk_usage = line.product_id.pack_unit_qty * line._get_release_actual_qty()

    def _get_release_actual_qty(self):
        self.ensure_one()
        return self.release_actual_qty or self.package_qty or 0

    def _ensure_label_barcodes(self):
        for line in self:
            product_barcode = line.batch_id._ensure_prepack_product_barcode(line.product_id)
            if not line.label_barcode:
                line.label_barcode = product_barcode

    def get_prepack_label_copies(self):
        self.ensure_one()
        self._ensure_label_barcodes()
        copies = []
        quantity_per_pack = self.component_qty_per_pack
        display_quantity = int(quantity_per_pack) if float(quantity_per_pack).is_integer() else quantity_per_pack
        bulk_product = self.bulk_lot_id.product_id
        lot = self.finished_lot_id or self.bulk_lot_id
        expiry_date = ""
        if lot and lot.expiration_date:
            expiry_value = lot.expiration_date
            if hasattr(expiry_value, "date"):
                expiry_value = expiry_value.date()
            expiry_date = fields.Date.to_string(expiry_value)
        for copy_number in range(1, self._get_release_actual_qty() + 1):
            copies.append(
                {
                    "serial": f"{self.batch_id.name}-{self.sequence or self.id:02d}-{copy_number:03d}",
                    "medicine": bulk_product.name or self.product_id.name,
                    "strength": bulk_product.display_name,
                    "batch_number": lot.name if lot else "",
                    "expiry_date": expiry_date,
                    "quantity_per_pack": f"{display_quantity} {bulk_product.uom_id.name}",
                    "facility": self.batch_id.location_src_id.display_name or self.company_id.name,
                    "prepacked_by": self.batch_id.responsible_id.name or "",
                    "barcode": self.product_id.barcode or self.label_barcode or self.product_id.default_code or "",
                    "product_barcode": self.product_id.barcode or self.product_id.default_code or "",
                    "prepack_product": self.product_id.display_name,
                    "prepacked_on": fields.Date.to_string(
                        fields.Datetime.context_timestamp(
                            self, self.batch_id.planned_date
                        ).date()
                    ) if self.batch_id.planned_date else "",
                }
            )
        return copies

    @api.depends("bom_id", "bulk_lot_id", "package_qty", "product_id")
    def _compute_component_metrics(self):
        for line in self:
            qty_per_pack = 0.0
            if line.bom_id and line.bom_id.product_qty:
                matching_lines = line.bom_id.bom_line_ids
                if line.bulk_lot_id:
                    matching_lines = matching_lines.filtered(
                        lambda bom_line: (
                            bom_line.product_id == line.bulk_lot_id.product_id
                        )
                    )
                elif len(matching_lines) > 1:
                    matching_lines = self.env["mrp.bom.line"]

                if matching_lines:
                    qty_per_pack = (
                        sum(matching_lines.mapped("product_qty"))
                        / line.bom_id.product_qty
                    )
            line.component_qty_per_pack = qty_per_pack
            line.component_required_qty = qty_per_pack * line._get_release_actual_qty()

    @api.depends(
        "bulk_lot_id",
        "bulk_lot_id.product_id",
        "batch_id.location_src_id",
        "component_required_qty",
        "company_id",
    )
    def _compute_bulk_qty_available(self):
        Quant = self.env["stock.quant"]
        for line in self:
            qty_available = 0.0
            if line.bulk_lot_id and line.batch_id.location_src_id:
                location = line._get_component_source_location()
                qty_available = Quant._get_available_quantity(
                    line.bulk_lot_id.product_id,
                    location,
                    lot_id=line.bulk_lot_id,
                    strict=bool(line.bulk_location_id),
                )
            line.bulk_qty_available = qty_available
            line.has_sufficient_bulk_stock = (
                qty_available >= line.component_required_qty
            )

    @api.depends("bom_id", "batch_id.location_src_id", "company_id")
    def _compute_available_bulk_lot_ids(self):
        Quant = self.env["stock.quant"]
        for line in self:
            lots = self.env["stock.lot"]
            if line.bom_id and line.batch_id.location_src_id:
                component_products = line.bom_id.bom_line_ids.mapped("product_id")
                if component_products:
                    quants = Quant.search(
                        [
                            ("product_id", "in", component_products.ids),
                            ("lot_id", "!=", False),
                            (
                                "location_id",
                                "child_of",
                                line.batch_id.location_src_id.id,
                            ),
                            ("quantity", ">", 0),
                            "|",
                            ("company_id", "=", False),
                            ("company_id", "=", line.company_id.id),
                        ]
                    )
                    lots = quants.mapped("lot_id")
            line.available_bulk_lot_ids = lots

    @api.depends("batch_id.state", "mrp_production_id.state")
    def _compute_state(self):
        for line in self:
            mo = line.mrp_production_id
            if line.batch_id.state == "rejected":
                line.state = "rejected"
            elif not mo:
                line.state = line.batch_id.state
            elif mo.state == "done":
                line.state = "done"
            elif mo.state == "cancel":
                line.state = "rejected"
            else:
                line.state = line.batch_id.state

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self:
            if not line.product_id:
                continue
            bom = self.env["mrp.bom"].search(
                [
                    ("product_tmpl_id", "=", line.product_id.product_tmpl_id.id),
                    "|",
                    ("product_id", "=", False),
                    ("product_id", "=", line.product_id.id),
                ],
                order="sequence, id",
                limit=1,
            )
            if bom:
                line.bom_id = bom

    @api.onchange("bom_id")
    def _onchange_bom_id(self):
        for line in self:
            if line.bom_id and line.bom_id.product_id:
                line.product_id = line.bom_id.product_id

    @api.constrains("package_qty")
    def _check_package_qty(self):
        for line in self:
            if (line.package_qty or 0) <= 0:
                raise ValidationError(
                    _("Number of prepacks must be greater than zero.")
                )

    @api.constrains(
        "product_id", "bom_id", "bulk_lot_id", "batch_id.location_src_id", "package_qty"
    )
    def _check_line_configuration(self):
        for line in self:
            line._validate_configuration()
            line._validate_bulk_stock()

    def name_get(self):
        result = []
        for line in self:
            result.append(
                (
                    line.id,
                    _("%s x %s") % (line.product_id.display_name, line.package_qty or 0),
                )
            )
        return result

    def _validate_configuration(self):
        for line in self:
            if not line.product_id or line.product_id.detailed_type != "product":
                raise ValidationError(_("Finished packs must use stockable products."))
            if line.bulk_product_id and line.bulk_product_id.is_prepack:
                raise ValidationError(
                    _("The source bulk product must not itself be a prepack.")
                )
            if not line.bom_id:
                raise ValidationError(
                    _("A prepack template is required on every line.")
                )
            bom_product = (
                line.bom_id.product_id or line.bom_id.product_tmpl_id.product_variant_id
            )
            if bom_product != line.product_id:
                raise ValidationError(
                    _(
                        "The selected prepack template must manufacture the chosen finished pack."
                    )
                )
            if (
                line.bulk_lot_id
                and line.bulk_lot_id.product_id
                not in line.bom_id.bom_line_ids.mapped("product_id")
            ):
                raise ValidationError(
                    _(
                        "The source bulk lot product must exist on the selected prepack template."
                    )
                )
            parent_elmis_product = line._get_parent_elmis_product()
            if not parent_elmis_product:
                raise ValidationError(
                    _(
                        "Prepack lines require a bulk product that resolves to an eLMIS orderable."
                    )
                )
            if line.bulk_lot_id and not line._get_parent_elmis_lot():
                raise ValidationError(
                    _(
                        "Prepack lines require a source bulk lot that resolves to an eLMIS lot."
                    )
                )
            if not line.parent_elmis_product_id:
                raise ValidationError(
                    _(
                        "Finished prepack %(product)s must be linked to a parent eLMIS product before it can be released or dispensed."
                    )
                    % {"product": line.product_id.display_name}
                )
            if not (line.bulk_lot_id.elmis_lot_id or line.bulk_lot_id.elmis_lot_number):
                raise ValidationError(
                    _(
                        "Source bulk lot %(lot)s is missing its eLMIS lot mapping."
                    )
                    % {"lot": line.bulk_lot_id.display_name}
                )
            if line.bulk_location_id and line.batch_id.location_src_id:
                valid_locations = self.env["stock.location"].search(
                    [("id", "child_of", line.batch_id.location_src_id.id)]
                )
                if line.bulk_location_id not in valid_locations:
                    raise ValidationError(
                        _(
                            "Bulk source location %(actual)s must be inside selected prepacking source location %(selected)s."
                        )
                        % {
                            "actual": line.bulk_location_id.display_name,
                            "selected": line.batch_id.location_src_id.display_name,
                        }
                    )
            if float_compare(
                line.component_qty_per_pack,
                line.product_id.pack_unit_qty,
                precision_digits=6,
            ) != 0:
                raise ValidationError(
                    _(
                        "Finished prepack %(product)s has a conversion factor mismatch between its BoM and pack definition."
                    )
                    % {"product": line.product_id.display_name}
                )

    def _validate_bulk_stock(self):
        for line in self:
            if not line.bulk_lot_id or not line.batch_id.location_src_id:
                continue
            if line.component_required_qty <= 0:
                continue
            if line.bulk_qty_available < line.component_required_qty:
                raise ValidationError(
                    _(
                        "Insufficient stock for lot %(lot)s in %(location)s. "
                        "Required: %(required).2f, On hand: %(available).2f."
                    )
                    % {
                        "lot": line.bulk_lot_id.display_name,
                        "location": line.batch_id.location_src_id.display_name,
                        "required": line.component_required_qty,
                        "available": line.bulk_qty_available,
                    }
                )

    def _get_parent_elmis_product(self):
        self.ensure_one()
        for candidate in (self.product_id, self.bulk_product_id):
            if not candidate:
                continue
            resolver = getattr(candidate, "_get_elmis_accountability_product", None)
            if not resolver:
                continue
            resolved = resolver()
            if resolved:
                return resolved
        return self.env["product.product"]

    def _get_parent_elmis_lot(self):
        self.ensure_one()
        if not self.bulk_lot_id:
            return self.env["stock.lot"]
        resolver = getattr(self.bulk_lot_id, "_get_elmis_accountability_lot", None)
        if not resolver:
            return self.env["stock.lot"]
        return resolver()

    def action_authorize_line(self):
        """Validate a single prepack line for release."""
        self._check_release_ready()
        for line in self:
            line._post_release_discrepancy_message()
        self._complete_manufacturing_order()
        # If all lines are done, mark the batch as done
        if all(line.state == "done" for line in self.batch_id.line_ids):
            self.batch_id.state = "done"
        return True

    def action_update_release_values(self, size, actual_qty, quality_check_completed=False, discrepancy_explanation=False):
        """Update editable release values before manufacturing completion."""
        for line in self:
            if line.state != "pending_auth":
                raise UserError(_("Only pending release lines can be edited."))
            size = float(size or 0)
            actual_qty = int(actual_qty or 0)
            if size <= 0 or actual_qty <= 0:
                raise UserError(_("Pack size and actual prepacks must be greater than zero."))

            expected_qty = line.release_expected_qty or line.package_qty or 0
            has_discrepancy = expected_qty != actual_qty
            vals = {
                "release_expected_qty": expected_qty,
                "release_actual_qty": actual_qty,
                "has_release_discrepancy": has_discrepancy,
                "release_discrepancy_reason": (discrepancy_explanation or "").strip()
                if has_discrepancy
                else False,
                "release_quality_check_completed": bool(quality_check_completed),
            }
            size_changed = float_compare(
                line.product_id.pack_unit_qty,
                size,
                precision_digits=6,
            ) != 0
            qty_changed = line._get_release_actual_qty() != actual_qty
            audit_changed = (
                line.release_expected_qty != expected_qty
                or line.has_release_discrepancy != has_discrepancy
                or (line.release_discrepancy_reason or "") != (vals["release_discrepancy_reason"] or "")
                or line.release_quality_check_completed != bool(quality_check_completed)
            )
            if not size_changed and not qty_changed and not audit_changed:
                continue

            if size_changed:
                bulk_product = line.bulk_lot_id.product_id
                prepack_product = line.batch_id._get_or_create_prepack_product(
                    bulk_product, size
                )
                bom = line.batch_id._get_or_create_prepack_bom(
                    prepack_product, bulk_product, size
                )
                vals.update(
                    {
                        "product_id": prepack_product.id,
                        "bom_id": bom.id,
                    }
                )

            line.write(vals)
            if size_changed and line.mrp_production_id:
                mo = line.mrp_production_id
                if mo.state not in ("done", "cancel"):
                    mo.sudo().action_cancel()
                line.mrp_production_id = False
                line.packaging_material_move_id = False
                line.finished_lot_id = False
                line._generate_manufacturing_order()
            else:
                line._sync_mo_from_line()

        return True

    def _check_release_ready(self):
        for line in self:
            line._sync_release_discrepancy_state()
            if line.has_release_discrepancy and not (line.release_discrepancy_reason or "").strip():
                raise UserError(_("Please enter a discrepancy explanation."))
            if not line.release_quality_check_completed:
                raise UserError(_("Please confirm Quality Check Completed before releasing."))

    def _sync_release_discrepancy_state(self):
        for line in self:
            expected_qty = line.release_expected_qty or line.package_qty or 0
            actual_qty = line._get_release_actual_qty()
            has_discrepancy = expected_qty != actual_qty
            vals = {}
            if line.has_release_discrepancy != has_discrepancy:
                vals["has_release_discrepancy"] = has_discrepancy
            if not has_discrepancy and line.release_discrepancy_reason:
                vals["release_discrepancy_reason"] = False
            if vals:
                line.write(vals)

    def _post_release_discrepancy_message(self):
        self.ensure_one()
        if not self.has_release_discrepancy:
            return
        self.batch_id.message_post(
            body=_(
                "Release discrepancy recorded by %(user)s for %(product)s:<br/><br/>%(reason)s"
            )
            % {
                "user": self.env.user.name,
                "product": self.product_id.display_name,
                "reason": self.release_discrepancy_reason,
            }
        )

    def action_reject_line(self):
        """Reject a single prepack line by cancelling its manufacturing order."""
        for line in self:
            if line.mrp_production_id:
                mo = line.mrp_production_id
                if mo.state not in ("done", "cancel"):
                    mo.sudo().action_cancel()
        return True

    def action_print_prepack_label(self):
        self.ensure_one()
        if self.state != "done":
            raise UserError(_("Only released prepack lines can have labels printed."))
        self._ensure_label_barcodes()
        return self.env.ref(
            "lesotho_prepack_batch.action_report_prepack_labels"
        ).with_context(prepack_line_ids=self.ids).report_action(self.batch_id)

    def _get_finished_lot_name(self):
        self.ensure_one()
        return self.bulk_lot_id.name

    def _ensure_finished_lot(self):
        self.ensure_one()
        if self.finished_lot_id:
            if "prepack_source_lot_id" in self.finished_lot_id._fields:
                if (
                    self.finished_lot_id.prepack_source_lot_id
                    and self.finished_lot_id.prepack_source_lot_id != self.bulk_lot_id
                ):
                    raise ValidationError(
                        _(
                            "Finished lot %(lot)s is already linked to source bulk lot %(source)s."
                        )
                        % {
                            "lot": self.finished_lot_id.display_name,
                            "source": self.finished_lot_id.prepack_source_lot_id.display_name,
                        }
                    )
                if not self.finished_lot_id.prepack_source_lot_id:
                    self.finished_lot_id.write(
                        {"prepack_source_lot_id": self.bulk_lot_id.id}
                    )
            return self.finished_lot_id
        finished_lot = self.env["stock.lot"].search(
            [
                ("name", "=", self._get_finished_lot_name()),
                ("product_id", "=", self.product_id.id),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.company_id.id),
            ],
            limit=1,
        )
        if not finished_lot:
            vals = {
                "name": self._get_finished_lot_name(),
                "product_id": self.product_id.id,
                "company_id": self.company_id.id,
            }
            if "prepack_source_lot_id" in self.env["stock.lot"]._fields:
                vals["prepack_source_lot_id"] = self.bulk_lot_id.id
            for field_name in (
                "expiration_date",
                "use_date",
                "removal_date",
                "alert_date",
            ):
                if (
                    field_name in self.bulk_lot_id._fields
                    and field_name in self.env["stock.lot"]._fields
                ):
                    vals[field_name] = self.bulk_lot_id[field_name]
            finished_lot = self.env["stock.lot"].create(vals)
        elif "prepack_source_lot_id" in finished_lot._fields:
            if (
                finished_lot.prepack_source_lot_id
                and finished_lot.prepack_source_lot_id != self.bulk_lot_id
            ):
                raise ValidationError(
                    _(
                        "Finished lot %(lot)s is already linked to source bulk lot %(source)s."
                    )
                    % {
                        "lot": finished_lot.display_name,
                        "source": finished_lot.prepack_source_lot_id.display_name,
                    }
                )
            if not finished_lot.prepack_source_lot_id:
                finished_lot.write({"prepack_source_lot_id": self.bulk_lot_id.id})
        self.finished_lot_id = finished_lot.id
        return finished_lot

    def _prepare_mo_vals(self):
        self.ensure_one()
        finished_lot = self._ensure_finished_lot()
        source_location = self._get_component_source_location()
        release_location = self.batch_id.location_dest_id
        actual_qty = self._get_release_actual_qty()
        return {
            "origin": self.batch_id.name,
            "product_id": self.product_id.id,
            "product_qty": actual_qty,
            "product_uom_id": self.product_id.uom_id.id,
            "bom_id": self.bom_id.id,
            "company_id": self.company_id.id,
            "date_start": self.batch_id.planned_date,
            "picking_type_id": self.batch_id.picking_type_id.id,
            "location_src_id": source_location.id,
            "location_dest_id": release_location.id,
            "lot_producing_id": finished_lot.id,
            "prepack_batch_id": self.batch_id.id,
            "prepack_batch_line_id": self.id,
            "prepack_source_lot_id": self.bulk_lot_id.id,
            "prepack_finished_lot_id": finished_lot.id,
        }

    def _sync_mo_from_line(self):
        for line in self:
            mo = line.mrp_production_id
            if not mo or mo.state in ("done", "cancel"):
                continue
            actual_qty = line._get_release_actual_qty()
            source_location = line._get_component_source_location()
            release_location = line.batch_id.location_dest_id
            vals = {}
            if mo.product_qty != actual_qty:
                vals["product_qty"] = actual_qty
            if source_location and mo.location_src_id != source_location:
                vals["location_src_id"] = source_location.id
            if release_location and mo.location_dest_id != release_location:
                vals["location_dest_id"] = release_location.id
            if vals:
                mo.write(vals)
            if line.bom_id and line.bom_id.product_qty:
                raw_qty_map = {}
                for bom_line in line.bom_id.bom_line_ids:
                    raw_qty_map.setdefault(bom_line.product_id.id, 0.0)
                    raw_qty_map[bom_line.product_id.id] += (
                        bom_line.product_qty / line.bom_id.product_qty
                    ) * actual_qty
                for move in mo.move_raw_ids.filtered(
                    lambda m: m.state not in ("done", "cancel")
                ):
                    move_vals = {}
                    if (
                        source_location
                        and move.product_id == line.bulk_lot_id.product_id
                        and move.location_id != source_location
                    ):
                        move_vals["location_id"] = source_location.id
                    expected_qty = raw_qty_map.get(move.product_id.id)
                    if (
                        expected_qty is not None
                        and move.product_uom_qty != expected_qty
                    ):
                        move_vals["product_uom_qty"] = expected_qty
                    if move_vals:
                        move.write(move_vals)
            for move in mo.move_finished_ids.filtered(
                lambda m: m.state not in ("done", "cancel")
            ):
                move_vals = {}
                if (
                    move.product_id == line.product_id
                    and move.product_uom_qty != actual_qty
                ):
                    move_vals["product_uom_qty"] = actual_qty
                if release_location and move.location_dest_id != release_location:
                    move_vals["location_dest_id"] = release_location.id
                if move_vals:
                    move.write(move_vals)
            line._sync_packaging_material_move()
            if "qty_producing" in mo._fields and mo.qty_producing != mo.product_qty:
                mo.write({"qty_producing": mo.product_qty})

    def _sync_packaging_material_move(self):
        for line in self:
            mo = line.mrp_production_id
            move = line.packaging_material_move_id
            if not mo or mo.state in ("done", "cancel"):
                continue
            if move and move.state in ("done", "cancel"):
                move = self.env["stock.move"]
                line.packaging_material_move_id = False
            if not line.packaging_material_id:
                if move:
                    move.unlink()
                    line.packaging_material_move_id = False
                continue
            values = {
                "name": line.packaging_material_id.display_name,
                "product_id": line.packaging_material_id.id,
                "product_uom_qty": line.packaging_material_qty,
                "product_uom": line.packaging_material_id.uom_id.id,
                "location_id": line.batch_id.location_src_id.id,
                "location_dest_id": line.batch_id.location_dest_id.id,
                "company_id": line.company_id.id,
                "raw_material_production_id": mo.id,
                "origin": mo.name,
            }
            if move:
                if move.product_id != line.packaging_material_id:
                    if move.state != "draft":
                        raise UserError(
                            _(
                                "Packaging material cannot be changed after the manufacturing order is confirmed."
                            )
                        )
                    move.write(values)
                elif move.product_uom_qty != line.packaging_material_qty:
                    move.write({"product_uom_qty": line.packaging_material_qty})
            else:
                line.packaging_material_move_id = self.env["stock.move"].create(values).id
                move = line.packaging_material_move_id
            if move and mo.state != "draft":
                if move.state == "draft":
                    move._action_confirm()
                if move.state in ("confirmed", "waiting", "partially_available"):
                    move._action_assign()

    def _generate_manufacturing_order(self):
        for line in self:
            line._validate_configuration()
            line._validate_bulk_stock()
            if line.mrp_production_id and line.mrp_production_id.state != "cancel":
                continue
            mo = self.env["mrp.production"].create(line._prepare_mo_vals())
            line.mrp_production_id = mo.id
            line._sync_packaging_material_move()
        return True

    def _confirm_manufacturing_order(self):
        for line in self:
            line._validate_bulk_stock()
            if not line.mrp_production_id:
                line._generate_manufacturing_order()
            mo = line.mrp_production_id
            if mo.state in ("done", "cancel"):
                continue
            line._sync_mo_from_line()
            if mo.state == "draft":
                mo.action_confirm()
            line._sync_packaging_material_move()
            if hasattr(mo, "action_assign"):
                mo.action_assign()
            line._apply_component_lot()
            line._apply_packaging_material_consumption()
            line._apply_finished_lot()
        return True

    def _complete_manufacturing_order(self):
        for line in self:
            line._validate_bulk_stock()
            line._sync_mo_from_line()
            line._confirm_manufacturing_order()
            mo = line.mrp_production_id
            if mo.state in ("done", "cancel"):
                continue
            _logger.info(
                "Completing prepack MO %s for batch %s: batch source=%s (%s), batch release=%s (%s), MO source=%s (%s), MO destination=%s (%s)",
                mo.display_name,
                line.batch_id.name,
                line.batch_id.location_src_id.display_name,
                line.batch_id.location_src_id.id,
                line.batch_id.location_dest_id.display_name,
                line.batch_id.location_dest_id.id,
                mo.location_src_id.display_name,
                mo.location_src_id.id,
                mo.location_dest_id.display_name,
                mo.location_dest_id.id,
            )
            if "qty_producing" in mo._fields and not mo.qty_producing:
                mo.write({"qty_producing": mo.product_qty})
            result = mo.with_context(
                skip_consumption=True,
                skip_backorder=True,
                cancel_backorder=True,
            ).button_mark_done()
            if isinstance(result, dict) and not line._process_mo_completion_action(result):
                action_name = result.get("name") or result.get("res_model") or _("Unknown action")
                raise UserError(
                    _(
                        "Manufacturing order %(order)s returned %(action)s before completion. "
                        "Please open the manufacturing order and resolve the pending wizard."
                    )
                    % {
                        "order": mo.display_name,
                        "action": action_name,
                    }
                )
            line.finished_lot_id = mo.lot_producing_id
            line.write(
                {
                    "release_expected_qty": line.release_expected_qty or line.package_qty,
                    "release_actual_qty": line._get_release_actual_qty(),
                    "released_by_id": self.env.user.id,
                    "release_date": fields.Datetime.now(),
                }
            )
            for move in mo.move_raw_ids:
                _logger.info(
                    "Prepack MO %s raw move %s after completion: product=%s, qty=%s, state=%s, move-line qty_done=%s",
                    mo.display_name,
                    move.display_name,
                    move.product_id.display_name,
                    move.product_uom_qty,
                    move.state,
                    sum(move.move_line_ids.mapped("qty_done")),
                )
        return True

    def _process_mo_completion_action(self, action):
        self.ensure_one()
        res_model = action.get("res_model")
        allowed_models = {
            "mrp.consumption.warning": ("action_confirm",),
            "mrp.immediate.production": ("process", "action_process"),
        }
        if res_model not in allowed_models:
            return False

        try:
            wizard_model = self.env[res_model]
        except KeyError:
            return False

        action_context = action.get("context") or {}
        if not isinstance(action_context, dict):
            action_context = {}
        context = dict(self.env.context, **action_context)
        wizard = wizard_model.with_context(context)
        if action.get("res_id"):
            wizard = wizard.browse(action["res_id"])
        else:
            wizard = wizard.create({})

        for method_name in allowed_models[res_model]:
            if hasattr(wizard, method_name):
                result = getattr(wizard, method_name)()
                return not isinstance(result, dict)
        return False

    def _apply_component_lot(self):
        for line in self:
            mo = line.mrp_production_id
            if not mo:
                continue
            source_location = line._get_component_source_location()
            target_moves = mo.move_raw_ids.filtered(
                lambda move: (
                    move.state not in ("done", "cancel")
                    and move.product_id == line.bulk_lot_id.product_id
                )
            )
            if not target_moves:
                raise UserError(
                    _("No component move for %s was generated on %s.")
                    % (line.bulk_lot_id.product_id.display_name, mo.display_name)
                )
            for move in target_moves:
                if source_location and move.location_id != source_location:
                    move.write({"location_id": source_location.id})
                _logger.info(
                    "Preparing prepack raw move for batch %s: batch source=%s (%s), release=%s (%s), MO source=%s (%s), MO destination=%s (%s), raw qty=%s",
                    line.batch_id.name,
                    line.batch_id.location_src_id.display_name,
                    line.batch_id.location_src_id.id,
                    line.batch_id.location_dest_id.display_name,
                    line.batch_id.location_dest_id.id,
                    mo.location_src_id.display_name,
                    mo.location_src_id.id,
                    mo.location_dest_id.display_name,
                    mo.location_dest_id.id,
                    move.product_uom_qty,
                )
                line._write_move_lines(move, line.bulk_lot_id, location=source_location)

    def _apply_finished_lot(self):
        for line in self:
            mo = line.mrp_production_id
            if not mo:
                continue
            finished_lot = line._ensure_finished_lot()
            mo.write(
                {
                    "lot_producing_id": finished_lot.id,
                    "prepack_source_lot_id": line.bulk_lot_id.id,
                    "prepack_finished_lot_id": finished_lot.id,
                }
            )
            target_moves = mo.move_finished_ids.filtered(
                lambda move: (
                    move.state not in ("done", "cancel")
                    and move.product_id == line.product_id
                )
            )
            for move in target_moves:
                line._write_move_lines(move, finished_lot)

    def _apply_packaging_material_consumption(self):
        for line in self:
            move = line.packaging_material_move_id
            if (
                not move
                or not line.packaging_material_id
                or move.state in ("done", "cancel")
            ):
                continue
            qty = line.packaging_material_qty
            vals = {
                "location_id": move.location_id.id,
                "location_dest_id": move.location_dest_id.id,
                "qty_done": qty,
            }
            if "product_uom_id" in self.env["stock.move.line"]._fields:
                vals["product_uom_id"] = move.product_uom.id
            first_line = move.move_line_ids[:1]
            if not first_line:
                first_line = self.env["stock.move.line"].create(
                    dict(vals, move_id=move.id, product_id=move.product_id.id)
                )
            else:
                first_line.write(vals)
            for extra_line in move.move_line_ids - first_line:
                extra_line.write({"qty_done": 0})

    def _write_move_lines(self, move, lot, location=False):
        self.ensure_one()
        qty = move.product_uom_qty
        source_location = location or move.location_id
        vals = {
            "lot_id": lot.id,
            "location_id": source_location.id,
            "location_dest_id": move.location_dest_id.id,
            "qty_done": qty,
        }
        if "product_uom_id" in self.env["stock.move.line"]._fields:
            vals["product_uom_id"] = move.product_uom.id
        first_line = move.move_line_ids[:1]
        if not first_line:
            first_line = self.env["stock.move.line"].create(
                dict(vals, move_id=move.id, product_id=move.product_id.id)
            )
        else:
            first_line.write(vals)
        for extra_line in move.move_line_ids - first_line:
            extra_line.write({"lot_id": lot.id, "qty_done": 0})
        _logger.info(
            "Prepared prepack move line for move %s: lot=%s, source=%s (%s), destination=%s (%s), qty_done=%s",
            move.display_name,
            lot.display_name,
            source_location.display_name,
            source_location.id,
            move.location_dest_id.display_name,
            move.location_dest_id.id,
            qty,
        )

    def _find_bulk_quant_location(self):
        self.ensure_one()
        if not self.bulk_lot_id or not self.batch_id.location_src_id:
            return self.env["stock.location"]
        quants = self.env["stock.quant"].search(
            [
                ("product_id", "=", self.bulk_lot_id.product_id.id),
                ("lot_id", "=", self.bulk_lot_id.id),
                ("location_id", "child_of", self.batch_id.location_src_id.id),
                ("quantity", ">", 0),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.company_id.id),
            ],
            order="quantity desc, id",
        )
        required_qty = self.component_required_qty
        for quant in quants:
            available_qty = self.env["stock.quant"]._get_available_quantity(
                self.bulk_lot_id.product_id,
                quant.location_id,
                lot_id=self.bulk_lot_id,
                strict=True,
            )
            if available_qty >= required_qty:
                return quant.location_id
        return quants[:1].location_id if quants else self.env["stock.location"]

    def _get_component_source_location(self):
        self.ensure_one()
        if self.bulk_location_id:
            return self.bulk_location_id
        quant_location = self._find_bulk_quant_location()
        if quant_location:
            self.bulk_location_id = quant_location.id
            return quant_location
        return self.batch_id.location_src_id


class PrepackDamageWizard(models.TransientModel):
    _name = "prepack.damage.wizard"
    _description = "Record Prepack Damaged Products"

    prepack_job_id = fields.Many2one(
        "bahmni.prepack.batch",
        string="Prepack Job",
        required=True,
        readonly=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Damaged Product",
        required=True,
        domain="[('id', 'in', available_product_ids)]",
    )
    available_product_ids = fields.Many2many(
        "product.product",
        compute="_compute_available_product_ids",
    )
    damaged_qty = fields.Float(
        string="Damaged Quantity",
        required=True,
        digits="Product Unit of Measure",
    )
    damage_reason = fields.Text(string="Damage Reason", required=True)
    elmis_program_id = fields.Many2one(
        "elmis.program",
        string="eLMIS Program",
        domain="[('id', 'in', available_elmis_program_ids)]",
    )
    available_elmis_program_ids = fields.Many2many(
        "elmis.program",
        compute="_compute_available_elmis_program_ids",
    )
    elmis_adjustment_reason = fields.Selection(
        ELMIS_ADJUSTMENT_REASON_SELECTION,
        string="eLMIS Adjustment Reason",
        default="Damaged",
        required=True,
    )
    unserviceable_location_id = fields.Many2one(
        "stock.location",
        string="Unserviceable/Scrap Location",
        required=True,
        domain="['|', ('scrap_location', '=', True), ('usage', '=', 'internal')]",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        batch = self.env["bahmni.prepack.batch"].browse(
            values.get("prepack_job_id")
            or self.env.context.get("default_prepack_job_id")
        )
        product_ids = self._get_batch_product_ids(batch)
        if not values.get("product_id") and product_ids:
            values["product_id"] = product_ids[0]
        if not values.get("unserviceable_location_id"):
            values["unserviceable_location_id"] = batch._default_scrap_location_id()
        if not values.get("elmis_adjustment_reason"):
            values["elmis_adjustment_reason"] = "Damaged"
        if values.get("product_id") and not values.get("elmis_program_id"):
            product = self.env["product.product"].browse(values["product_id"])
            if len(product.elmis_program_ids) == 1:
                values["elmis_program_id"] = product.elmis_program_ids.id
        return values

    @api.depends("prepack_job_id", "prepack_job_id.line_ids", "prepack_job_id.draft_data")
    def _compute_available_product_ids(self):
        for wizard in self:
            wizard.available_product_ids = self.env["product.product"].browse(
                wizard._get_batch_product_ids(wizard.prepack_job_id)
            )

    @api.depends("product_id")
    def _compute_available_elmis_program_ids(self):
        for wizard in self:
            wizard.available_elmis_program_ids = wizard.product_id.elmis_program_ids

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for wizard in self:
            wizard.elmis_program_id = False
            if len(wizard.product_id.elmis_program_ids) == 1:
                wizard.elmis_program_id = wizard.product_id.elmis_program_ids[0]

    def _get_batch_product_ids(self, batch):
        product_ids = []
        for product in batch.line_ids.mapped("bulk_lot_id.product_id"):
            if product.id not in product_ids:
                product_ids.append(product.id)
        if batch.draft_data:
            try:
                draft_items = json.loads(batch.draft_data)
            except (TypeError, ValueError):
                draft_items = []
            for item in draft_items:
                product_id = item.get("id")
                if product_id and product_id not in product_ids:
                    product_ids.append(product_id)
        return product_ids

    def action_confirm_damage(self):
        self.ensure_one()
        batch = self.prepack_job_id
        batch._check_can_edit_workflow()
        if self.damaged_qty <= 0:
            raise UserError(_("Damaged quantity must be greater than zero."))

        available_qty = self._get_available_or_picked_quantity()
        if self.damaged_qty > available_qty:
            raise UserError(
                _(
                    "Damaged quantity cannot exceed the available/picked quantity. Available: %(available).2f."
                )
                % {"available": available_qty}
            )

        if self.unserviceable_location_id.usage == "internal":
            damage_record = self._create_unserviceable_move()
            damage_ref = damage_record.display_name
            batch.write(
                {
                    "has_damaged_products": True,
                    "damage_move_ids": [(4, damage_record.id)],
                }
            )
        else:
            damage_record = self._create_scrap_record()
            damage_ref = damage_record.name
            batch.write(
                {
                    "has_damaged_products": True,
                    "damage_scrap_ids": [(4, damage_record.id)],
                }
            )

        batch.message_post(
            body=_(
                "Damaged product recorded: %(product)s, quantity %(qty).2f %(uom)s. Reason: %(reason)s. Reference: %(reference)s."
            )
            % {
                "product": self.product_id.display_name,
                "qty": self.damaged_qty,
                "uom": self.product_id.uom_id.name,
                "reason": self.damage_reason,
                "reference": damage_ref,
            }
        )
        if self.env.context.get("from_prepack_dashboard"):
            return {"type": "ir.actions.act_window_close"}
        return {
            "type": "ir.actions.act_window",
            "res_model": "bahmni.prepack.batch",
            "res_id": batch.id,
            "view_mode": "form",
            "target": "current",
        }

    def _create_scrap_record(self):
        self.ensure_one()
        lot = self._get_damage_lot()
        source_location = self._get_damage_source_location(lot)
        scrap = self.env["stock.scrap"].create(
            {
                "product_id": self.product_id.id,
                "scrap_qty": self.damaged_qty,
                "product_uom_id": self.product_id.uom_id.id,
                "location_id": source_location.id,
                "scrap_location_id": self.unserviceable_location_id.id,
                "company_id": self.prepack_job_id.company_id.id,
                "origin": self.prepack_job_id.name,
                "elmis_program_id": self.elmis_program_id.id,
                "elmis_adjustment_reason": self.elmis_adjustment_reason,
                **self._get_scrap_lot_values(lot),
            }
        )
        scrap.action_validate()
        return scrap

    def _create_unserviceable_move(self):
        self.ensure_one()
        lot = self._get_damage_lot()
        source_location = self._get_damage_source_location(lot)
        move = self.env["stock.move"].create(
            {
                "name": _("Damaged product for %(batch)s") % {"batch": self.prepack_job_id.name},
                "product_id": self.product_id.id,
                "product_uom_qty": self.damaged_qty,
                "product_uom": self.product_id.uom_id.id,
                "location_id": source_location.id,
                "location_dest_id": self.unserviceable_location_id.id,
                "origin": self.prepack_job_id.name,
                "company_id": self.prepack_job_id.company_id.id,
            }
        )
        move._action_confirm()
        move_line_values = {
            "move_id": move.id,
            "product_id": self.product_id.id,
            "location_id": source_location.id,
            "location_dest_id": self.unserviceable_location_id.id,
            "qty_done": self.damaged_qty,
        }
        if "product_uom_id" in self.env["stock.move.line"]._fields:
            move_line_values["product_uom_id"] = self.product_id.uom_id.id
        if lot:
            move_line_values["lot_id"] = lot.id
        self.env["stock.move.line"].create(move_line_values)
        move._action_done()
        return move

    def _get_damage_lot(self):
        self.ensure_one()
        matching_line = self.prepack_job_id.line_ids.filtered(
            lambda line: line.bulk_lot_id.product_id == self.product_id
        )[:1]
        if matching_line:
            return matching_line.bulk_lot_id
        if self.prepack_job_id.draft_data:
            try:
                draft_items = json.loads(self.prepack_job_id.draft_data)
            except (TypeError, ValueError):
                draft_items = []
            for item in draft_items:
                if item.get("id") == self.product_id.id and item.get("lot_id"):
                    return self.env["stock.lot"].browse(item["lot_id"])
        return self.env["stock.lot"]

    def _get_damage_source_location(self, lot):
        self.ensure_one()
        domain = [
            ("product_id", "=", self.product_id.id),
            ("location_id", "child_of", self.prepack_job_id.location_src_id.id),
            ("quantity", ">", 0),
        ]
        if lot:
            domain.append(("lot_id", "=", lot.id))
        quant = self.env["stock.quant"].search(domain, order="quantity desc", limit=1)
        return quant.location_id if quant else self.prepack_job_id.location_src_id

    def _get_available_or_picked_quantity(self):
        self.ensure_one()
        batch = self.prepack_job_id
        matching_lines = batch.line_ids.filtered(
            lambda line: line.bulk_lot_id.product_id == self.product_id
        )
        if matching_lines:
            available_qty = sum(matching_lines.mapped("component_required_qty"))
        else:
            available_qty = self.env["stock.quant"]._get_available_quantity(
                self.product_id,
                batch.location_src_id,
                strict=False,
            )
            if batch.draft_data:
                try:
                    draft_items = json.loads(batch.draft_data)
                except (TypeError, ValueError):
                    draft_items = []
                draft_quantities = [
                    item.get("soh", 0.0)
                    for item in draft_items
                    if item.get("id") == self.product_id.id
                ]
                if draft_quantities:
                    available_qty = sum(draft_quantities)
        damaged_qty = sum(
            batch.damage_scrap_ids.filtered(
                lambda scrap: scrap.product_id == self.product_id
            ).mapped("scrap_qty")
        )
        damaged_qty += sum(
            batch.damage_move_ids.filtered(
                lambda move: move.product_id == self.product_id
            ).mapped("product_uom_qty")
        )
        return max(available_qty - damaged_qty, 0.0)

    def _get_scrap_lot_values(self, lot=None):
        self.ensure_one()
        lot = lot or self._get_damage_lot()
        return {"lot_id": lot.id} if lot else {}
