from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestElmisConfigSettings(TransactionCase):
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

    def test_default_non_secret_config_parameters_are_available(self):
        params = self.env["ir.config_parameter"].sudo()

        self.assertEqual(
            params.get_param("elmis_integration.base_url"),
            "https://dev.elmis.gov.ls/api/",
        )
        self.assertEqual(
            params.get_param("elmis_integration.program_codes"),
            "art,em,lab,ois",
        )
        self.assertEqual(
            params.get_param("elmis_integration.sync_interval_number"),
            "6",
        )
        self.assertEqual(
            params.get_param("elmis_integration.sync_interval_type"),
            "hours",
        )

    def test_settings_can_store_credentials_and_mirror_location_for_local_testing(self):
        settings = self.env["res.config.settings"].create(
            {
                "elmis_username": "test-user",
                "elmis_password": "test-password",
                "elmis_api_token": "test-token",
                "elmis_mirror_location_id": self.mirror_location.id,
            }
        )

        settings.execute()

        params = self.env["ir.config_parameter"].sudo()
        self.assertEqual(params.get_param("elmis_integration.username"), "test-user")
        self.assertEqual(params.get_param("elmis_integration.password"), "test-password")
        self.assertEqual(params.get_param("elmis_integration.api_token"), "test-token")
        self.assertEqual(
            params.get_param("elmis_integration.mirror_location_id"),
            str(self.mirror_location.id),
        )

    def test_settings_can_configure_scheduled_sync_cron(self):
        cron = self.env.ref("elmis_integration.ir_cron_elmis_inventory_sync")
        cron.write({"active": False, "interval_number": 6, "interval_type": "hours"})
        settings = self.env["res.config.settings"].create(
            {
                "elmis_sync_cron_active": True,
                "elmis_sync_interval_number": 30,
                "elmis_sync_interval_type": "minutes",
            }
        )

        settings.execute()

        self.assertTrue(cron.active)
        self.assertEqual(cron.interval_number, 30)
        self.assertEqual(cron.interval_type, "minutes")
        self.assertTrue(cron.nextcall)

    def test_settings_test_connection_button_returns_notification(self):
        settings = self.env["res.config.settings"].create({})

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
                "program_codes": ["art", "em"],
                "programs_resolved": 2,
                "stock_entries_found": 7,
            }, run

        with patch.object(
            type(self.env["elmis.inventory.sync"]),
            "test_configured_connection_with_run",
            fake_test_connection,
        ):
            action = settings.action_elmis_test_connection()

        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("Facility: A2681-cp", action["params"]["message"])
        self.assertIn("active stock rows found: 7", action["params"]["message"])

    def test_settings_sync_button_returns_notification(self):
        settings = self.env["res.config.settings"].create({})

        def fake_sync(service):
            run = self.env["elmis.inventory.sync.run"].create(
                {
                    "operation": "inventory_sync",
                    "facility_code": "A2681-cp",
                }
            )
            return {
                "items_processed": 3,
                "products_created": 2,
                "lots_created": 1,
                "quants_updated": 3,
            }, run

        with patch.object(
            type(self.env["elmis.inventory.sync"]),
            "sync_configured_facility_inventory_with_run",
            fake_sync,
        ):
            action = settings.action_elmis_sync_inventory()

        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("Items: 3", action["params"]["message"])
