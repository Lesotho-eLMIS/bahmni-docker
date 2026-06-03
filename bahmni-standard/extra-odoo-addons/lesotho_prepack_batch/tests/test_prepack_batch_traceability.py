from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPrepackBatchTraceability(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.program_art = cls.env.ref("elmis_integration.elmis_program_art")
        cls.picking_type = cls.env["stock.picking.type"].search(
            [("code", "=", "mrp_operation")],
            order="company_id desc, sequence, id",
            limit=1,
        )
        cls.batch = cls.env["bahmni.prepack.batch"].create(
            {
                "picking_type_id": cls.picking_type.id,
                "location_src_id": cls.picking_type.default_location_src_id.id,
                "location_dest_id": cls.picking_type.default_location_dest_id.id,
            }
        )
        cls.elmis_bulk_product = cls._create_product(
            "eLMIS Bulk Paracetamol",
            is_elmis_product=True,
            orderable_id="11111111-2222-3333-4444-555555555555",
            product_code="DON-PAR-BULK-001",
        )
        cls.local_bulk_product = cls._create_product("Local Bulk Vitamin C")
        cls.elmis_bulk_lot = cls._create_lot(
            "LOT-ELMIS-BULK-001",
            cls.elmis_bulk_product,
            lot_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.elmis_bulk_product,
            cls.batch.location_src_id,
            500.0,
            lot_id=cls.elmis_bulk_lot,
        )
        cls.unmapped_bulk_lot = cls.env["stock.lot"].create(
            {
                "name": "LOT-LOCAL-BULK-001",
                "product_id": cls.local_bulk_product.id,
                "company_id": cls.env.company.id,
            }
        )
        cls.untraced_elmis_lot = cls.env["stock.lot"].create(
            {
                "name": "LOT-ELMIS-BULK-UNTRACED",
                "product_id": cls.elmis_bulk_product.id,
                "company_id": cls.env.company.id,
            }
        )
        cls.unmapped_prepack = cls._create_prepack(
            "Local Pack of 10",
            cls.local_bulk_product,
            10.0,
        )
        cls.elmis_prepack = cls._create_prepack(
            "eLMIS Pack of 30",
            cls.elmis_bulk_product,
            30.0,
        )
        cls.unmapped_bom = cls._create_bom(cls.unmapped_prepack, cls.local_bulk_product, 10.0)
        cls.elmis_bom = cls._create_bom(cls.elmis_prepack, cls.elmis_bulk_product, 30.0)

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
                    "elmis_program_ids": [(4, cls.program_art.id)],
                }
            )
        return product

    @classmethod
    def _create_lot(cls, name, product, lot_id=False):
        vals = {
            "name": name,
            "product_id": product.id,
            "company_id": cls.env.company.id,
        }
        if lot_id:
            vals.update({"elmis_lot_id": lot_id, "elmis_lot_number": name})
        return cls.env["stock.lot"].create(vals)

    @classmethod
    def _create_prepack(cls, name, bulk_product, pack_qty):
        template = cls.env["product.template"].create(
            {
                "name": name,
                "detailed_type": "product",
                "uom_id": cls.unit_uom.id,
                "uom_po_id": cls.unit_uom.id,
                "list_price": pack_qty,
                "is_prepack": True,
                "bulk_product_id": bulk_product.id,
                "is_dispensing_pack": True,
                "dispensing_pack_enabled": True,
                "dispensing_base_product_id": bulk_product.id,
                "pack_unit_qty": pack_qty,
            }
        )
        product = template.product_variant_id
        resolved_parent = bulk_product._get_elmis_accountability_product()
        if resolved_parent:
            product.write({"prepack_parent_elmis_product_id": resolved_parent.id})
        return product

    @classmethod
    def _create_bom(cls, prepack_product, bulk_product, bulk_qty):
        return cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": prepack_product.product_tmpl_id.id,
                "product_qty": 1.0,
                "type": "normal",
                "bom_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": bulk_product.id,
                            "product_qty": bulk_qty,
                        },
                    )
                ],
            }
        )

    def test_prepack_line_requires_resolvable_parent_elmis_product(self):
        with self.assertRaises(ValidationError):
            self.env["bahmni.prepack.batch.line"].create(
                {
                    "batch_id": self.batch.id,
                    "product_id": self.unmapped_prepack.id,
                    "bom_id": self.unmapped_bom.id,
                    "bulk_lot_id": self.unmapped_bulk_lot.id,
                    "package_qty": 1,
                }
            )

    def test_prepack_line_requires_traced_elmis_bulk_lot(self):
        with self.assertRaises(ValidationError):
            self.env["bahmni.prepack.batch.line"].create(
                {
                    "batch_id": self.batch.id,
                    "product_id": self.elmis_prepack.id,
                    "bom_id": self.elmis_bom.id,
                    "bulk_lot_id": self.untraced_elmis_lot.id,
                    "package_qty": 1,
                }
            )

    def test_prepack_line_accepts_elmis_product_and_traced_lot(self):
        line = self.env["bahmni.prepack.batch.line"].create(
            {
                "batch_id": self.batch.id,
                "product_id": self.elmis_prepack.id,
                "bom_id": self.elmis_bom.id,
                "bulk_lot_id": self.elmis_bulk_lot.id,
                "package_qty": 1,
            }
        )

        self.assertEqual(line._get_parent_elmis_product(), self.elmis_bulk_product)
        self.assertEqual(line._get_parent_elmis_lot(), self.elmis_bulk_lot)
