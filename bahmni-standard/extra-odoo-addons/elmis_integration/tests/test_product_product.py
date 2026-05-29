from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestElmisProductExtension(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")

    def _create_elmis_product(self, name, orderable_id, product_code):
        template = self.env["product.template"].create(
            {
                "name": name,
                "detailed_type": "product",
                "uom_id": self.unit_uom.id,
                "uom_po_id": self.unit_uom.id,
                "list_price": 1.0,
            }
        )
        product = template.product_variant_id
        product.write(
            {
                "is_elmis_product": True,
                "elmis_orderable_id": orderable_id,
                "elmis_product_code": product_code,
                "elmis_program_ids": [(4, self.env.ref("elmis_integration.elmis_program_art").id)],
                "elmis_generic_name": "Paracetamol",
                "elmis_strength": "500Mg",
                "elmis_dosage_form": "tablets",
                "elmis_pack_size": 1000,
                "elmis_pack_size_unit": "tablets",
                "elmis_dispensable_unit": "each",
                "elmis_dispensable_unit_factor": 1.0,
            }
        )
        return product

    def test_elmis_product_fields_are_stored_on_product_variant(self):
        product = self._create_elmis_product(
            "Paracetamol 500Mg Tablets 1000",
            "11111111-1111-1111-1111-111111111111",
            "DON-PAR004-TAB001-1000",
        )

        self.assertTrue(product.is_elmis_product)
        self.assertEqual(
            product.elmis_orderable_id,
            "11111111-1111-1111-1111-111111111111",
        )
        self.assertEqual(product.elmis_product_code, "DON-PAR004-TAB001-1000")
        self.assertEqual(product.elmis_program_ids.mapped("code"), ["art"])
        self.assertEqual(product.elmis_pack_size, 1000)

    def test_elmis_orderable_id_is_unique(self):
        self._create_elmis_product(
            "Paracetamol 500Mg Tablets 1000",
            "22222222-2222-2222-2222-222222222222",
            "DON-PAR004-TAB001-1000",
        )

        with self.assertRaises(Exception):
            self._create_elmis_product(
                "Duplicate Paracetamol",
                "22222222-2222-2222-2222-222222222222",
                "DUP-PAR004-TAB001-1000",
            )
