"""Database-free regression tests for eLMIS inventory requests."""
import socket
from types import MethodType, SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from odoo.exceptions import UserError

from ..models import elmis_inventory_sync as sync_module


class TestElmisInventoryHttp(TestCase):
    def setUp(self):
        self.service = SimpleNamespace(_elmis_get_json=Mock())
        for name in ("_extract_page_content", "_build_elmis_url"):
            setattr(self.service, name, MethodType(
                getattr(sync_module.ElmisInventorySync, name), self.service,
            ))

    def fetch_summaries(self):
        return sync_module.ElmisInventorySync._get_elmis_stock_card_summaries(
            self.service, "https://example.invalid/api/", "test-token", "facility", "program",
        )

    def request_json(self):
        return sync_module.ElmisInventorySync._elmis_request_json(
            self.service, "https://example.invalid/api/", "v2/stockCardSummaries",
        )

    def test_collects_all_pages_using_metadata_even_when_page_is_short(self):
        self.service._elmis_get_json.side_effect = [
            {"content": [{"id": "first"}], "number": 0, "last": False},
            {"content": [{"id": "second"}], "number": 1, "last": True},
        ]
        self.assertEqual(self.fetch_summaries(), [{"id": "first"}, {"id": "second"}])
        calls = self.service._elmis_get_json.call_args_list
        self.assertEqual(len(calls), 2)
        for page, call in enumerate(calls):
            self.assertEqual(call.args[3], {
                "facilityId": "facility", "programId": "program",
                "nonEmptyOnly": "true", "size": 20, "page": page,
            })

    def test_empty_last_page_returns_no_stock(self):
        self.service._elmis_get_json.return_value = {
            "content": [], "number": 0, "last": True, "totalPages": 0,
        }
        self.assertEqual(self.fetch_summaries(), [])
        self.service._elmis_get_json.assert_called_once()

    def test_uses_total_pages_when_last_flag_is_absent(self):
        self.service._elmis_get_json.side_effect = [
            {"content": [1], "totalPages": 2},
            {"content": [2], "totalPages": 2},
        ]
        self.assertEqual(self.fetch_summaries(), [1, 2])

    def test_uses_page_length_when_metadata_is_absent(self):
        self.service._elmis_get_json.side_effect = [
            {"content": list(range(20))}, {"content": [20]},
        ]
        self.assertEqual(self.fetch_summaries(), list(range(21)))

    def test_retains_unpaged_list_support(self):
        self.service._elmis_get_json.return_value = [{"id": "first"}]
        self.assertEqual(self.fetch_summaries(), [{"id": "first"}])
        self.service._elmis_get_json.assert_called_once()

    def test_rejects_repeated_page_instead_of_looping(self):
        self.service._elmis_get_json.side_effect = [
            {"content": [1], "number": 0, "last": False},
            {"content": [1], "number": 0, "last": False},
        ]
        with self.assertRaisesRegex(UserError, "unexpected stock summary page"):
            self.fetch_summaries()

    def test_rejects_empty_nonfinal_page_instead_of_returning_partial_stock(self):
        self.service._elmis_get_json.return_value = {"content": [], "last": False}
        with self.assertRaisesRegex(UserError, "empty stock summary page"):
            self.fetch_summaries()

    def test_later_page_failure_does_not_return_partial_stock(self):
        self.service._elmis_get_json.side_effect = [
            {"content": [1], "last": False}, UserError("eLMIS unavailable"),
        ]
        with self.assertRaisesRegex(UserError, "eLMIS unavailable"):
            self.fetch_summaries()

    def test_timeout_waiting_for_headers_is_readable(self):
        for timeout in (socket.timeout, TimeoutError):
            with self.subTest(timeout=timeout), patch.object(
                sync_module.request, "urlopen", side_effect=timeout("timed out"),
            ) as urlopen:
                with self.assertRaisesRegex(UserError, "within 60 seconds.*stockCardSummaries"):
                    self.request_json()
                self.assertEqual(urlopen.call_args.kwargs["timeout"], 60)
                self.assertEqual(urlopen.call_count, 2)

    def test_timeout_reading_body_is_readable(self):
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = socket.timeout("timed out")
        with patch.object(sync_module.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(UserError, "within 60 seconds"):
                self.request_json()

    def test_successful_json_response_is_unchanged(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"content": []}'
        with patch.object(sync_module.request, "urlopen", return_value=response):
            self.assertEqual(self.request_json(), {"content": []})

    def test_standard_endpoint_is_used(self):
        self.service._elmis_get_json.return_value = {"content": [], "last": True}
        self.fetch_summaries()
        self.assertEqual(self.service._elmis_get_json.call_args.args[1], "v2/stockCardSummaries")

    def test_lot_lookup_batches_ids_and_keeps_expiry_dates(self):
        self.service._chunked = MethodType(sync_module.ElmisInventorySync._chunked, self.service)
        ids = [str(i) for i in range(51)]
        self.service._elmis_get_json.side_effect = lambda base, path, token, query: {
            "content": [{"id": i, "lotCode": "BATCH-" + i, "expirationDate": "2027-01-31"}
                        for i in query["id"]],
        }
        lots = sync_module.ElmisInventorySync._get_elmis_lots(
            self.service, "https://example.invalid/api/", "test-token", ids,
        )
        self.assertEqual(set(lots), set(ids))
        self.assertEqual(lots["50"]["expirationDate"], "2027-01-31")
        calls = self.service._elmis_get_json.call_args_list
        self.assertEqual([call.args[3]["size"] for call in calls], [50, 1])
        self.assertTrue(all(call.args[1] == "lots" for call in calls))

    def test_missing_batch_details_stop_import(self):
        self.service._chunked = MethodType(sync_module.ElmisInventorySync._chunked, self.service)
        for lots in ([], [{"id": "lot-1", "lotCode": None}]):
            with self.subTest(lots=lots):
                self.service._elmis_get_json.return_value = {"content": lots}
                with self.assertRaisesRegex(UserError, "without batch numbers"):
                    sync_module.ElmisInventorySync._get_elmis_lots(
                        self.service, "https://example.invalid/api/", "test-token", ["lot-1"],
                    )

    def test_normalization_resolves_unique_active_lots_without_mutating_input(self):
        self.service._get_elmis_lots = Mock(return_value={
            "lot-1": {"id": "lot-1", "lotCode": "BATCH-1", "expirationDate": "2027-01-31"},
        })
        self.service._normalize_stock_card_entry = MethodType(
            sync_module.ElmisInventorySync._normalize_stock_card_entry, self.service,
        )
        entry = {"orderable": {"id": "product-1"}, "lot": {"id": "lot-1"},
                 "stockOnHand": 17, "active": True}
        summaries = [{"canFulfillForMe": [entry, dict(entry, stockOnHand=0),
                                          dict(entry, active=False, lot={"id": "inactive-lot"})]}]
        items = sync_module.ElmisInventorySync._normalize_stock_card_summaries(
            self.service, "https://example.invalid/api/", "test-token", summaries,
            {"product-1": {"productCode": "CODE", "fullProductName": "Product"}},
            program={"id": "program-1", "code": "art"},
        )
        self.service._get_elmis_lots.assert_called_once_with(
            "https://example.invalid/api/", "test-token", ["lot-1"],
        )
        self.assertEqual([item["stockOnHand"] for item in items], [17, 0])
        self.assertTrue(all(item["lot"] == "BATCH-1" for item in items))
        self.assertTrue(all(item["expirationDate"] == "2027-01-31" for item in items))
        self.assertTrue(all(item["programCode"] == "art" for item in items))
        self.assertNotIn("lotCode", entry)

    def test_item_limit_avoids_fetching_unused_lots(self):
        self.service._get_elmis_lots = Mock(return_value={"lot-1": {"lotCode": "BATCH-1"}})
        self.service._normalize_stock_card_entry = MethodType(
            sync_module.ElmisInventorySync._normalize_stock_card_entry, self.service,
        )
        summaries = [{"orderable": {"id": "product-1"}, "canFulfillForMe": [
            {"lot": {"id": "lot-1"}, "stockOnHand": 2},
            {"lot": {"id": "lot-2"}, "stockOnHand": 3},
        ]}]
        items = sync_module.ElmisInventorySync._normalize_stock_card_summaries(
            self.service, "https://example.invalid/api/", "test-token", summaries,
            {"product-1": {"productCode": "CODE"}}, item_limit=1,
        )
        self.assertEqual(len(items), 1)
        self.service._get_elmis_lots.assert_called_once_with(
            "https://example.invalid/api/", "test-token", ["lot-1"],
        )

    def test_existing_enriched_batch_details_are_preserved(self):
        self.service._get_elmis_lots = Mock()
        self.service._normalize_stock_card_entry = MethodType(
            sync_module.ElmisInventorySync._normalize_stock_card_entry, self.service,
        )
        summaries = [{"orderable": {"id": "product-1"}, "canFulfillForMe": [
            {"lot": {"id": "lot-1"}, "lotCode": "BATCH-1", "stockOnHand": 2},
            {"lot": None, "stockOnHand": 3},
        ]}]
        items = sync_module.ElmisInventorySync._normalize_stock_card_summaries(
            self.service, "https://example.invalid/api/", "test-token", summaries,
            {"product-1": {"productCode": "CODE"}},
        )
        self.assertEqual(items[0]["lot"], "BATCH-1")
        self.assertIsNone(items[1]["lot"])
        self.service._get_elmis_lots.assert_not_called()

    def test_transient_stock_timeout_is_retried_once(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"content": []}'
        for timeout in (socket.timeout("timed out"),
                        sync_module.error.URLError(socket.timeout("timed out"))):
            with self.subTest(timeout=type(timeout)), patch.object(
                sync_module.request, "urlopen", side_effect=[timeout, response],
            ) as urlopen:
                self.assertEqual(self.request_json(), {"content": []})
                self.assertEqual(urlopen.call_count, 2)

    def test_post_timeout_is_not_retried(self):
        with patch.object(sync_module.request, "urlopen", side_effect=socket.timeout("timed out")) as urlopen:
            with self.assertRaises(UserError):
                sync_module.ElmisInventorySync._elmis_request_json(
                    self.service, "https://example.invalid/api/", "stockEvents",
                    method="POST", data=b'{}',
                )
            urlopen.assert_called_once()

    def test_http_authentication_error_is_not_retried(self):
        from io import BytesIO
        error = sync_module.error.HTTPError(
            "https://example.invalid/api/", 401, "Unauthorized", {}, BytesIO(b'Invalid token'),
        )
        with patch.object(sync_module.request, "urlopen", side_effect=error) as urlopen:
            with self.assertRaisesRegex(UserError, "401 Unauthorized"):
                self.request_json()
            urlopen.assert_called_once()
