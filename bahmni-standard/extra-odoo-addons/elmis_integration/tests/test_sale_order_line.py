from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSaleOrderLineElmisSelection(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.partner = cls.env["res.partner"].create({"name": "Test Patient"})
        cls.shop = cls.env["sale.shop"].search([], limit=1)
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.mirror_location = cls.env["stock.location"].create(
            {
                "name": "eLMIS Test Pharmacy",
                "usage": "internal",
                "location_id": cls.stock_location.id,
                "elmis_facility_code": "A2681-cp",
            }
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "elmis_integration.mirror_location_id",
            cls.mirror_location.id,
        )
        cls.program_art = cls.env.ref("elmis_integration.elmis_program_art")
        cls.program_em = cls.env.ref("elmis_integration.elmis_program_em")
        cls.prescribed_product = cls._create_product("Paracetamol 500mg")
        cls.elmis_product = cls._create_product(
            "Paracetamol 500Mg Tablets 1000",
            is_elmis_product=True,
            orderable_id="77777777-7777-7777-7777-777777777777",
            product_code="DON-PAR004-TAB001-1000",
            program=cls.program_art,
        )
        cls.other_elmis_product = cls._create_product(
            "Amoxicillin 250Mg Capsules 100",
            is_elmis_product=True,
            orderable_id="88888888-8888-8888-8888-888888888888",
            product_code="DON-AMX250-CAP001-100",
            program=cls.program_em,
        )
        cls.elmis_lot = cls._create_lot(
            "LOT-PAR-001",
            cls.elmis_product,
            "99999999-9999-9999-9999-999999999999",
        )
        cls.other_lot = cls._create_lot(
            "LOT-AMX-001",
            cls.other_elmis_product,
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )

    @classmethod
    def _create_product(
        cls,
        name,
        is_elmis_product=False,
        orderable_id=False,
        product_code=False,
        program=False,
    ):
        template = cls.env["product.template"].create(
            {
                "name": name,
                "detailed_type": "product",
                "uom_id": cls.unit_uom.id,
                "uom_po_id": cls.unit_uom.id,
                "list_price": 1.0,
            }
        )
        product = template.product_variant_id
        if is_elmis_product:
            product.write(
                {
                    "is_elmis_product": True,
                    "elmis_orderable_id": orderable_id,
                    "elmis_product_code": product_code,
                    "elmis_program_ids": [(4, program.id)],
                }
            )
        return product

    @classmethod
    def _create_lot(cls, name, product, lot_id):
        return cls.env["stock.lot"].create(
            {
                "name": name,
                "product_id": product.id,
                "company_id": cls.env.company.id,
                "elmis_lot_id": lot_id,
                "elmis_lot_number": name,
            }
        )

    def _create_line(self, **overrides):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "shop_id": self.shop.id,
            }
        )
        vals = {
            "order_id": order.id,
            "product_id": self.prescribed_product.id,
            "product_uom_qty": 1,
            "product_uom": self.unit_uom.id,
            "price_unit": 0.0,
        }
        vals.update(overrides)
        return self.env["sale.order.line"].create(vals)

    def test_non_elmis_line_is_unchanged(self):
        line = self._create_line()

        self.assertFalse(line.elmis_product_id)
        self.assertFalse(line.elmis_lot_id)
        self.assertFalse(line.elmis_program_id)

    def test_line_can_store_elmis_product_lot_and_program_selection(self):
        line = self._create_line(
            elmis_product_id=self.elmis_product.id,
            elmis_lot_id=self.elmis_lot.id,
            elmis_program_id=self.program_art.id,
        )

        self.assertEqual(line.elmis_product_id, self.elmis_product)
        self.assertEqual(line.elmis_lot_id, self.elmis_lot)
        self.assertEqual(line.elmis_program_id, self.program_art)
        self.assertEqual(line.product_id, self.prescribed_product)

    def test_elmis_product_selection_requires_elmis_product(self):
        with self.assertRaises(ValidationError):
            self._create_line(elmis_product_id=self.prescribed_product.id)

    def test_elmis_lot_must_belong_to_selected_elmis_product(self):
        with self.assertRaises(ValidationError):
            self._create_line(
                elmis_product_id=self.elmis_product.id,
                elmis_lot_id=self.other_lot.id,
            )

    def test_elmis_lot_requires_elmis_product(self):
        with self.assertRaises(ValidationError):
            self._create_line(elmis_lot_id=self.elmis_lot.id)

    def test_elmis_program_must_belong_to_selected_elmis_product(self):
        with self.assertRaises(ValidationError):
            self._create_line(
                elmis_product_id=self.elmis_product.id,
                elmis_program_id=self.program_em.id,
            )

    def test_marking_non_elmis_line_dispensed_does_not_create_outbox(self):
        line = self._create_line()

        line.write({"dispensed": True})

        self.assertFalse(
            self.env["elmis.outbox"].search(
                [("source_sale_order_line_id", "=", line.id)]
            )
        )

    def test_marking_elmis_line_dispensed_creates_consumed_outbox(self):
        line = self._create_line(
            elmis_product_id=self.elmis_product.id,
            elmis_lot_id=self.elmis_lot.id,
            elmis_program_id=self.program_art.id,
            product_uom_qty=30,
            external_order_uuid="RX-ORDER-001",
        )

        line.write({"dispensed": True})

        outbox = self.env["elmis.outbox"].search(
            [("source_sale_order_line_id", "=", line.id)]
        )
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox.transaction_type, "DISPENSE")
        self.assertEqual(outbox.status, "PENDING")
        self.assertEqual(outbox.adjustment_reason, "Consumed")
        self.assertEqual(outbox.facility_code, "A2681-cp")
        self.assertEqual(outbox.program_id, self.program_art)
        self.assertEqual(outbox.elmis_orderable_id, self.elmis_product)
        self.assertEqual(outbox.lot_id, self.elmis_lot)
        self.assertEqual(outbox.quantity, 30)
        self.assertEqual(outbox.uom_id, self.unit_uom)
        self.assertEqual(outbox.prescription_ref, "RX-ORDER-001")

    def test_marking_elmis_line_dispensed_twice_does_not_duplicate_outbox(self):
        line = self._create_line(
            elmis_product_id=self.elmis_product.id,
            elmis_lot_id=self.elmis_lot.id,
            elmis_program_id=self.program_art.id,
        )

        line.write({"dispensed": True})
        line.write({"dispensed": True})

        self.assertEqual(
            self.env["elmis.outbox"].search_count(
                [("source_sale_order_line_id", "=", line.id)]
            ),
            1,
        )

    def test_marking_dispensed_with_elmis_selection_in_same_write_creates_outbox(self):
        line = self._create_line(product_uom_qty=5)

        line.write(
            {
                "elmis_product_id": self.elmis_product.id,
                "elmis_lot_id": self.elmis_lot.id,
                "elmis_program_id": self.program_art.id,
                "dispensed": True,
            }
        )

        outbox = self.env["elmis.outbox"].search(
            [("source_sale_order_line_id", "=", line.id)]
        )
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox.quantity, 5)
