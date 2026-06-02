from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockScrapElmisAdjustment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.scrap_location = cls.env["stock.location"].search(
            [("scrap_location", "=", True)],
            limit=1,
        )
        cls.mirror_location = cls.env["stock.location"].create(
            {
                "name": "eLMIS Test Pharmacy Scrap",
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
        cls.elmis_product = cls._create_product(
            "Efavirenz 600mg Tablets",
            is_elmis_product=True,
            orderable_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            product_code="ARV-EFV600-TAB",
            program=cls.program_art,
        )
        cls.other_elmis_product = cls._create_product(
            "Nevirapine 200mg Tablets",
            is_elmis_product=True,
            orderable_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            product_code="ARV-NVP200-TAB",
            program=cls.program_em,
        )
        cls.non_elmis_product = cls._create_product("Cotton Wool")
        cls.elmis_lot = cls._create_lot(
            "LOT-EFV-001",
            cls.elmis_product,
            "dddddddd-dddd-dddd-dddd-dddddddddddd",
        )
        cls.other_lot = cls._create_lot(
            "LOT-NVP-001",
            cls.other_elmis_product,
            "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
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

    def _create_scrap(self, **overrides):
        vals = {
            "product_id": self.elmis_product.id,
            "product_uom_id": self.unit_uom.id,
            "scrap_qty": 3,
            "lot_id": self.elmis_lot.id,
            "location_id": self.mirror_location.id,
            "scrap_location_id": self.scrap_location.id,
            "elmis_program_id": self.program_art.id,
            "elmis_adjustment_reason": "Damaged",
        }
        vals.update(overrides)
        return self.env["stock.scrap"].create(vals)

    def _seed_stock(self, product=None, lot=None, quantity=10):
        self.env["stock.quant"]._update_available_quantity(
            product or self.elmis_product,
            self.mirror_location,
            quantity,
            lot_id=lot or self.elmis_lot,
        )

    def test_elmis_scrap_requires_adjustment_reason(self):
        self._seed_stock()
        scrap = self._create_scrap(elmis_adjustment_reason=False)

        with self.assertRaises(UserError):
            scrap.action_validate()

    def test_elmis_scrap_requires_program(self):
        self._seed_stock()
        scrap = self._create_scrap(elmis_program_id=False)

        with self.assertRaises(UserError):
            scrap.action_validate()

    def test_elmis_scrap_program_must_belong_to_product(self):
        with self.assertRaises(ValidationError):
            self._create_scrap(elmis_program_id=self.program_em.id)

    def test_elmis_scrap_lot_must_belong_to_product(self):
        with self.assertRaises(ValidationError):
            self._create_scrap(lot_id=self.other_lot.id)

    def test_elmis_scrap_creates_adjustment_outbox(self):
        self._seed_stock(quantity=10)
        scrap = self._create_scrap(scrap_qty=4, elmis_adjustment_reason="Expiry")

        scrap.action_validate()

        outbox = self.env["elmis.outbox"].search(
            [("source_stock_scrap_id", "=", scrap.id)]
        )
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox.transaction_type, "ADJUSTMENT")
        self.assertEqual(outbox.status, "PENDING")
        self.assertEqual(outbox.adjustment_reason, "Expiry")
        self.assertEqual(outbox.facility_code, "A2681-cp")
        self.assertEqual(outbox.program_id, self.program_art)
        self.assertEqual(outbox.elmis_orderable_id, self.elmis_product)
        self.assertEqual(outbox.lot_id, self.elmis_lot)
        self.assertEqual(outbox.quantity, 4)
        self.assertEqual(outbox.uom_id, self.unit_uom)
        self.assertEqual(outbox.prescription_ref, scrap.name)

    def test_validating_elmis_scrap_twice_does_not_duplicate_outbox(self):
        self._seed_stock(quantity=10)
        scrap = self._create_scrap(scrap_qty=2)

        scrap.action_validate()
        scrap._create_elmis_scrap_outbox_for_done_records()

        self.assertEqual(
            self.env["elmis.outbox"].search_count(
                [("source_stock_scrap_id", "=", scrap.id)]
            ),
            1,
        )

    def test_non_elmis_scrap_does_not_create_outbox_or_require_reason(self):
        self.env["stock.quant"]._update_available_quantity(
            self.non_elmis_product,
            self.mirror_location,
            5,
        )
        scrap = self._create_scrap(
            product_id=self.non_elmis_product.id,
            lot_id=False,
            elmis_program_id=False,
            elmis_adjustment_reason=False,
            scrap_qty=1,
        )

        scrap.action_validate()

        self.assertFalse(
            self.env["elmis.outbox"].search(
                [("source_stock_scrap_id", "=", scrap.id)]
            )
        )
