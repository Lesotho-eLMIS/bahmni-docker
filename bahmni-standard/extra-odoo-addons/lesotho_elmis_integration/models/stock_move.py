from odoo import models


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_done(self, *args, **kwargs):
        if self.env.context.get("is_scrap"):
            return super()._action_done(*args, **kwargs)
        self.mapped("move_line_ids")._check_elmis_internal_transfer_ready()
        moves = super()._action_done(*args, **kwargs)
        moves.mapped("move_line_ids")._create_elmis_internal_transfer_outbox()
        return moves
