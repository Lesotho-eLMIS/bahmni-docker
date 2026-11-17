from odoo.exceptions import UserError
from odoo.tests import SavepointCase, tagged


@tagged("post_install", "-at_install")
class TestPrescriptionQuantity(SavepointCase):
    # Independent expectations for every frequency offered by the form.
    CASES = [
        ("Immediately", 7, "Days", 1),
        ("Once a day", 7, "Days", 7),
        ("Twice a day", 7, "Days", 14),
        ("Thrice a day", 7, "Days", 21),
        ("Four times a day", 7, "Days", 28),
        ("Every Hour", 1, "Days", 24),
        ("Every 2 hours", 1, "Days", 12),
        ("Every 3 hours", 1, "Days", 8),
        ("Every 4 hours", 1, "Days", 6),
        ("Every 6 hours", 1, "Days", 4),
        ("Every 8 hours", 1, "Days", 3),
        ("Every 12 hours", 1, "Days", 2),
        ("On alternate days", 14, "Days", 7),
        ("Once a week", 2, "Weeks", 2),
        ("Twice a week", 2, "Weeks", 4),
        ("Thrice a week", 2, "Weeks", 6),
        ("Every 2 weeks", 4, "Weeks", 2),
        ("Every 3 weeks", 6, "Weeks", 2),
        ("Once a month", 2, "Months", 2),
        ("Five times a day", 7, "Days", 35),
        ("Four days a week", 2, "Weeks", 8),
        ("Five days a week", 2, "Weeks", 10),
        ("Six days a week", 2, "Weeks", 12),
    ]

    def payload(self, **values):
        return {"dose": "1", "frequency": "Thrice a day", "duration": "7", "duration_unit": "Days", "instructions": "After meals", **values}

    def test_all_offered_frequencies(self):
        model = self.env["sale.order"]
        offered = model._get_prescription_direction_options(self.env["sale.order.line"])["frequency"]
        for frequency, duration, unit, expected in self.CASES:
            with self.subTest(frequency=frequency):
                self.assertIn(frequency, offered)
                quantity = model._get_ui_prescribed_quantity(self.payload(frequency=frequency, duration=duration, duration_unit=unit))
                self.assertAlmostEqual(quantity, expected)

    def test_scaled_dose_and_normalization(self):
        model = self.env["sale.order"]
        self.assertEqual(model._get_ui_prescribed_quantity(self.payload(dose="2", frequency="  THRICE   A DAY  ")), 42)
        self.assertEqual(model._get_ui_prescribed_quantity(self.payload(dose="0.5", frequency="Twice a week", duration=2, duration_unit="Weeks")), 2)
        for frequency, expected in [("TDS", 21), ("BID", 14), ("Q6H", 28), ("2", 14), ("twice daily", 14)]:
            self.assertAlmostEqual(model._get_ui_prescribed_quantity(self.payload(frequency=frequency)), expected)

    def test_single_administration_does_not_repeat_over_duration(self):
        model = self.env["sale.order"]
        self.assertEqual(model._get_ui_prescribed_quantity(self.payload(dose=2, frequency="Immediately", duration="", duration_unit="")), 2)
        self.assertEqual(model._get_ui_prescribed_quantity(self.payload(dose=2, frequency="STAT", duration=30)), 2)

    def test_validation_identifies_field_and_item(self):
        cases = [
            ({"dose": ""}, "Enter a dose"),
            ({"dose": "-1"}, "Dose must be"),
            ({"dose": "inf"}, "Dose must be"),
            ({"frequency": ""}, "Select a frequency"),
            ({"frequency": "As needed"}, "Frequency 'As needed' is not supported"),
            ({"duration": ""}, "Enter a duration"),
            ({"duration": "0"}, "Duration must be"),
            ({"duration_unit": ""}, "Select a duration unit"),
            ({"duration_unit": "Unknown unit"}, "Duration unit 'Unknown unit' is not supported"),
        ]
        for change, message in cases:
            with self.subTest(change=change), self.assertRaisesRegex(UserError, "Prescribed item 2: " + message):
                self.env["sale.order"]._get_ui_prescribed_quantity(self.payload(**change), item_number=2)

    def test_instructions_do_not_hide_frequency_validation(self):
        payload = self.payload(instructions="After meals", additional_instructions="Use label")
        self.assertEqual(self.env["sale.order"]._get_ui_prescribed_quantity(payload), 21)
        payload["frequency"] = "Unsupported schedule"
        with self.assertRaisesRegex(UserError, "Frequency 'Unsupported schedule' is not supported"):
            self.env["sale.order"]._get_ui_prescribed_quantity(payload)

    def test_numeric_frequency_rejects_non_decimal_formats(self):
        model = self.env["sale.order"]
        for value in ("0x10", "1_0", "nan", "inf", "-2", "0"):
            with self.subTest(value=value):
                self.assertEqual(model._get_ui_frequency_multiplier(value), 0)

    def test_saved_prescription_retains_correct_quantity_and_instructions(self):
        partner = self.env["res.partner"].create({"name": "Frequency Regression Patient", "is_patient": True})
        product = self.env["product.product"].create({"name": "Frequency Regression Product", "type": "consu"})
        shop = self.env["sale.shop"].create({
            "name": "Frequency Regression Dispensary",
            "company_id": self.env.company.id,
            "warehouse_id": self.env.ref("stock.warehouse0").id,
            "location_id": self.env.ref("stock.stock_location_stock").id,
            "payment_default_id": self.env.ref("account.account_payment_term_immediate").id,
        })
        result = self.env["sale.order"].create_prescription_from_ui({
            "patient_id": partner.id,
            "dispensary_id": shop.id,
            "lines": [{"product_id": product.id, **self.payload(frequency="Twice a week", duration=2, duration_unit="Weeks")}],
        })
        order = self.env["sale.order"].browse(result["id"])
        self.assertEqual(order.partner_id, partner)
        self.assertEqual(len(order.order_line), 1)
        self.assertAlmostEqual(order.order_line.product_uom_qty, 4)
        self.assertAlmostEqual(order.order_line.prescribed_qty_base_units, 4)
        self.assertEqual(order.order_line.frequency, "Twice a week")
        self.assertEqual(order.order_line.administration_instructions, "After meals")
