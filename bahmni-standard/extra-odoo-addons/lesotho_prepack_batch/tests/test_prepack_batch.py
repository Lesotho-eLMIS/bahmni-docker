from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPrepackBatchElmisTraceability(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.program = cls.env.ref("lesotho_elmis_integration.elmis_program_art")
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

    def test_release_consumes_bulk_and_receives_finished_prepacks(self):
        source_location = self.env["stock.location"].create(
            {
                "name": "Prepack Source Test",
                "usage": "internal",
                "location_id": self.stock_location.id,
                "company_id": self.env.company.id,
            }
        )
        release_location = self.env["stock.location"].create(
            {
                "name": "Prepack Release Test",
                "usage": "internal",
                "location_id": self.stock_location.id,
                "company_id": self.env.company.id,
            }
        )
        packaging_material = self._create_product("Test Packaging Bag")
        self.env["stock.quant"]._update_available_quantity(
            self.bulk_product,
            source_location,
            500.0,
            lot_id=self.bulk_lot,
        )
        self.env["stock.quant"]._update_available_quantity(
            packaging_material,
            source_location,
            100.0,
        )

        batch_model = self.env["bahmni.prepack.batch"]
        result = batch_model.submit_prepack_batch(
            [
                {
                    "id": self.bulk_product.id,
                    "lot_id": self.bulk_lot.id,
                    "location_id": source_location.id,
                    "targets": [
                        {
                            "size": 30,
                            "qty": 10,
                            "packaging_material_id": packaging_material.id,
                        }
                    ],
                }
            ],
            location_src_id=source_location.id,
        )
        batch = batch_model.browse(result["id"])
        batch.location_dest_id = release_location.id

        self.assertEqual(batch.location_src_id, source_location)
        batch.line_ids.write({"release_quality_check_completed": True})
        batch.action_release_batch()

        line = batch.line_ids[:1]
        bulk_qty = self.env["stock.quant"]._get_available_quantity(
            self.bulk_product,
            source_location,
            lot_id=self.bulk_lot,
            strict=True,
        )
        finished_qty = self.env["stock.quant"]._get_available_quantity(
            line.product_id,
            release_location,
            lot_id=line.finished_lot_id,
            strict=True,
        )

        self.assertEqual(batch.location_src_id, source_location)
        self.assertEqual(batch.location_dest_id, release_location)
        self.assertEqual(bulk_qty, 200.0)
        self.assertEqual(finished_qty, 10.0)
        self.assertEqual(line.release_expected_qty, 10)
        self.assertEqual(line.release_actual_qty, 10)
        self.assertEqual(line.release_actual_bulk_usage, 300.0)
        self.assertTrue(line.release_quality_check_completed)
        self.assertEqual(line.released_by_id, self.env.user)
        self.assertTrue(line.release_date)
        self.assertTrue(line.mrp_production_id.move_raw_ids)
        self.assertTrue(all(move.state == "done" for move in line.mrp_production_id.move_raw_ids))
