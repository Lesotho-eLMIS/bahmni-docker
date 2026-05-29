from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestElmisInventorySync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.mirror_location = cls.env["stock.location"].create(
            {
                "name": "Clinical Pharmacy",
                "usage": "internal",
                "location_id": cls.stock_location.id,
                "elmis_facility_code": "A2681-cp",
            }
        )
        cls.sync_service = cls.env["elmis.inventory.sync"]

    def _snapshot(self, stock_on_hand=25):
        return {
            "facilityDhis2Code": "A2681-cp",
            "items": [
                {
                    "productCode": "TEST-DON-PAR004-TAB001-1000",
                    "fullName": "Paracetamol 500Mg Tablets 1000",
                    "genericName": "Paracetamol",
                    "strength": "500Mg",
                    "dosageForm": "tablets",
                    "packSize": 1000,
                    "packSizeUnit": "tablets",
                    "dispensableUnit": "each",
                    "dispensableUnitFactor": 1,
                    "programCode": "art",
                    "programName": "ART",
                    "lot": "TEST-LOT-2026-001",
                    "expirationDate": "2027-12-31",
                    "stockOnHand": stock_on_hand,
                }
            ],
        }

    def test_sync_creates_only_snapshot_product_lot_and_quant(self):
        result = self.sync_service.sync_inventory_snapshot(self._snapshot())

        self.assertEqual(result["items_processed"], 1)
        self.assertEqual(result["products_created"], 1)
        self.assertEqual(result["lots_created"], 1)
        self.assertEqual(result["quants_updated"], 1)

        product = self.env["product.product"].search(
            [("elmis_product_code", "=", "TEST-DON-PAR004-TAB001-1000")]
        )
        self.assertEqual(len(product), 1)
        self.assertTrue(product.is_elmis_product)
        self.assertEqual(product.elmis_program_ids.mapped("code"), ["art"])
        self.assertEqual(product.tracking, "lot")

        lot = self.env["stock.lot"].search(
            [
                ("product_id", "=", product.id),
                ("elmis_lot_number", "=", "TEST-LOT-2026-001"),
            ]
        )
        self.assertEqual(len(lot), 1)

        quant = self.env["stock.quant"].search(
            [
                ("product_id", "=", product.id),
                ("location_id", "=", result["location_id"]),
                ("lot_id", "=", lot.id),
            ]
        )
        self.assertEqual(sum(quant.mapped("quantity")), 25.0)
        self.assertEqual(quant.elmis_program_ids.mapped("code"), ["art"])

    def test_sync_sets_absolute_stock_on_hand(self):
        self.sync_service.sync_inventory_snapshot(self._snapshot(stock_on_hand=25))
        result = self.sync_service.sync_inventory_snapshot(self._snapshot(stock_on_hand=7))

        product = self.env["product.product"].search(
            [("elmis_product_code", "=", "TEST-DON-PAR004-TAB001-1000")]
        )
        lot = self.env["stock.lot"].search(
            [
                ("product_id", "=", product.id),
                ("elmis_lot_number", "=", "TEST-LOT-2026-001"),
            ]
        )
        quant = self.env["stock.quant"].search(
            [
                ("product_id", "=", product.id),
                ("location_id", "=", result["location_id"]),
                ("lot_id", "=", lot.id),
            ]
        )

        self.assertEqual(sum(quant.mapped("quantity")), 7.0)

    def test_sync_accumulates_programs_for_existing_product(self):
        payload = self._snapshot(stock_on_hand=25)
        payload["items"][0]["programCode"] = "art"
        self.sync_service.sync_inventory_snapshot(payload)

        payload = self._snapshot(stock_on_hand=7)
        payload["items"][0]["programCode"] = "em"
        payload["items"][0]["programName"] = "EM"
        self.sync_service.sync_inventory_snapshot(payload)

        product = self.env["product.product"].search(
            [("elmis_product_code", "=", "TEST-DON-PAR004-TAB001-1000")]
        )

        self.assertEqual(set(product.elmis_program_ids.mapped("code")), {"art", "em"})

    def test_sync_requires_configured_mirror_location(self):
        payload = self._snapshot()
        payload["facilityDhis2Code"] = "UNKNOWN"

        with self.assertRaises(UserError):
            self.sync_service.sync_inventory_snapshot(payload)

    def test_build_elmis_url_adds_query_parameters(self):
        url = self.sync_service._build_elmis_url(
            "https://dev.elmis.gov.ls/api/",
            "v2/stockCardSummariesResolv",
            {"facilityId": "facility-id", "programId": ["art-id", "em-id"]},
        )

        self.assertEqual(
            url,
            "https://dev.elmis.gov.ls/api/v2/stockCardSummariesResolv"
            "?facilityId=facility-id&programId=art-id&programId=em-id",
        )

    def test_chunked_splits_large_batches(self):
        chunks = list(self.sync_service._chunked(list(range(105)), 50))

        self.assertEqual(len(chunks), 3)
        self.assertEqual(len(chunks[0]), 50)
        self.assertEqual(len(chunks[1]), 50)
        self.assertEqual(len(chunks[2]), 5)

    def test_sync_facility_inventory_fetches_and_normalizes_elmis_stock(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("elmis_integration.base_url", "https://dev.elmis.gov.ls/api/")
        params.set_param("elmis_integration.program_codes", "art")
        params.set_param("elmis_integration.api_token", "test-token")

        def fake_get_json(service, base_url, path, token, query=None):
            self.assertEqual(base_url, "https://dev.elmis.gov.ls/api/")
            self.assertEqual(token, "test-token")
            if path == "facilities/full":
                self.assertEqual(query["code"], "A2681-cp")
                return {"content": [{"id": "facility-id", "code": "A2681-cp"}]}
            if path == "programs":
                self.assertEqual(query["code"], ["art"])
                return [{"id": "program-id", "code": "art", "name": "ART"}]
            if path == "v2/stockCardSummariesResolv":
                self.assertEqual(query["facilityId"], "facility-id")
                self.assertEqual(query["programId"], "program-id")
                return {
                    "content": [
                        {
                            "orderable": {"id": "orderable-id"},
                            "canFulfillForMe": [
                                {
                                    "orderable": {"id": "orderable-id"},
                                    "lot": {"id": "lot-id"},
                                    "stockOnHand": 11,
                                    "active": True,
                                    "orderableName": "Tenofovir 300mg Tablets",
                                    "lotCode": "LOT-ART-001",
                                    "lotExpirationDate": "2027-06-30",
                                }
                            ],
                        }
                    ]
                }
            if path == "orderables":
                self.assertEqual(query["id"], ["orderable-id"])
                return {
                    "content": [
                        {
                            "id": "orderable-id",
                            "productCode": "ARV-TDF300-TAB",
                            "fullProductName": "Tenofovir 300mg Tablets",
                            "netContent": 30,
                            "dispensable": {"displayUnit": "tablet", "size": 1},
                            "extraData": {
                                "genericName": "Tenofovir",
                                "strength": "300mg",
                                "dosageForm": "tablets",
                            },
                        }
                    ]
                }
            self.fail("Unexpected eLMIS path: %s" % path)

        with patch.object(type(self.sync_service), "_elmis_get_json", fake_get_json):
            result = self.sync_service.sync_facility_inventory("A2681-cp")

        self.assertEqual(result["items_processed"], 1)
        product = self.env["product.product"].search(
            [("elmis_orderable_id", "=", "orderable-id")]
        )
        self.assertEqual(len(product), 1)
        self.assertEqual(product.elmis_product_code, "ARV-TDF300-TAB")
        self.assertEqual(product.elmis_program_ids.mapped("code"), ["art"])
        self.assertEqual(product.elmis_program_ids.elmis_program_id, "program-id")

        lot = self.env["stock.lot"].search([("elmis_lot_id", "=", "lot-id")])
        self.assertEqual(len(lot), 1)
        self.assertEqual(lot.name, "LOT-ART-001")

    def test_sync_facility_inventory_can_limit_items_for_trial_run(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("elmis_integration.base_url", "https://dev.elmis.gov.ls/api/")
        params.set_param("elmis_integration.program_codes", "art")
        params.set_param("elmis_integration.api_token", "test-token")

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
                                {
                                    "orderable": {"id": "orderable-1"},
                                    "lot": {"id": "lot-1"},
                                    "stockOnHand": 11,
                                    "active": True,
                                    "orderableName": "Tenofovir 300mg Tablets",
                                    "lotCode": "LOT-ART-001",
                                },
                                {
                                    "orderable": {"id": "orderable-2"},
                                    "lot": {"id": "lot-2"},
                                    "stockOnHand": 22,
                                    "active": True,
                                    "orderableName": "Lamivudine 150mg Tablets",
                                    "lotCode": "LOT-ART-002",
                                },
                            ],
                        }
                    ]
                }
            if path == "orderables":
                self.assertEqual(query["id"], ["orderable-1"])
                return {
                    "content": [
                        {
                            "id": "orderable-1",
                            "productCode": "ARV-TDF300-TAB",
                            "fullProductName": "Tenofovir 300mg Tablets",
                            "dispensable": {"displayUnit": "tablet", "size": 1},
                        }
                    ]
                }
            self.fail("Unexpected eLMIS path: %s" % path)

        with patch.object(type(self.sync_service), "_elmis_get_json", fake_get_json):
            result = self.sync_service.sync_facility_inventory("A2681-cp", item_limit=1)

        self.assertEqual(result["items_processed"], 1)
        self.assertTrue(
            self.env["product.product"].search([("elmis_orderable_id", "=", "orderable-1")])
        )
        self.assertFalse(
            self.env["product.product"].search([("elmis_orderable_id", "=", "orderable-2")])
        )

    def test_configured_connection_fetches_counts_without_creating_inventory(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("elmis_integration.base_url", "https://dev.elmis.gov.ls/api/")
        params.set_param("elmis_integration.program_codes", "art")
        params.set_param("elmis_integration.api_token", "test-token")
        params.set_param("elmis_integration.mirror_location_id", str(self.mirror_location.id))

        def fake_get_json(service, base_url, path, token, query=None):
            if path == "facilities/full":
                self.assertEqual(query["code"], "A2681-cp")
                return {"content": [{"id": "facility-id", "code": "A2681-cp"}]}
            if path == "programs":
                self.assertEqual(query["code"], ["art"])
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
            if path.startswith("orderables/"):
                self.fail("Dry-run connection test must not fetch orderable details.")
            self.fail("Unexpected eLMIS path: %s" % path)

        with patch.object(type(self.sync_service), "_elmis_get_json", fake_get_json):
            result = self.sync_service.test_configured_connection()

        self.assertEqual(result["facility_code"], "A2681-cp")
        self.assertEqual(result["program_codes"], ["art"])
        self.assertEqual(result["programs_resolved"], 1)
        self.assertEqual(result["stock_entries_found"], 1)
        product = self.env["product.product"].search(
            [("elmis_orderable_id", "=", "orderable-id")]
        )
        self.assertFalse(product)
