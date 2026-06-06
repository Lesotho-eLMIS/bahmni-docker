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

    def test_location_inventory_menu_action_is_read_only_inventory_browser(self):
        operations_menu = self.env.ref("stock.menu_stock_warehouse_mgmt")
        menu = self.env.ref("lesotho_elmis_integration.menu_location_inventory")
        action = self.env.ref("lesotho_elmis_integration.action_location_inventory")
        tree_view = self.env.ref("lesotho_elmis_integration.view_stock_quant_tree_location_inventory")
        pivot_view = self.env.ref("lesotho_elmis_integration.view_stock_quant_pivot_location_inventory")
        graph_view = self.env.ref("lesotho_elmis_integration.view_stock_quant_graph_location_inventory")

        self.assertEqual(menu.parent_id, operations_menu)
        self.assertEqual(action.res_model, "stock.quant")
        self.assertEqual(action.view_mode, "tree,pivot,graph")
        self.assertIn("location_id.usage", action.domain)
        self.assertIn("quantity", action.domain)
        self.assertIn("search_default_elmis_products", action.context)
        self.assertIn("search_default_elmis_mirror_locations", action.context)
        self.assertIn('create="false"', tree_view.arch_db)
        self.assertIn('edit="false"', tree_view.arch_db)
        self.assertIn('delete="false"', tree_view.arch_db)
        self.assertIn("elmis_stock_status", tree_view.arch_db)
        self.assertIn("elmis_expiry_status", tree_view.arch_db)
        self.assertIn("quantity", pivot_view.arch_db)
        self.assertIn("location_id", graph_view.arch_db)
