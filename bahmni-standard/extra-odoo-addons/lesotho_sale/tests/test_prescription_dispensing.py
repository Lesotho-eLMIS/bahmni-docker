from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import SavepointCase, tagged


@tagged("post_install", "-at_install")
class TestPrescriptionDispensing(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.partner = cls.env["res.partner"].create({"name": "Dispense Patient"})
        cls.shop = cls.env["sale.shop"].search([], limit=1)
        cls.lot_expiry_field = cls._get_lot_expiry_field()

        cls.product = cls._create_tracked_product("Dispensed Product A")
        cls.alt_product = cls._create_tracked_product("Dispensed Product B")

        cls.product_lot_1 = cls._create_lot(
            cls.product,
            "A-BATCH-01",
            "2026-07-15",
        )
        cls.product_lot_2 = cls._create_lot(
            cls.product,
            "A-BATCH-02",
            "2026-09-30",
        )
        cls.alt_product_lot_1 = cls._create_lot(
            cls.alt_product,
            "B-BATCH-01",
            "2026-06-20",
        )
        cls.alt_product_lot_2 = cls._create_lot(
            cls.alt_product,
            "B-BATCH-02",
            "2026-10-10",
        )

        cls.env["stock.quant"]._update_available_quantity(
            cls.product,
            cls.stock_location,
            15.0,
            lot_id=cls.product_lot_1,
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.product,
            cls.stock_location,
            10.0,
            lot_id=cls.product_lot_2,
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.alt_product,
            cls.stock_location,
            6.0,
            lot_id=cls.alt_product_lot_1,
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.alt_product,
            cls.stock_location,
            12.0,
            lot_id=cls.alt_product_lot_2,
        )

    @classmethod
    def _get_lot_expiry_field(cls):
        for field_name in ("expiration_date", "use_date", "removal_date", "alert_date"):
            if field_name in cls.env["stock.lot"]._fields:
                return field_name
        raise AssertionError("Expected stock.lot to expose an expiry-related field.")

    @classmethod
    def _create_tracked_product(cls, name):
        template = cls.env["product.template"].create(
            {
                "name": name,
                "detailed_type": "product",
                "tracking": "lot",
                "uom_id": cls.unit_uom.id,
                "uom_po_id": cls.unit_uom.id,
                "list_price": 1.0,
            }
        )
        return template.product_variant_id

    @classmethod
    def _create_lot(cls, product, name, expiry_value):
        lot = cls.env["stock.lot"].create(
            {
                "name": name,
                "product_id": product.id,
                "company_id": cls.env.company.id,
            }
        )
        field = lot._fields[cls.lot_expiry_field]
        if field.type == "date":
            parsed_value = fields.Date.to_date(expiry_value)
        else:
            parsed_value = fields.Datetime.to_datetime(f"{expiry_value} 00:00:00")
        lot.write({cls.lot_expiry_field: parsed_value})
        return lot

    def _create_order_line(self, product=None, quantity=5.0):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shop_id": self.shop.id,
            }
        )
        return self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": (product or self.product).id,
                "name": (product or self.product).display_name,
                "product_uom_qty": quantity,
                "product_uom": self.unit_uom.id,
                "price_unit": 0.0,
            }
        )

    def _format_expected_expiry(self, lot):
        field = lot._fields[self.lot_expiry_field]
        value = getattr(lot, self.lot_expiry_field)
        if field.type == "date":
            return fields.Date.to_string(value)
        return fields.Datetime.to_string(value)

    def test_fetch_prescription_dispensing_includes_batch_options(self):
        line = self._create_order_line(quantity=3.0)

        payload = line.order_id.fetch_prescription_dispensing()
        line_payload = next(item for item in payload["lines"] if item["id"] == line.id)

        self.assertEqual(
            [option["batch_number"] for option in line_payload["batch_options"]],
            ["A-BATCH-01", "A-BATCH-02"],
        )
        self.assertEqual(
            line_payload["batch_options"][0]["expiry_date"],
            self._format_expected_expiry(self.product_lot_1),
        )

    def test_product_change_auto_selects_fefo_batch_and_returns_metadata(self):
        line = self._create_order_line(quantity=4.0)

        updated = line.order_id.update_prescription_dispensing_line(
            line.id,
            {"product_id": self.alt_product.id},
        )

        self.assertEqual(updated["product_id"], self.alt_product.id)
        self.assertEqual(updated["batch_number"], "B-BATCH-01")
        self.assertEqual(
            updated["expiry_date"],
            self._format_expected_expiry(self.alt_product_lot_1),
        )
        self.assertEqual(
            [option["batch_number"] for option in updated["batch_options"]],
            ["B-BATCH-01", "B-BATCH-02"],
        )

    def test_batch_change_updates_corresponding_expiry(self):
        line = self._create_order_line(quantity=2.0)

        line.order_id.update_prescription_dispensing_line(
            line.id,
            {"product_id": self.alt_product.id},
        )
        updated = line.order_id.update_prescription_dispensing_line(
            line.id,
            {"batch_number": "B-BATCH-02"},
        )

        self.assertEqual(updated["batch_number"], "B-BATCH-02")
        self.assertEqual(
            updated["expiry_date"],
            self._format_expected_expiry(self.alt_product_lot_2),
        )

    def test_product_change_loads_batch_dropdown_even_when_no_single_lot_covers_quantity(self):
        line = self._create_order_line(quantity=20.0)

        updated = line.order_id.update_prescription_dispensing_line(
            line.id,
            {"product_id": self.alt_product.id},
        )

        self.assertEqual(updated["batch_number"], "B-BATCH-01")
        self.assertEqual(
            [option["batch_number"] for option in updated["batch_options"]],
            ["B-BATCH-01", "B-BATCH-02"],
        )

    def test_fetch_prescription_dispensing_auto_populates_missing_batch_number(self):
        line = self._create_order_line(quantity=20.0)
        line.with_context(skip_prescription_init=True).write(
            {
                "dispensing_batch_number": False,
            }
        )

        payload = line.order_id.fetch_prescription_dispensing()
        line_payload = next(item for item in payload["lines"] if item["id"] == line.id)

        self.assertEqual(line_payload["batch_number"], "A-BATCH-01")
        self.assertEqual(
            line_payload["expiry_date"],
            self._format_expected_expiry(self.product_lot_1),
        )

    def test_external_line_is_treated_as_fully_served_with_zero_quantity(self):
        line = self._create_order_line(quantity=5.0)
        line.with_context(skip_prescription_init=True).write(
            {
                "served_internally": False,
            }
        )

        payload = line.order_id.fetch_prescription_dispensing()
        line_payload = next(item for item in payload["lines"] if item["id"] == line.id)

        line = self.env["sale.order.line"].browse(line.id)
        order = self.env["sale.order"].browse(line.order_id.id)

        self.assertFalse(line.served_internally)
        self.assertTrue(line.dispensed)
        self.assertEqual(line.product_uom_qty, 0.0)
        self.assertEqual(line.prescription_status, "served_externally")
        self.assertEqual(order.prescription_status, "awaiting_dispensing")
        self.assertEqual(order.dispensed_line_count, 0)
        self.assertEqual(line_payload["served_internally"], False)
        self.assertEqual(line_payload["quantity_dispensed"], 0)
        self.assertEqual(line_payload["prescription_status"], "served_externally")

    def test_partial_internal_fulfillment_sets_partial_status(self):
        line = self._create_order_line(quantity=5.0)
        line.with_context(skip_prescription_init=True).write(
            {
                "prescribed_qty_base_units": 10.0,
                "product_uom_qty": 4.0,
            }
        )

        payload = line.order_id.fetch_prescription_dispensing()
        line_payload = next(item for item in payload["lines"] if item["id"] == line.id)

        line = self.env["sale.order.line"].browse(line.id)
        order = self.env["sale.order"].browse(line.order_id.id)

        self.assertTrue(line.served_internally)
        self.assertEqual(line.product_uom_qty, 4.0)
        self.assertEqual(line.prescription_status, "partially_fulfilled")
        self.assertEqual(order.prescription_status, "awaiting_dispensing")
        self.assertEqual(line_payload["quantity_dispensed"], 4.0)
        self.assertEqual(line_payload["prescription_status"], "partially_fulfilled")

    def test_zero_internal_quantity_stays_awaiting_dispensing(self):
        line = self._create_order_line(quantity=5.0)
        line.with_context(skip_prescription_init=True).write(
            {
                "prescribed_qty_base_units": 5.0,
                "product_uom_qty": 0.0,
                "served_internally": True,
            }
        )

        payload = line.order_id.fetch_prescription_dispensing()
        line_payload = next(item for item in payload["lines"] if item["id"] == line.id)

        line = self.env["sale.order.line"].browse(line.id)
        order = self.env["sale.order"].browse(line.order_id.id)

        self.assertTrue(line.served_internally)
        self.assertEqual(line.product_uom_qty, 0.0)
        self.assertEqual(line.prescription_status, "awaiting_dispensing")
        self.assertEqual(order.prescription_status, "awaiting_dispensing")
        self.assertEqual(line_payload["quantity_dispensed"], 0.0)
        self.assertEqual(line_payload["prescription_status"], "awaiting_dispensing")

    def test_overdispensing_is_treated_as_fully_dispensed(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id

        updated = order.update_prescription_dispensing_line(
            line.id,
            {"quantity_dispensed": 6.0},
        )

        line = self.env["sale.order.line"].browse(line.id)
        self.assertTrue(order.action_save_prescription_from_ui())
        self.assertEqual(line.prescription_status, "fully_served")
        self.assertEqual(updated["prescription_status"], "fully_served")

    def test_overdispensing_can_be_served_normally(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id
        order.write({"medication_explanation_confirmed": True})

        order.update_prescription_dispensing_line(
            line.id,
            {"quantity_dispensed": 6.0},
        )
        order.fetch_prescription_dispensing()
        report_action = order.action_serve_prescription_from_ui(False)

        order = self.env["sale.order"].browse(order.id)
        line = self.env["sale.order.line"].browse(line.id)
        self.assertEqual(report_action["type"], "ir.actions.act_url")
        self.assertEqual(line.prescription_status, "fully_served")
        self.assertEqual(order.prescription_status, "fully_served")

    def test_serving_uses_per_line_status_not_aggregate_totals(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id
        line_2 = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.alt_product.id,
                "name": self.alt_product.display_name,
                "product_uom_qty": 5.0,
                "product_uom": self.unit_uom.id,
                "price_unit": 0.0,
            }
        )
        order.write({"medication_explanation_confirmed": True})
        line.with_context(skip_prescription_init=True).write(
            {
                "prescribed_qty_base_units": 5.0,
                "product_uom_qty": 10.0,
            }
        )
        line_2.with_context(skip_prescription_init=True).write(
            {
                "prescribed_qty_base_units": 5.0,
                "product_uom_qty": 0.0,
                "served_internally": True,
            }
        )

        summary = order.evaluate_prescription_serving()

        self.assertEqual(summary["total_prescribed"], 10.0)
        self.assertEqual(summary["total_dispensed"], 10.0)
        self.assertTrue(summary["needs_backorder"])
        self.assertEqual(summary["prescription_status"], "partially_fulfilled")

        order.fetch_prescription_dispensing()
        report_action = order.action_serve_prescription_from_ui(True)

        order = self.env["sale.order"].browse(order.id)
        line = self.env["sale.order.line"].browse(line.id)
        line_2 = self.env["sale.order.line"].browse(line_2.id)
        backorder = self.env["sale.order"].search([("origin", "=", order.name)], limit=1)
        backorder_line = backorder.order_line.filtered(lambda l: not l.display_type)[:1]

        self.assertEqual(report_action["type"], "ir.actions.act_url")
        self.assertEqual(line.prescription_status, "fully_served")
        self.assertFalse(line_2.exists())
        self.assertEqual(backorder.prescription_status, "awaiting_dispensing")
        self.assertEqual(backorder_line.prescribed_qty_base_units, 5.0)
        self.assertEqual(backorder_line.product_uom_qty, 0.0)
        self.assertEqual(order.prescription_status, "fully_served")

    def test_save_action_keeps_prescription_status_unchanged(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id

        saved = order.action_save_prescription_from_ui()

        order = self.env["sale.order"].browse(order.id)
        self.assertTrue(saved)
        self.assertFalse(order.is_on_hold)
        self.assertEqual(order.prescription_status, "awaiting_dispensing")

    def test_hold_action_requires_reason_and_stores_previous_status(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id

        with self.assertRaises(UserError):
            order.action_hold_prescription_from_ui("")

        action = order.action_hold_prescription_from_ui("Awaiting stock verification")

        order = self.env["sale.order"].browse(order.id)
        self.assertTrue(order.is_on_hold)
        self.assertEqual(order.prescription_status, "on_hold")
        self.assertEqual(order.previous_status, "awaiting_dispensing")
        self.assertEqual(order.on_hold_reason, "Awaiting stock verification")
        self.assertEqual(action["name"], "Prescriptions")

    def test_evaluate_serving_summary_requires_backorder_for_partial_internal_lines(self):
        line = self._create_order_line(quantity=5.0)
        line.with_context(skip_prescription_init=True).write(
            {
                "prescribed_qty_base_units": 10.0,
                "product_uom_qty": 4.0,
            }
        )

        summary = line.order_id.evaluate_prescription_serving()

        self.assertTrue(summary["has_internal_lines"])
        self.assertTrue(summary["needs_backorder"])
        self.assertEqual(summary["total_prescribed"], 10.0)
        self.assertEqual(summary["total_dispensed"], 4.0)
        self.assertTrue(summary["has_labels"])

    def test_partial_serve_with_backorder_creates_new_order_for_balance(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id
        order.write({"medication_explanation_confirmed": True})
        line.with_context(skip_prescription_init=True).write(
            {
                "prescribed_qty_base_units": 10.0,
                "product_uom_qty": 4.0,
            }
        )
        order.fetch_prescription_dispensing()

        report_action = order.action_serve_prescription_from_ui(True)

        order = self.env["sale.order"].browse(order.id)
        backorder = self.env["sale.order"].search([("origin", "=", order.name)], limit=1)
        backorder_line = backorder.order_line.filtered(lambda l: not l.display_type)[:1]

        self.assertEqual(report_action["type"], "ir.actions.act_url")
        self.assertIn("report/pdf/lesotho_sale.report_prescription_labels", report_action["url"])
        self.assertEqual(report_action["target"], "new")
        line = self.env["sale.order.line"].browse(line.id)
        self.assertEqual(order.prescription_status, "fully_served")
        self.assertEqual(line.prescription_status, "fully_served")
        self.assertEqual(line.prescribed_qty_base_units, 4.0)
        self.assertTrue(backorder)
        self.assertEqual(backorder.prescription_status, "awaiting_dispensing")
        self.assertEqual(backorder_line.prescribed_qty_base_units, 6.0)
        self.assertEqual(backorder_line.product_uom_qty, 0.0)
        self.assertEqual(backorder_line.prescription_status, "awaiting_dispensing")

    def test_partial_serve_without_backorder_requires_balance_resolution(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id
        order.write({"medication_explanation_confirmed": True})
        line.with_context(skip_prescription_init=True).write(
            {
                "prescribed_qty_base_units": 10.0,
                "product_uom_qty": 4.0,
            }
        )
        order.fetch_prescription_dispensing()

        with self.assertRaises(UserError):
            order.action_serve_prescription_from_ui(False)

        with self.assertRaises(UserError):
            order.action_serve_prescription_from_ui(False, "other", "")

        report_action = order.action_serve_prescription_from_ui(
            False,
            "external_referral",
            "",
        )

        order = self.env["sale.order"].browse(order.id)
        line = self.env["sale.order.line"].browse(line.id)

        self.assertEqual(report_action["type"], "ir.actions.act_url")
        self.assertEqual(order.prescription_status, "fully_served")
        self.assertEqual(line.prescription_status, "balance_waived")
        self.assertEqual(line.balance_resolution, "external_referral")

    def test_on_hold_prescription_cannot_be_served_until_resumed(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id
        order.write({"medication_explanation_confirmed": True})
        order.action_hold_prescription_from_ui("Patient counselling interrupted")

        with self.assertRaises(UserError):
            order.action_serve_prescription_from_ui()

        payload = order.action_resume_prescription_from_ui()
        order.fetch_prescription_dispensing()
        report_action = order.action_serve_prescription_from_ui()

        order = self.env["sale.order"].browse(order.id)
        line = self.env["sale.order.line"].browse(line.id)

        self.assertFalse(order.is_on_hold)
        self.assertEqual(payload["prescription_status"], "awaiting_dispensing")
        self.assertEqual(order.prescription_status, "fully_served")
        self.assertTrue(line.dispensed)
        self.assertEqual(report_action["type"], "ir.actions.act_url")
        self.assertIn("report/pdf/lesotho_sale.report_prescription_labels", report_action["url"])
        self.assertEqual(report_action["target"], "new")

    def test_resume_restores_previous_status(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id
        order.write({"dispensing_status": "partially_fulfilled"})
        order.action_hold_prescription_from_ui("Waiting for patient")

        payload = order.action_resume_prescription_from_ui()

        order = self.env["sale.order"].browse(order.id)
        self.assertFalse(order.is_on_hold)
        self.assertEqual(order.previous_status, "partially_fulfilled")
        self.assertEqual(order.prescription_status, "partially_fulfilled")
        self.assertEqual(payload["prescription_status"], "partially_fulfilled")

    def test_closed_or_cancelled_prescription_cannot_resume(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id
        order.action_hold_prescription_from_ui("Cancelled by clinician")
        order.action_cancel()

        with self.assertRaises(UserError):
            order.action_resume_prescription_from_ui()

    def test_fully_served_prescription_cannot_be_modified_again(self):
        line = self._create_order_line(quantity=5.0)
        order = line.order_id
        order.write({"medication_explanation_confirmed": True})
        order.fetch_prescription_dispensing()
        order.action_serve_prescription_from_ui()

        order = self.env["sale.order"].browse(order.id)
        self.assertEqual(order.prescription_status, "fully_served")

        with self.assertRaises(UserError):
            order.add_prescription_dispensing_line()

        with self.assertRaises(UserError):
            order.update_prescription_dispensing_line(line.id, {"comments": "locked"})

        with self.assertRaises(UserError):
            order.remove_prescription_dispensing_line(line.id)

        with self.assertRaises(UserError):
            order.action_serve_prescription_from_ui()
