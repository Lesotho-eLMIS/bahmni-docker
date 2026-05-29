import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestElmisOutbox(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.program = cls.env.ref("elmis_integration.elmis_program_art")
        cls.product = cls._create_elmis_product()
        cls.lot = cls.env["stock.lot"].create(
            {
                "name": "LOT-ART-001",
                "product_id": cls.product.id,
                "company_id": cls.env.company.id,
                "elmis_lot_id": "55555555-5555-5555-5555-555555555555",
                "elmis_lot_number": "LOT-ART-001",
            }
        )

    @classmethod
    def _create_elmis_product(cls):
        template = cls.env["product.template"].create(
            {
                "name": "Tenofovir 300mg Tablets",
                "detailed_type": "product",
                "uom_id": cls.unit_uom.id,
                "uom_po_id": cls.unit_uom.id,
                "list_price": 1.0,
            }
        )
        product = template.product_variant_id
        product.write(
            {
                "is_elmis_product": True,
                "elmis_orderable_id": "66666666-6666-6666-6666-666666666666",
                "elmis_product_code": "ARV-TDF300-TAB",
                "elmis_program_ids": [(4, cls.program.id)],
            }
        )
        return product

    def _create_outbox(self, **overrides):
        vals = {
            "transaction_type": "DISPENSE",
            "facility_code": "A2681-cp",
            "program_id": self.program.id,
            "elmis_orderable_id": self.product.id,
            "lot_id": self.lot.id,
            "quantity": 30,
            "uom_id": self.unit_uom.id,
            "transaction_date": "2026-05-29 08:15:00",
            "prescription_ref": "RX-2026-0001",
        }
        vals.update(overrides)
        return self.env["elmis.outbox"].create(vals)

    def test_defaults_message_id_status_reason_and_attempt_count(self):
        event = self._create_outbox()

        self.assertTrue(event.message_id)
        self.assertEqual(event.status, "PENDING")
        self.assertEqual(event.adjustment_reason, "Consumed")
        self.assertEqual(event.attempt_count, 0)

    def test_message_id_is_unique(self):
        self._create_outbox(message_id="fixed-message-id")

        with self.assertRaises(Exception):
            self._create_outbox(message_id="fixed-message-id")

    def test_outbox_records_cannot_be_deleted(self):
        event = self._create_outbox()

        with self.assertRaises(UserError):
            event.unlink()

    def test_dispense_builds_public_stock_event_payload(self):
        event = self._create_outbox()

        self.assertEqual(
            event.to_public_stock_event_payload(),
            {
                "facility": "A2681-cp",
                "program": "art",
                "signature": "Odoo",
                "documentNumber": "RX-2026-0001",
                "items": [
                    {
                        "orderable": "ARV-TDF300-TAB",
                        "lot": "LOT-ART-001",
                        "occurredDate": "2026-05-29",
                        "quantity": 30,
                        "reason": "Consumed",
                    }
                ],
            },
        )

    def test_payload_uses_message_id_when_prescription_ref_is_missing(self):
        event = self._create_outbox(prescription_ref=False)

        self.assertEqual(
            event.to_public_stock_event_payload()["documentNumber"],
            event.message_id,
        )

    def test_payload_rejects_fractional_quantity(self):
        event = self._create_outbox(quantity=1.5)

        with self.assertRaises(UserError):
            event.to_public_stock_event_payload()

    def test_submit_to_elmis_posts_public_stock_event_and_marks_delivered(self):
        event = self._create_outbox()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("elmis_integration.base_url", "https://dev.elmis.gov.ls/api/")
        params.set_param("elmis_integration.program_codes", "art")
        params.set_param("elmis_integration.api_token", "test-token")
        calls = []

        def fake_request_json(
            service,
            base_url,
            path,
            method="GET",
            query=None,
            headers=None,
            data=None,
        ):
            calls.append(
                {
                    "base_url": base_url,
                    "path": path,
                    "method": method,
                    "query": query,
                    "headers": headers,
                    "data": data,
                }
            )
            return {"id": "stock-event-id"}

        sync_service = self.env["elmis.inventory.sync"]
        with patch.object(type(sync_service), "_elmis_request_json", fake_request_json):
            result = event.submit_to_elmis()

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["delivered"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(event.status, "DELIVERED")
        self.assertEqual(event.attempt_count, 1)
        self.assertTrue(event.sent_at)
        self.assertTrue(event.delivered_at)
        self.assertFalse(event.error_message)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["base_url"], "https://dev.elmis.gov.ls/api/")
        self.assertEqual(calls[0]["path"], "public/stockEvents")
        self.assertEqual(calls[0]["method"], "POST")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(calls[0]["headers"]["Content-Type"], "application/json")
        self.assertEqual(
            json.loads(calls[0]["data"].decode("utf-8")),
            event.to_public_stock_event_payload(),
        )

    def test_submit_to_elmis_marks_failed_and_records_error(self):
        event = self._create_outbox()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("elmis_integration.base_url", "https://dev.elmis.gov.ls/api/")
        params.set_param("elmis_integration.program_codes", "art")
        params.set_param("elmis_integration.api_token", "test-token")

        def fake_request_json(
            service,
            base_url,
            path,
            method="GET",
            query=None,
            headers=None,
            data=None,
        ):
            raise UserError("eLMIS rejected stock event")

        sync_service = self.env["elmis.inventory.sync"]
        with patch.object(type(sync_service), "_elmis_request_json", fake_request_json):
            result = event.submit_to_elmis()

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["delivered"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(event.status, "FAILED")
        self.assertEqual(event.attempt_count, 1)
        self.assertTrue(event.sent_at)
        self.assertFalse(event.delivered_at)
        self.assertIn("eLMIS rejected stock event", event.error_message)

    def test_submit_to_elmis_skips_delivered_event(self):
        event = self._create_outbox(status="DELIVERED")

        result = event.submit_to_elmis()

        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["delivered"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(event.attempt_count, 0)

    def test_submit_to_elmis_skips_dlq_event(self):
        event = self._create_outbox(status="DLQ")

        result = event.submit_to_elmis()

        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["delivered"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(event.attempt_count, 0)

    def test_drain_pending_to_elmis_only_selects_retryable_events(self):
        pending = self._create_outbox(message_id="pending-message", status="PENDING")
        failed = self._create_outbox(message_id="failed-message", status="FAILED")
        delivered = self._create_outbox(message_id="delivered-message", status="DELIVERED")
        dlq = self._create_outbox(message_id="dlq-message", status="DLQ")
        sent = self._create_outbox(message_id="sent-message", status="SENT")
        submitted_ids = []

        def fake_submit(event):
            submitted_ids.append(event.id)
            event.write(
                {
                    "status": "DELIVERED",
                    "attempt_count": event.attempt_count + 1,
                }
            )
            return True

        Outbox = self.env["elmis.outbox"]
        with patch.object(type(Outbox), "_submit_one_to_elmis", fake_submit):
            result = Outbox.drain_pending_to_elmis(limit=10)

        self.assertEqual(result["selected"], 2)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["delivered"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(set(submitted_ids), {pending.id, failed.id})
        self.assertEqual(delivered.status, "DELIVERED")
        self.assertEqual(dlq.status, "DLQ")
        self.assertEqual(sent.status, "SENT")
        self.assertEqual(delivered.attempt_count, 0)
        self.assertEqual(dlq.attempt_count, 0)
        self.assertEqual(sent.attempt_count, 0)

    def test_cron_drain_pending_to_elmis_uses_configured_batch_limit(self):
        first = self._create_outbox(message_id="first-message", status="PENDING")
        second = self._create_outbox(message_id="second-message", status="PENDING")
        third = self._create_outbox(message_id="third-message", status="PENDING")
        submitted_ids = []
        self.env["ir.config_parameter"].sudo().set_param(
            "elmis_integration.outbox_batch_limit",
            "2",
        )

        def fake_submit(event):
            submitted_ids.append(event.id)
            event.write(
                {
                    "status": "DELIVERED",
                    "attempt_count": event.attempt_count + 1,
                }
            )
            return True

        Outbox = self.env["elmis.outbox"]
        with patch.object(type(Outbox), "_submit_one_to_elmis", fake_submit):
            result = Outbox._cron_drain_pending_to_elmis()

        self.assertEqual(result["selected"], 2)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["delivered"], 2)
        self.assertEqual(set(submitted_ids), {first.id, second.id})
        self.assertEqual(first.status, "DELIVERED")
        self.assertEqual(second.status, "DELIVERED")
        self.assertEqual(third.status, "PENDING")

    def test_action_drain_pending_to_elmis_returns_notification(self):
        event = self._create_outbox()

        def fake_submit(outbox_event):
            outbox_event.write(
                {
                    "status": "DELIVERED",
                    "attempt_count": outbox_event.attempt_count + 1,
                }
            )
            return True

        Outbox = self.env["elmis.outbox"]
        with patch.object(type(Outbox), "_submit_one_to_elmis", fake_submit):
            action = Outbox.action_drain_pending_to_elmis()

        self.assertEqual(event.status, "DELIVERED")
        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("1 delivered", action["params"]["message"])

    def test_health_summary_counts_retryable_and_last_error(self):
        self._create_outbox(message_id="health-pending", status="PENDING")
        failed = self._create_outbox(message_id="health-failed", status="FAILED")
        failed.write({"error_message": "eLMIS timeout"})
        self._create_outbox(
            message_id="health-delivered",
            status="DELIVERED",
            delivered_at="2026-05-29 09:00:00",
        )
        self._create_outbox(message_id="health-dlq", status="DLQ")

        summary = self.env["elmis.outbox"].get_health_summary()

        self.assertGreaterEqual(summary["pending"], 1)
        self.assertGreaterEqual(summary["failed"], 1)
        self.assertGreaterEqual(summary["delivered"], 1)
        self.assertGreaterEqual(summary["dlq"], 1)
        self.assertEqual(summary["retryable"], summary["pending"] + summary["failed"])
        self.assertTrue(summary["oldest_retryable_at"])
        self.assertIsInstance(summary["oldest_retryable_age_seconds"], int)
        self.assertEqual(summary["last_error"], "eLMIS timeout")

    def test_mark_dlq_and_reset_to_pending_actions(self):
        failed = self._create_outbox(status="FAILED")
        delivered = self._create_outbox(message_id="action-delivered", status="DELIVERED")

        dlq_action = (failed | delivered).action_mark_dlq()

        self.assertEqual(failed.status, "DLQ")
        self.assertEqual(delivered.status, "DELIVERED")
        self.assertEqual(dlq_action["tag"], "display_notification")

        failed.write({"error_message": "Needs retry"})
        reset_action = failed.action_reset_to_pending()

        self.assertEqual(failed.status, "PENDING")
        self.assertFalse(failed.error_message)
        self.assertEqual(reset_action["tag"], "display_notification")
