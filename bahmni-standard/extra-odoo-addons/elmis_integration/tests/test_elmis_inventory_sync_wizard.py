from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestElmisInventorySyncWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mirror_location = cls.env["stock.location"].create(
            {
                "name": "Clinical Pharmacy",
                "usage": "internal",
                "location_id": cls.env.ref("stock.stock_location_stock").id,
                "elmis_facility_code": "A2681-cp",
            }
        )
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param("elmis_integration.base_url", "https://dev.elmis.gov.ls/api/")
        params.set_param("elmis_integration.program_codes", "art,em")
        params.set_param("elmis_integration.api_token", "test-token")
        params.set_param("elmis_integration.mirror_location_id", str(cls.mirror_location.id))

    def test_wizard_defaults_show_configured_scope(self):
        wizard = self.env["elmis.inventory.sync.wizard"].create({})

        self.assertEqual(wizard.mirror_location_id, self.mirror_location)
        self.assertEqual(wizard.facility_code, "A2681-cp")
        self.assertEqual(wizard.program_codes, "art, em")

    def test_wizard_buttons_return_notifications(self):
        wizard = self.env["elmis.inventory.sync.wizard"].create({})

        def fake_test_connection(service):
            run = self.env["elmis.inventory.sync.run"].create(
                {
                    "operation": "test_connection",
                    "facility_code": "A2681-cp",
                }
            )
            return {
                "facility_code": "A2681-cp",
                "facility_id": "facility-id",
                "program_codes": ["art"],
                "programs_resolved": 1,
                "stock_entries_found": 5,
            }, run

        def fake_sync(service):
            run = self.env["elmis.inventory.sync.run"].create(
                {
                    "operation": "inventory_sync",
                    "facility_code": "A2681-cp",
                }
            )
            return {
                "items_processed": 3,
                "products_created": 1,
                "lots_created": 2,
                "quants_updated": 3,
            }, run

        sync_service = self.env["elmis.inventory.sync"]
        with patch.object(
            type(sync_service),
            "test_configured_connection_with_run",
            fake_test_connection,
        ):
            test_action = wizard.action_test_connection()
        with patch.object(
            type(sync_service),
            "sync_configured_facility_inventory_with_run",
            fake_sync,
        ):
            sync_action = wizard.action_sync_inventory()

        self.assertEqual(test_action["tag"], "display_notification")
        self.assertIn("active stock rows found: 5", test_action["params"]["message"])
        self.assertEqual(sync_action["tag"], "display_notification")
        self.assertIn("Items: 3", sync_action["params"]["message"])

    def test_sync_menus_live_under_inventory_operations(self):
        operations_menu = self.env.ref("stock.menu_stock_warehouse_mgmt")
        trigger_menu = self.env.ref("elmis_integration.menu_elmis_inventory_sync_wizard")
        history_menu = self.env.ref("elmis_integration.menu_elmis_inventory_sync_runs")

        self.assertEqual(trigger_menu.parent_id, operations_menu)
        self.assertEqual(history_menu.parent_id, operations_menu)
