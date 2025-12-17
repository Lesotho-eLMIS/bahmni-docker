from odoo import models, fields

class ProductTemplate(models.Model):
    _inherit = "product.template"

    elmis_code = fields.Char(
        string="eLMIS Code",
        help="OpenLMIS product code for integration.",
        index=True,
        copy=False,
    )

    is_prepack = fields.Boolean(
        string="Is Prepack?",
        default=False
    )

    bulk_product_id = fields.Many2one(
        'product.product',
        string="Bulk Product",
        domain="[('type', 'in', ['product', 'consu'])]",
        help="The bulk product used to create this prepack"
    )

    def create_prepack_product(self):
        pass