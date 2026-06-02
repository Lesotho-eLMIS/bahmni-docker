from odoo import fields, models


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

    _sql_constraints = [
        (
            "elmis_orderable_id_unique",
            "UNIQUE(elmis_orderable_id)",
            "An eLMIS orderable UUID must map to exactly one Odoo product.",
        ),
    ]
