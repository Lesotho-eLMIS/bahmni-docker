from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPrepackBatchElmisTraceability(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.program = cls.env.ref("elmis_integration.elmis_program_art")
        cls.bulk_product = cls._create_product(
            "Tenofovir 300mg Tablets",
            is_elmis_product=True,
            orderable_id="10101010-1010-1010-1010-101010101010",
            product_code="ARV-TDF300-TAB",
        )
        cls.non_elmis_product = cls._create_product("Local Buffer Stock")
        cls.bulk_lot = cls.env["stock.lot"].create(
            {
                "name": "LOT-TDF-001",
                "product_id": cls.bulk_product.id,
                "company_id": cls.env.company.id,
                "elmis_lot_id": "20202020-2020-2020-2020-202020202020",
                "elmis_lot_number": "LOT-TDF-001",
            }
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.bulk_product,
            cls.stock_location,
            500.0,
            lot_id=cls.bulk_lot,
        )

    @classmethod
    def _create_product(
        cls,
        name,
        is_elmis_product=False,
        orderable_id=False,
        product_code=False,
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
                    "elmis_program_ids": [(4, cls.program.id)],
                }
            )
        return product

    def test_create_prepack_product_links_parent_elmis_product(self):
        prepack_product = self.env["bahmni.prepack.batch"]._get_or_create_prepack_product(
            self.bulk_product,
            30,
        )

        self.assertTrue(prepack_product.is_prepack)
        self.assertEqual(prepack_product.bulk_product_id, self.bulk_product)
        self.assertEqual(prepack_product.prepack_parent_elmis_product_id, self.bulk_product)
        self.assertEqual(prepack_product.pack_unit_qty, 30)

    def test_create_prepack_product_rejects_bulk_product_without_elmis_mapping(self):
        with self.assertRaises(UserError):
            self.env["bahmni.prepack.batch"]._get_or_create_prepack_product(
                self.non_elmis_product,
                30,
            )

    def test_finished_prepack_lot_keeps_source_bulk_lot_lineage(self):
        batch_model = self.env["bahmni.prepack.batch"]
        prepack_product = batch_model._get_or_create_prepack_product(self.bulk_product, 30)
        bom = batch_model._get_or_create_prepack_bom(prepack_product, self.bulk_product, 30)
        batch = batch_model.create({})
        line = self.env["bahmni.prepack.batch.line"].create(
            {
                "batch_id": batch.id,
                "product_id": prepack_product.id,
                "bom_id": bom.id,
                "bulk_lot_id": self.bulk_lot.id,
                "package_qty": 2,
            }
        )

        finished_lot = line._ensure_finished_lot()

        self.assertEqual(finished_lot.prepack_source_lot_id, self.bulk_lot)
