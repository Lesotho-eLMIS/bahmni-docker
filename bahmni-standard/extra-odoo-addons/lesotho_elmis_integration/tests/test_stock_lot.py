from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestElmisStockLotExtension(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.product_template = cls.env["product.template"].create(
            {
                "name": "Amoxicillin 250Mg Capsules 100",
                "detailed_type": "product",
                "uom_id": cls.unit_uom.id,
                "uom_po_id": cls.unit_uom.id,
            }
        )
        cls.product = cls.product_template.product_variant_id

    def _create_elmis_lot(self, name, lot_id, lot_number):
        return self.env["stock.lot"].create(
            {
                "name": name,
                "product_id": self.product.id,
                "company_id": self.env.company.id,
                "elmis_lot_id": lot_id,
                "elmis_lot_number": lot_number,
            }
        )

    def test_elmis_lot_fields_are_stored(self):
        lot = self._create_elmis_lot(
            "LOT-2026-001",
            "33333333-3333-3333-3333-333333333333",
            "LOT-2026-001",
        )

        self.assertEqual(lot.elmis_lot_id, "33333333-3333-3333-3333-333333333333")
        self.assertEqual(lot.elmis_lot_number, "LOT-2026-001")

    def test_elmis_lot_id_is_unique(self):
        self._create_elmis_lot(
            "LOT-2026-002",
            "44444444-4444-4444-4444-444444444444",
            "LOT-2026-002",
        )

        with self.assertRaises(Exception):
            self._create_elmis_lot(
                "LOT-2026-003",
                "44444444-4444-4444-4444-444444444444",
                "LOT-2026-003",
            )
