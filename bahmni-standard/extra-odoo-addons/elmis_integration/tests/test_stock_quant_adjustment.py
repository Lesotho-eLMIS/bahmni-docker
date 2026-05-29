from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockQuantElmisInventoryAdjustment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.mirror_location = cls.env["stock.location"].create(
            {
                "name": "eLMIS Inventory Count Pharmacy",
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
            "Dolutegravir 50mg Tablets",
            is_elmis_product=True,
            orderable_id="abababab-abab-abab-abab-abababababab",
            product_code="ARV-DTG50-TAB",
            program=cls.program_art,
        )
        cls.other_elmis_product = cls._create_product(
            "Lamivudine 150mg Tablets",
            is_elmis_product=True,
            orderable_id="bcbcbcbc-bcbc-bcbc-bcbc-bcbcbcbcbcbc",
            product_code="ARV-3TC150-TAB",
            program=cls.program_em,
        )
        cls.non_elmis_product = cls._create_product("Bandages")
        cls.elmis_lot = cls._create_lot(
            "LOT-DTG-001",
            cls.elmis_product,
            "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd",
        )
        cls.other_lot = cls._create_lot(
            "LOT-3TC-001",
            cls.other_elmis_product,
            "dededede-dede-dede-dede-dededededede",
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

    def _get_quant(self, product=None, lot=None, quantity=10):
        product = product or self.elmis_product
        if lot is None and product == self.elmis_product:
            lot = self.elmis_lot
        self.env["stock.quant"]._update_available_quantity(
            product,
            self.mirror_location,
            quantity,
            lot_id=lot,
        )
        return self.env["stock.quant"].search(
            [
                ("product_id", "=", product.id),
                ("location_id", "=", self.mirror_location.id),
                ("lot_id", "=", lot.id if lot else False),
            ],
            limit=1,
        )

    def _set_count(self, quant, counted_quantity, reason=None, program=None):
        quant.write(
            {
                "inventory_quantity": counted_quantity,
                "inventory_quantity_set": True,
                "elmis_inventory_program_id": (program or self.program_art).id if program is not False else False,
                "elmis_inventory_adjustment_reason": reason,
            }
        )
        return quant

    def test_negative_elmis_inventory_adjustment_requires_reason(self):
        quant = self._get_quant(quantity=10)
        self._set_count(quant, 7, reason=False)

        with self.assertRaises(UserError):
            quant.action_apply_inventory()

    def test_positive_elmis_inventory_adjustment_requires_credit_reason(self):
        quant = self._get_quant(quantity=10)
        self._set_count(quant, 12, reason="Lost")

        with self.assertRaises(UserError):
            quant.action_apply_inventory()

    def test_negative_elmis_inventory_adjustment_requires_debit_reason(self):
        quant = self._get_quant(quantity=10)
        self._set_count(quant, 7, reason="Receipts")

        with self.assertRaises(UserError):
            quant.action_apply_inventory()

    def test_elmis_inventory_adjustment_program_must_belong_to_product(self):
        quant = self._get_quant(quantity=10)

        with self.assertRaises(ValidationError):
            self._set_count(quant, 8, reason="Lost", program=self.program_em)

    def test_elmis_inventory_adjustment_lot_must_belong_to_product(self):
        quant = self._get_quant(quantity=10)

        with self.assertRaises(ValidationError):
            quant.write({"lot_id": self.other_lot.id})

    def test_negative_elmis_inventory_adjustment_creates_debit_outbox(self):
        quant = self._get_quant(quantity=10)
        self._set_count(quant, 7, reason="Lost")

        quant.action_apply_inventory()

        outbox = self.env["elmis.outbox"].search(
            [
                ("source_stock_move_id", "!=", False),
                ("elmis_orderable_id", "=", self.elmis_product.id),
                ("adjustment_reason", "=", "Lost"),
            ]
        )
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox.transaction_type, "ADJUSTMENT")
        self.assertEqual(outbox.status, "PENDING")
        self.assertEqual(outbox.facility_code, "A2681-cp")
        self.assertEqual(outbox.program_id, self.program_art)
        self.assertEqual(outbox.lot_id, self.elmis_lot)
        self.assertEqual(outbox.quantity, 3)
        self.assertEqual(outbox.uom_id, self.unit_uom)

    def test_positive_elmis_inventory_adjustment_creates_credit_outbox(self):
        quant = self._get_quant(quantity=10)
        self._set_count(quant, 14, reason="Beginning Balance Excess")

        quant.action_apply_inventory()

        outbox = self.env["elmis.outbox"].search(
            [
                ("source_stock_move_id", "!=", False),
                ("elmis_orderable_id", "=", self.elmis_product.id),
                ("adjustment_reason", "=", "Beginning Balance Excess"),
            ]
        )
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox.transaction_type, "ADJUSTMENT")
        self.assertEqual(outbox.quantity, 4)
        self.assertEqual(outbox.program_id, self.program_art)

    def test_zero_elmis_inventory_difference_creates_no_outbox(self):
        quant = self._get_quant(quantity=10)
        self._set_count(quant, 10, reason=False, program=False)

        quant.action_apply_inventory()

        self.assertFalse(
            self.env["elmis.outbox"].search(
                [
                    ("source_stock_move_id", "!=", False),
                    ("elmis_orderable_id", "=", self.elmis_product.id),
                ]
            )
        )

    def test_non_elmis_inventory_adjustment_does_not_create_outbox_or_require_reason(self):
        quant = self._get_quant(product=self.non_elmis_product, lot=False, quantity=10)
        quant.write(
            {
                "inventory_quantity": 6,
                "inventory_quantity_set": True,
                "elmis_inventory_program_id": False,
                "elmis_inventory_adjustment_reason": False,
            }
        )

        quant.action_apply_inventory()

        self.assertFalse(
            self.env["elmis.outbox"].search(
                [("elmis_orderable_id", "=", self.non_elmis_product.id)]
            )
        )
