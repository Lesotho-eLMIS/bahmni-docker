from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLocationInventoryView(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.program_art = cls.env.ref("lesotho_elmis_integration.elmis_program_art")
        cls.mirror_location = cls.env["stock.location"].create(
            {
                "name": "Location Inventory Pharmacy",
                "usage": "internal",
                "location_id": cls.env.ref("stock.stock_location_stock").id,
                "elmis_facility_code": "A2681-cp",
            }
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "lesotho_elmis_integration.mirror_location_ids",
            str(cls.mirror_location.id),
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "lesotho_elmis_integration.mirror_location_id",
            str(cls.mirror_location.id),
        )
        template = cls.env["product.template"].create(
            {
                "name": "Location Inventory Product",
                "default_code": "INV-TAB-001",
                "detailed_type": "product",
                "uom_id": cls.unit_uom.id,
                "uom_po_id": cls.unit_uom.id,
            }
        )
        cls.product = template.product_variant_id
        cls.product.write(
            {
                "is_elmis_product": True,
                "elmis_orderable_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "elmis_product_code": "INV-TAB-001",
                "elmis_pack_size": 10,
                "elmis_pack_size_unit": "units",
                "elmis_program_ids": [(4, cls.program_art.id)],
            }
        )
        out_template = cls.env["product.template"].create(
            {
                "name": "Out of Stock Dashboard Product",
                "default_code": "INV-OUT-001",
                "detailed_type": "product",
                "uom_id": cls.unit_uom.id,
                "uom_po_id": cls.unit_uom.id,
            }
        )
        cls.out_product = out_template.product_variant_id
        cls.out_product.write(
            {
                "is_elmis_product": True,
                "elmis_orderable_id": "99999999-bbbb-cccc-dddd-eeeeeeeeeeee",
                "elmis_product_code": "INV-OUT-001",
                "elmis_program_ids": [(4, cls.program_art.id)],
            }
        )
        cls.expiry_datetime = fields.Datetime.add(fields.Datetime.now(), days=30)
        cls.lot = cls.env["stock.lot"].create(
            {
                "name": "INV-LOT-001",
                "product_id": cls.product.id,
                "company_id": cls.env.company.id,
                "elmis_lot_id": "ffffffff-1111-2222-3333-444444444444",
                "expiration_date": cls.expiry_datetime,
            }
        )

    def test_location_inventory_related_display_fields(self):
        self.env["stock.quant"]._update_available_quantity(
            self.product,
            self.mirror_location,
            25,
            lot_id=self.lot,
        )
        quant = self.env["stock.quant"].search(
            [
                ("product_id", "=", self.product.id),
                ("location_id", "=", self.mirror_location.id),
                ("lot_id", "=", self.lot.id),
            ],
            limit=1,
        )

        self.assertEqual(quant.elmis_product_code, "INV-TAB-001")
        self.assertEqual(quant.elmis_location_facility_code, "A2681-cp")
        self.assertEqual(
            quant.elmis_lot_expiration_date,
            self.expiry_datetime,
        )
        self.assertEqual(quant.elmis_program_ids, self.program_art)
        self.assertEqual(quant.elmis_stock_status, "available")
        self.assertEqual(quant.elmis_expiry_status, "expiring_soon")
        self.assertGreaterEqual(quant.elmis_days_to_expiry, 29)

    def test_location_inventory_menu_opens_dashboard_with_detailed_fallback(self):
        operations_menu = self.env.ref("stock.menu_stock_warehouse_mgmt")
        menu = self.env.ref("lesotho_elmis_integration.menu_location_inventory")
        dashboard_action = self.env.ref(
            "lesotho_elmis_integration.action_elmis_inventory_dashboard"
        )
        detail_action = self.env.ref("lesotho_elmis_integration.action_location_inventory")
        adjustment_action = self.env.ref(
            "lesotho_elmis_integration.action_elmis_inventory_adjustments"
        )
        tree_view = self.env.ref("lesotho_elmis_integration.view_stock_quant_tree_location_inventory")
        adjustment_view = self.env.ref(
            "lesotho_elmis_integration.view_stock_quant_tree_elmis_adjustment"
        )
        pivot_view = self.env.ref("lesotho_elmis_integration.view_stock_quant_pivot_location_inventory")
        graph_view = self.env.ref("lesotho_elmis_integration.view_stock_quant_graph_location_inventory")

        self.assertEqual(menu.parent_id, operations_menu)
        self.assertEqual(menu.action, dashboard_action)
        self.assertEqual(
            dashboard_action.tag,
            "lesotho_elmis_integration.inventory_dashboard",
        )
        self.assertEqual(detail_action.res_model, "stock.quant")
        self.assertEqual(detail_action.view_mode, "tree,pivot,graph")
        self.assertEqual(adjustment_action.res_model, "stock.quant")
        self.assertEqual(adjustment_action.view_id, adjustment_view)
        self.assertIn("inventory_mode", adjustment_action.context)
        self.assertIn("product_id.is_elmis_product", adjustment_action.domain)
        self.assertIn("location_id.elmis_facility_code", adjustment_action.domain)
        self.assertIn("location_id.usage", detail_action.domain)
        self.assertIn("quantity", detail_action.domain)
        self.assertIn("search_default_elmis_products", detail_action.context)
        self.assertIn("search_default_elmis_mirror_locations", detail_action.context)
        self.assertIn('create="false"', tree_view.arch_db)
        self.assertIn('edit="false"', tree_view.arch_db)
        self.assertIn('delete="false"', tree_view.arch_db)
        self.assertIn("elmis_stock_status", tree_view.arch_db)
        self.assertIn("elmis_expiry_status", tree_view.arch_db)
        self.assertIn("action_open_elmis_batch_stock_card", tree_view.arch_db)
        self.assertIn("o_elmis_workspace_list", tree_view.arch_db)
        self.assertIn("o_elmis_adjustment_list", adjustment_view.arch_db)
        self.assertIn("elmis_inventory_program_id", adjustment_view.arch_db)
        self.assertIn("elmis_inventory_adjustment_reason", adjustment_view.arch_db)
        self.assertIn("quantity", pivot_view.arch_db)
        self.assertIn("location_id", graph_view.arch_db)

    def test_dashboard_aggregates_products_lots_thresholds_and_sync_health(self):
        self.env["stock.quant"]._update_available_quantity(
            self.product,
            self.mirror_location,
            25,
            lot_id=self.lot,
        )
        self.env["stock.warehouse.orderpoint"].create(
            {
                "product_id": self.product.id,
                "location_id": self.mirror_location.id,
                "product_min_qty": 30,
                "product_max_qty": 60,
            }
        )

        dashboard = self.env["stock.quant"].get_elmis_inventory_dashboard(
            {
                "location_id": self.mirror_location.id,
                "search": "INV-",
                "page_size": 25,
            }
        )

        rows = {row["id"]: row for row in dashboard["products"]}
        self.assertEqual(dashboard["selected_location"]["id"], self.mirror_location.id)
        self.assertEqual(rows[self.product.id]["quantity"], 25)
        self.assertEqual(rows[self.product.id]["pack_size"], 10)
        self.assertEqual(rows[self.product.id]["pack_size_unit"], "units")
        self.assertEqual(rows[self.product.id]["stock_status"], "low")
        self.assertEqual(rows[self.product.id]["lot_count"], 1)
        self.assertEqual(rows[self.product.id]["lots"][0]["name"], "INV-LOT-001")
        self.assertEqual(rows[self.product.id]["expiry_status"], "expiring_soon")
        self.assertEqual(rows[self.out_product.id]["stock_status"], "out")
        self.assertEqual(dashboard["summary"]["low"], 1)
        self.assertGreaterEqual(dashboard["summary"]["out"], 1)
        self.assertIn("outbox", dashboard["sync"])
        self.assertEqual(
            dashboard["actions"]["detail"],
            "lesotho_elmis_integration.action_location_inventory",
        )
        self.assertEqual(
            dashboard["actions"]["adjustments"],
            "lesotho_elmis_integration.action_elmis_inventory_adjustments",
        )

    def test_integration_event_views_use_workspace_theme_and_status_badges(self):
        tree_view = self.env.ref(
            "lesotho_elmis_integration.elmis_outbox_view_tree"
        )
        form_view = self.env.ref(
            "lesotho_elmis_integration.elmis_outbox_view_form"
        )
        dlq_view = self.env.ref(
            "lesotho_elmis_integration.elmis_outbox_dlq_view_tree"
        )
        action = self.env.ref("lesotho_elmis_integration.action_elmis_outbox")

        self.assertEqual(action.name, "Integration Events")
        self.assertIn("o_elmis_workspace_list", tree_view.arch_db)
        self.assertIn('name="status" widget="badge"', tree_view.arch_db)
        self.assertIn("decoration-danger", tree_view.arch_db)
        self.assertIn("o_elmis_workspace_form", form_view.arch_db)
        self.assertIn("eLMIS Transaction", form_view.arch_db)
        self.assertIn("o_elmis_dlq_list", dlq_view.arch_db)

    def test_dashboard_filters_by_search_program_and_status(self):
        dashboard = self.env["stock.quant"].get_elmis_inventory_dashboard(
            {
                "location_id": self.mirror_location.id,
                "search": "INV-OUT-001",
                "program_id": self.program_art.id,
                "status": "out",
            }
        )

        self.assertEqual(dashboard["pagination"]["total"], 1)
        self.assertEqual(dashboard["products"][0]["id"], self.out_product.id)

    def test_dashboard_reports_non_overlapping_expiry_quantity_buckets(self):
        expiry_lots = [
            ("INV-EXPIRED", -5, 2),
            ("INV-EXP-15", 15, 3),
            ("INV-EXP-45", 45, 4),
            ("INV-EXP-75", 75, 5),
        ]
        for name, days, quantity in expiry_lots:
            lot = self.env["stock.lot"].create(
                {
                    "name": name,
                    "product_id": self.product.id,
                    "company_id": self.env.company.id,
                    "expiration_date": fields.Datetime.add(
                        fields.Datetime.now(),
                        days=days,
                    ),
                }
            )
            self.env["stock.quant"]._update_available_quantity(
                self.product,
                self.mirror_location,
                quantity,
                lot_id=lot,
            )

        dashboard = self.env["stock.quant"].get_elmis_inventory_dashboard(
            {
                "location_id": self.mirror_location.id,
                "search": "INV-TAB-001",
            }
        )
        row = dashboard["products"][0]

        self.assertEqual(
            row["expiry_quantities"],
            {
                "expired": 2,
                "days_0_30": 3,
                "days_31_60": 4,
                "days_61_90": 5,
            },
        )
        self.assertEqual(dashboard["summary"]["expired_quantity"], 2)
        self.assertEqual(dashboard["summary"]["expiring_0_30_quantity"], 3)
        self.assertEqual(dashboard["summary"]["expiring_31_60_quantity"], 4)
        self.assertEqual(dashboard["summary"]["expiring_61_90_quantity"], 5)

        filtered = self.env["stock.quant"].get_elmis_inventory_dashboard(
            {
                "location_id": self.mirror_location.id,
                "search": "INV-TAB-001",
                "status": "expiring_31_60",
            }
        )
        self.assertEqual(filtered["pagination"]["total"], 1)
        self.assertEqual(filtered["products"][0]["id"], self.product.id)

    def test_dashboard_pack_breakdown_preserves_canonical_units(self):
        quant_model = self.env["stock.quant"]

        self.assertEqual(
            quant_model._get_dashboard_pack_breakdown(25, 10),
            {
                "complete_packs": 2,
                "loose_units": 5,
            },
        )
        self.assertEqual(
            quant_model._get_dashboard_pack_breakdown(25.5, 10),
            {
                "complete_packs": 2,
                "loose_units": 5.5,
            },
        )
        self.assertEqual(
            quant_model._get_dashboard_pack_breakdown(7, 0),
            {
                "complete_packs": "",
                "loose_units": 7,
            },
        )
        self.assertEqual(
            quant_model._get_dashboard_pack_breakdown(-2, 10),
            {
                "complete_packs": "",
                "loose_units": -2,
            },
        )

    def test_inventory_export_uses_filters_and_outputs_batch_rows(self):
        self.env["stock.quant"]._update_available_quantity(
            self.product,
            self.mirror_location,
            27,
            lot_id=self.lot,
        )

        exported = self.env["stock.quant"].get_elmis_inventory_export_data(
            {
                "location_id": self.mirror_location.id,
                "search": "INV-TAB-001",
                "program_id": self.program_art.id,
                "status": "available",
            }
        )

        self.assertEqual(exported["facility_code"], "A2681-cp")
        self.assertEqual(len(exported["rows"]), 1)
        row = exported["rows"][0]
        self.assertEqual(row["product_code"], "INV-TAB-001")
        self.assertEqual(row["batch"], "INV-LOT-001")
        self.assertEqual(row["batch_stock_on_hand"], 27)
        self.assertEqual(row["pack_size"], 10)
        self.assertEqual(row["stock_on_hand_complete_packs"], 2)
        self.assertEqual(row["stock_on_hand_loose_units"], 7)
        self.assertEqual(row["batch_stock_on_hand_complete_packs"], 2)
        self.assertEqual(row["batch_stock_on_hand_loose_units"], 7)
        self.assertEqual(row["expiry_bucket"], "0-30 Days")

        zero_stock_export = self.env["stock.quant"].get_elmis_inventory_export_data(
            {
                "location_id": self.mirror_location.id,
                "search": "INV-OUT-001",
                "status": "out",
            }
        )
        self.assertEqual(len(zero_stock_export["rows"]), 1)
        self.assertEqual(zero_stock_export["rows"][0]["batch"], "")

    def test_batch_stock_card_reconciles_movements_to_current_quant(self):
        supplier = self.env.ref("stock.stock_location_suppliers")
        customer = self.env.ref("stock.stock_location_customers")
        receipt_line = self._create_done_move_line(
            supplier,
            self.mirror_location,
            20,
            fields.Datetime.add(fields.Datetime.now(), days=-2),
            "TEST/RECEIPT/001",
        )
        issue_line = self._create_done_move_line(
            self.mirror_location,
            customer,
            5,
            fields.Datetime.add(fields.Datetime.now(), days=-1),
            "TEST/ISSUE/001",
        )
        self.env["stock.quant"]._update_available_quantity(
            self.product,
            self.mirror_location,
            15,
            lot_id=self.lot,
        )
        self.env["elmis.outbox"].create(
            {
                "transaction_type": "ADJUSTMENT",
                "facility_code": self.mirror_location.elmis_facility_code,
                "program_id": self.program_art.id,
                "elmis_orderable_id": self.product.id,
                "lot_id": self.lot.id,
                "source_stock_move_line_id": issue_line.id,
                "stock_move_line_side": "source",
                "quantity": 5,
                "uom_id": self.unit_uom.id,
                "adjustment_reason": "Transfer Out",
                "status": "DELIVERED",
            }
        )

        card = self.env["stock.quant"].get_elmis_stock_card(
            {
                "product_id": self.product.id,
                "lot_id": self.lot.id,
                "location_id": self.mirror_location.id,
            }
        )

        self.assertEqual(card["summary"]["opening_balance"], 0)
        self.assertEqual(card["summary"]["quantity_in"], 20)
        self.assertEqual(card["summary"]["quantity_out"], 5)
        self.assertEqual(card["summary"]["current_balance"], 15)
        self.assertEqual(card["summary"]["movement_count"], 2)
        newest, oldest = card["rows"]
        self.assertEqual(newest["id"], issue_line.id)
        self.assertEqual(newest["balance_after"], 15)
        self.assertEqual(newest["elmis_status"], "delivered")
        self.assertEqual(newest["reason"], "Transfer Out")
        self.assertEqual(oldest["id"], receipt_line.id)
        self.assertEqual(oldest["balance_after"], 20)
        self.assertEqual(oldest["movement_type"], "receipt")

    def test_batch_stock_card_filters_after_calculating_running_balance(self):
        supplier = self.env.ref("stock.stock_location_suppliers")
        customer = self.env.ref("stock.stock_location_customers")
        old_date = fields.Datetime.add(fields.Datetime.now(), days=-10)
        recent_date = fields.Datetime.add(fields.Datetime.now(), days=-1)
        self._create_done_move_line(
            supplier,
            self.mirror_location,
            30,
            old_date,
            "TEST/RECEIPT/002",
        )
        issue_line = self._create_done_move_line(
            self.mirror_location,
            customer,
            7,
            recent_date,
            "TEST/ISSUE/002",
        )
        self.env["stock.quant"]._update_available_quantity(
            self.product,
            self.mirror_location,
            23,
            lot_id=self.lot,
        )

        card = self.env["stock.quant"].get_elmis_stock_card(
            {
                "product_id": self.product.id,
                "lot_id": self.lot.id,
                "location_id": self.mirror_location.id,
                "date_from": fields.Date.to_string(
                    fields.Date.add(fields.Date.today(), days=-2)
                ),
            }
        )

        self.assertEqual(card["summary"]["movement_count"], 1)
        self.assertEqual(card["summary"]["opening_balance"], 30)
        self.assertEqual(card["summary"]["current_balance"], 23)
        self.assertEqual(card["rows"][0]["id"], issue_line.id)
        self.assertEqual(card["rows"][0]["balance_before"], 30)
        self.assertEqual(card["rows"][0]["balance_after"], 23)

    def test_quant_opens_stock_card_for_its_batch_and_location(self):
        self.env["stock.quant"]._update_available_quantity(
            self.product,
            self.mirror_location,
            1,
            lot_id=self.lot,
        )
        quant = self.env["stock.quant"].search(
            [
                ("product_id", "=", self.product.id),
                ("lot_id", "=", self.lot.id),
                ("location_id", "=", self.mirror_location.id),
            ],
            limit=1,
        )

        action = quant.action_open_elmis_batch_stock_card()

        self.assertEqual(
            action["tag"],
            "lesotho_elmis_integration.stock_card",
        )
        self.assertEqual(action["context"]["product_id"], self.product.id)
        self.assertEqual(action["context"]["lot_id"], self.lot.id)
        self.assertEqual(
            action["context"]["location_id"],
            self.mirror_location.id,
        )

    def _create_done_move_line(
        self,
        source,
        destination,
        quantity,
        movement_date,
        reference,
    ):
        move = self.env["stock.move"].create(
            {
                "name": reference,
                "reference": reference,
                "product_id": self.product.id,
                "product_uom_qty": quantity,
                "product_uom": self.unit_uom.id,
                "location_id": source.id,
                "location_dest_id": destination.id,
                "company_id": self.env.company.id,
            }
        )
        line = self.env["stock.move.line"].create(
            {
                "move_id": move.id,
                "product_id": self.product.id,
                "product_uom_id": self.unit_uom.id,
                "location_id": source.id,
                "location_dest_id": destination.id,
                "lot_id": self.lot.id,
                "qty_done": quantity,
                "date": movement_date,
                "company_id": self.env.company.id,
            }
        )
        move.write({"state": "done"})
        return line
