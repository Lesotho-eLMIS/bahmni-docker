from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockMoveLineElmisTransfer(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref("uom.product_uom_unit")
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.source_location = cls._create_location("Clinical Pharmacy", "A2681-cp")
        cls.destination_location = cls._create_location("Unserviceable", "A2681-uns")
        cls.unmapped_location = cls._create_location("Local Shelf", False)
        cls.program_art = cls.env.ref("lesotho_elmis_integration.elmis_program_art")
        cls.program_em = cls.env.ref("lesotho_elmis_integration.elmis_program_em")
        cls.elmis_product = cls._create_product(
            "Dolutegravir 50mg Tablets",
            is_elmis_product=True,
            orderable_id="abababab-abab-abab-abab-abababababab",
            product_code="ARV-DTG50-TAB",
            program=cls.program_art,
        )
        cls.multi_program_product = cls._create_product(
            "Multi Program Product",
            is_elmis_product=True,
            orderable_id="bcbcbcbc-bcbc-bcbc-bcbc-bcbcbcbcbcbc",
            product_code="MULTI-PROG",
            program=cls.program_art,
        )
        cls.multi_program_product.write({"elmis_program_ids": [(4, cls.program_em.id)]})
        cls.non_elmis_product = cls._create_product("Bandages")
        cls.elmis_lot = cls._create_lot(
            "LOT-DTG-001",
            cls.elmis_product,
            "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd",
        )
        cls.other_elmis_lot = cls._create_lot(
            "LOT-DTG-OTHER",
            cls.elmis_product,
            "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        )
        cls.multi_program_lot = cls._create_lot(
            "LOT-MULTI-001",
            cls.multi_program_product,
            "dededede-dede-dede-dede-dededededede",
        )

    @classmethod
    def _create_location(cls, name, facility_code):
        return cls.env["stock.location"].create(
            {
                "name": name,
                "usage": "internal",
                "location_id": cls.stock_location.id,
                "elmis_facility_code": facility_code,
            }
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
                "tracking": "lot" if is_elmis_product else "none",
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

    def _create_done_move_line(
        self,
        product=None,
        lot=None,
        source=None,
        destination=None,
        quantity=4,
        program=None,
        debit_reason="Transfer Out",
        credit_reason="Transfer In",
        seed_quantity=None,
    ):
        product = product or self.elmis_product
        source = source or self.source_location
        destination = destination or self.destination_location
        lot = lot if lot is not None else self.elmis_lot
        seed_quantity = quantity if seed_quantity is None else seed_quantity
        self.env["stock.quant"]._update_available_quantity(
            product,
            source,
            seed_quantity,
            lot_id=lot,
        )
        move = self.env["stock.move"].create(
            {
                "name": "eLMIS internal transfer",
                "product_id": product.id,
                "product_uom_qty": quantity,
                "product_uom": self.unit_uom.id,
                "location_id": source.id,
                "location_dest_id": destination.id,
            }
        )
        move._action_confirm()
        line_vals = {
            "move_id": move.id,
            "product_id": product.id,
            "product_uom_id": self.unit_uom.id,
            "qty_done": quantity,
            "location_id": source.id,
            "location_dest_id": destination.id,
            "elmis_transfer_debit_reason": debit_reason,
            "elmis_transfer_credit_reason": credit_reason,
        }
        if lot:
            line_vals["lot_id"] = lot.id
        if program is not None:
            line_vals["elmis_transfer_program_id"] = program.id if program else False
        line = self.env["stock.move.line"].create(line_vals)
        move._action_done()
        return line

    def test_available_lots_are_limited_to_source_location(self):
        self.env["stock.quant"]._update_available_quantity(
            self.elmis_product,
            self.source_location,
            5,
            lot_id=self.elmis_lot,
        )
        self.env["stock.quant"]._update_available_quantity(
            self.elmis_product,
            self.destination_location,
            7,
            lot_id=self.other_elmis_lot,
        )
        move = self.env["stock.move"].create(
            {
                "name": "eLMIS availability check",
                "product_id": self.elmis_product.id,
                "product_uom_qty": 2,
                "product_uom": self.unit_uom.id,
                "location_id": self.source_location.id,
                "location_dest_id": self.destination_location.id,
            }
        )
        line = self.env["stock.move.line"].create(
            {
                "move_id": move.id,
                "product_id": self.elmis_product.id,
                "product_uom_id": self.unit_uom.id,
                "qty_done": 2,
                "location_id": self.source_location.id,
                "location_dest_id": self.destination_location.id,
                "lot_id": self.elmis_lot.id,
            }
        )

        self.assertIn(self.elmis_lot, line.available_source_lot_ids)
        self.assertNotIn(self.other_elmis_lot, line.available_source_lot_ids)
        self.assertEqual(line.selected_lot_available_qty, 5)
        self.assertTrue(line.selected_lot_has_enough_qty)

    def test_transfer_blocks_when_selected_lot_has_insufficient_source_stock(self):
        with self.assertRaises(UserError):
            self._create_done_move_line(quantity=4, seed_quantity=2)

    def test_mapped_internal_transfer_creates_debit_and_credit_outbox(self):
        line = self._create_done_move_line(debit_reason="Unusable", credit_reason="Transfer In")

        outbox = self.env["elmis.outbox"].search(
            [("source_stock_move_line_id", "=", line.id)],
            order="stock_move_line_side",
        )

        self.assertEqual(len(outbox), 2)
        source_event = outbox.filtered(lambda event: event.stock_move_line_side == "source")
        destination_event = outbox.filtered(
            lambda event: event.stock_move_line_side == "destination"
        )
        self.assertEqual(source_event.facility_code, "A2681-cp")
        self.assertEqual(source_event.adjustment_reason, "Unusable")
        self.assertEqual(destination_event.facility_code, "A2681-uns")
        self.assertEqual(destination_event.adjustment_reason, "Transfer In")
        self.assertEqual(source_event.program_id, self.program_art)
        self.assertEqual(destination_event.program_id, self.program_art)
        self.assertEqual(source_event.quantity, 4)
        self.assertEqual(destination_event.quantity, 4)

    def test_one_mapped_side_creates_only_that_side(self):
        line = self._create_done_move_line(destination=self.unmapped_location)

        outbox = self.env["elmis.outbox"].search(
            [("source_stock_move_line_id", "=", line.id)]
        )

        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox.stock_move_line_side, "source")
        self.assertEqual(outbox.facility_code, "A2681-cp")

    def test_non_elmis_product_internal_transfer_creates_no_outbox(self):
        line = self._create_done_move_line(
            product=self.non_elmis_product,
            lot=False,
            debit_reason=False,
            credit_reason=False,
        )

        self.assertFalse(
            self.env["elmis.outbox"].search([("source_stock_move_line_id", "=", line.id)])
        )

    def test_multi_program_elmis_transfer_requires_program_selection(self):
        with self.assertRaises(UserError):
            self._create_done_move_line(
                product=self.multi_program_product,
                lot=self.multi_program_lot,
                program=None,
            )

    def test_selected_transfer_program_must_belong_to_product(self):
        with self.assertRaises(ValidationError):
            self._create_done_move_line(program=self.program_em)

    def test_validating_same_transfer_twice_does_not_duplicate_outbox(self):
        line = self._create_done_move_line()

        line._create_elmis_internal_transfer_outbox()

        self.assertEqual(
            self.env["elmis.outbox"].search_count(
                [("source_stock_move_line_id", "=", line.id)]
            ),
            2,
        )
