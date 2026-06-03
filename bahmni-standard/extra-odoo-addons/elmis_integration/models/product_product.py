from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    prepack_parent_elmis_product_id = fields.Many2one(
        "product.product",
        string="Parent eLMIS Product",
        related="product_variant_id.prepack_parent_elmis_product_id",
        readonly=False,
    )
    prepack_parent_bulk_uom_id = fields.Many2one(
        "uom.uom",
        string="Parent Bulk UoM",
        related="bulk_product_id.uom_id",
        readonly=True,
    )

    def _resolve_prepack_parent_elmis_product(self):
        self.ensure_one()
        candidates = (
            self.prepack_parent_elmis_product_id,
            self.bulk_product_id,
            self.dispensing_base_product_id,
        )
        for candidate in candidates:
            if not candidate:
                continue
            resolved = candidate._get_elmis_accountability_product()
            if resolved:
                return resolved
        return self.env["product.product"]

    @api.onchange("bulk_product_id", "dispensing_base_product_id")
    def _onchange_prepack_parent_elmis_product_id(self):
        for product in self:
            if product.prepack_parent_elmis_product_id:
                continue
            resolved = product._resolve_prepack_parent_elmis_product()
            if resolved:
                product.prepack_parent_elmis_product_id = resolved


class ProductProduct(models.Model):
    _inherit = "product.product"

    is_elmis_product = fields.Boolean(
        string="eLMIS Product",
        default=False,
        index=True,
        help="Identifies OpenLMIS orderables synced into Odoo for dispensing stock.",
    )
    elmis_orderable_id = fields.Char(
        string="eLMIS Orderable UUID",
        index=True,
        copy=False,
        help="OpenLMIS orderable UUID. This is the canonical product mapping key.",
    )
    elmis_product_code = fields.Char(
        string="eLMIS Product Code",
        index=True,
        copy=False,
        help="OpenLMIS orderable product code.",
    )
    elmis_program_ids = fields.Many2many(
        "elmis.program",
        "elmis_program_product_rel",
        "product_id",
        "program_id",
        string="eLMIS Programs",
        copy=False,
        help="eLMIS programs where this product is stocked for the configured facility.",
    )
    elmis_generic_name = fields.Char(
        string="eLMIS Generic Name",
        copy=False,
    )
    elmis_strength = fields.Char(
        string="eLMIS Strength",
        copy=False,
    )
    elmis_dosage_form = fields.Selection(
        [
            ("tablets", "Tablets"),
            ("capsules", "Capsules"),
            ("syrup", "Syrup"),
            ("suspension", "Suspension"),
            ("injection", "Injection"),
            ("ointment", "Ointment"),
            ("cream", "Cream"),
            ("drops", "Drops"),
            ("solution", "Solution"),
            ("other", "Other"),
        ],
        string="eLMIS Dosage Form",
        copy=False,
    )
    elmis_pack_size = fields.Integer(
        string="eLMIS Pack Size",
        copy=False,
    )
    elmis_pack_size_unit = fields.Char(
        string="eLMIS Pack Size Unit",
        copy=False,
    )
    elmis_dispensable_unit = fields.Char(
        string="eLMIS Dispensable Unit",
        copy=False,
    )
    elmis_dispensable_unit_factor = fields.Float(
        string="eLMIS Dispensable Unit Factor",
        digits=(16, 4),
        copy=False,
    )
    openmrs_concept_uuid = fields.Char(
        string="OpenMRS Concept UUID",
        copy=False,
        index=True,
        help="OpenMRS/eRegister concept UUID for cross-system product resolution.",
    )
    openmrs_concept_code = fields.Char(
        string="OpenMRS Concept Code",
        copy=False,
        index=True,
        help="OpenMRS/eRegister concept code for cross-system product resolution.",
    )
    prepack_parent_elmis_product_id = fields.Many2one(
        "product.product",
        string="Prepack Parent eLMIS Product",
        domain=[("is_elmis_product", "=", True)],
        index=True,
        copy=False,
        help=(
            "Canonical eLMIS orderable consumed when this local prepack product "
            "is dispensed."
        ),
    )

    _sql_constraints = [
        (
            "elmis_orderable_id_unique",
            "UNIQUE(elmis_orderable_id)",
            "An eLMIS orderable UUID must map to exactly one Odoo product.",
        ),
    ]

    @api.constrains("prepack_parent_elmis_product_id")
    def _check_prepack_parent_elmis_product_id(self):
        for product in self:
            if (
                product.prepack_parent_elmis_product_id
                and not product.prepack_parent_elmis_product_id.is_elmis_product
            ):
                raise ValidationError(
                    "The prepack parent eLMIS product must be an eLMIS-linked product."
                )

    def _get_elmis_accountability_product(self):
        self.ensure_one()
        product = self
        seen_ids = set()
        while product and product.id not in seen_ids:
            if product.is_elmis_product:
                return product
            seen_ids.add(product.id)
            product = (
                product.prepack_parent_elmis_product_id
                or product.bulk_product_id
                or product.dispensing_base_product_id
            )
        return self.env["product.product"]
