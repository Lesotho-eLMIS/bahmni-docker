from odoo import fields
from odoo.tests import SavepointCase, tagged


@tagged("post_install", "-at_install")
class TestPrescriptionDispensing(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.partner = cls.env["res.partner"].create({"name": "Dispense Patient"})
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
        order = self.env["sale.order"].create({"partner_id": self.partner.id})
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
