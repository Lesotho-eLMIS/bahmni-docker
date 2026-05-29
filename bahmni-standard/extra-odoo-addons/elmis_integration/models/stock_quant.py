from odoo import api, fields, models


class StockQuant(models.Model):
    _inherit = "stock.quant"

    elmis_program_ids = fields.Many2many(
        "elmis.program",
        compute="_compute_elmis_program_ids",
        search="_search_elmis_program_ids",
        string="eLMIS Programs",
        readonly=True,
    )

    @api.depends("product_id.elmis_program_ids")
    def _compute_elmis_program_ids(self):
        for quant in self:
            quant.elmis_program_ids = quant.product_id.elmis_program_ids

    def _search_elmis_program_ids(self, operator, value):
        return [("product_id.elmis_program_ids", operator, value)]
