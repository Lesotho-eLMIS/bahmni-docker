from odoo import models, api, fields
from odoo.exceptions import ValidationError
import logging
_logger = logging.getLogger(__name__)

class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    bom_product_ids = fields.Many2many(
        comodel_name="product.product",
        compute="_compute_bom_product_ids",
        string="BoM Products",
        store=False,
    )

    @api.depends('bom_id', 'bom_id.bom_line_ids', 'bom_id.bom_line_ids.product_id')
    def _compute_bom_product_ids(self):
        for rec in self:
            rec.bom_product_ids = rec.bom_id.bom_line_ids.product_id

    def print_self(self):
        _logger.info("Printing Self: %s", self)
        """Show only bulk product lots in finished lot_id dropdown."""
        
    def _get_selected_component_lot(self):
        self.ensure_one()
        for fld in ('lot_producing_id', 'lot_production_id', 'lot_id'):
            if fld in self._fields and getattr(self, fld):
                return getattr(self, fld)
        for move in self.move_raw_ids:
            for ml in move.move_line_ids:
                if ml.lot_id:
                    return ml.lot_id
        return None

    def _clone_or_get_finished_lot(self, component_lot, finished_product):
        Lot = self.env['stock.lot']
        existing = Lot.search([
            ('name', '=', component_lot.name),
            ('product_id', '=', finished_product.id)
        ], limit=1)
        if existing:
            _logger.info("Printing Existing Batch: %s", existing.name)
            return existing
        vals = {'name': component_lot.name, 'product_id': finished_product.id}
        for fld in ('expiration_date','use_date','removal_date','alert_date'):
            if fld in component_lot._fields:
                vals[fld] = getattr(component_lot, fld)
        return Lot.create(vals)

    def button_mark_done(self):
        for mo in self:
            comp = mo._get_selected_component_lot()
            if not comp:
                continue

            fin = mo._clone_or_get_finished_lot(comp, mo.product_id)

            for move in mo.move_finished_ids.filtered(
                    lambda m: m.product_id.id == mo.product_id.id
            ):
                if move.move_line_ids:
                    move.move_line_ids.write({'lot_id': fin.id})
                else:
                    self.env['stock.move.line'].create({
                        'move_id': move.id,
                        'product_id': move.product_id.id,
                        'lot_id': fin.id,
                        'location_id': move.location_id.id,
                        'location_dest_id': move.location_dest_id.id,
                        'qty_done': move.product_uom_qty,
                    })

        return super().button_mark_done()
