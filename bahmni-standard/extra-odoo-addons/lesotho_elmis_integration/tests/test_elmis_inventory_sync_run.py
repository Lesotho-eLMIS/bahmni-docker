from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestElmisInventorySyncRun(TransactionCase):
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
        cls.sync_service = cls.env["elmis.inventory.sync"]

    def _set_config(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("lesotho_elmis_integration.base_url", "https://dev.elmis.gov.ls/api/")
        params.set_param("lesotho_elmis_integration.program_codes", "art")
        params.set_param("lesotho_elmis_integration.api_token", "test-token")
        params.set_param("lesotho_elmis_integration.mirror_location_id", str(self.mirror_location.id))

    def test_connection_wrapper_creates_successful_run(self):
        self._set_config()

        def fake_get_json(service, base_url, path, token, query=None):
            if path == "facilities/full":
                return {"content": [{"id": "facility-id", "code": "A2681-cp"}]}
            if path == "programs":
                return [{"id": "program-id", "code": "art"}]
            if path == "v2/stockCardSummariesResolv":
                return {
                    "content": [
                        {
                            "canFulfillForMe": [
                                {"stockOnHand": 11, "active": True},
                                {"stockOnHand": 0, "active": False},
                            ],
                        }
                    ]
                }
            self.fail("Unexpected eLMIS path: %s" % path)

        with patch.object(type(self.sync_service), "_elmis_get_json", fake_get_json):
            result, run = self.sync_service.test_configured_connection_with_run()

        self.assertEqual(result["stock_entries_found"], 1)
        self.assertEqual(run.operation, "test_connection")
        self.assertEqual(run.status, "success")
        self.assertEqual(run.location_id, self.mirror_location)
        self.assertEqual(run.facility_code, "A2681-cp")
        self.assertEqual(run.facility_id, "facility-id")
        self.assertEqual(run.program_codes, "art")
        self.assertEqual(run.programs_resolved, 1)
        self.assertEqual(run.stock_entries_found, 1)
        self.assertTrue(run.finished_at)

    def test_failed_connection_wrapper_records_error(self):
        self._set_config()

        def fake_test_connection(service):
            raise UserError("Connection denied")

        with patch.object(
            type(self.sync_service),
            "test_configured_connection",
            fake_test_connection,
        ):
            try:
                self.sync_service.test_configured_connection_with_run()
            except UserError:
                pass
            else:
                self.fail("Expected a UserError.")

        run = self.env["elmis.inventory.sync.run"].search(
            [("operation", "=", "test_connection")],
            limit=1,
        )
        self.assertEqual(run.status, "failed")
        self.assertIn("Connection denied", run.error_message)
        self.assertTrue(run.finished_at)

    def test_sync_status_reports_cron_and_last_success(self):
        cron = self.env.ref("lesotho_elmis_integration.ir_cron_elmis_inventory_sync")
        cron.write(
            {
                "active": True,
                "interval_number": 6,
                "interval_type": "hours",
            }
        )
        run = self.env["elmis.inventory.sync.run"].create(
            {
                "operation": "inventory_sync",
                "facility_code": "A2681-cp",
                "items_processed": 4,
            }
        )
        run.mark_success(
            {
                "facility_code": "A2681-cp",
                "items_processed": 4,
                "products_created": 2,
                "lots_created": 1,
                "quants_updated": 4,
            }
        )

        status = self.sync_service.get_sync_status()

        self.assertTrue(status["enabled"])
        self.assertEqual(status["interval_number"], 6)
        self.assertEqual(status["interval_type"], "hours")
        self.assertEqual(status["last_success"]["items_processed"], 4)
        self.assertEqual(status["last_run"]["status"], "success")
        self.assertIn("outbox", status)
        self.assertIn("pending", status["outbox"])
        self.assertIn("failed", status["outbox"])
