# copyright 2022 Sodexis
# license OPL-1 (see license file for full copyright and licensing details).

from odoo import models


# === Extend stock.move ===
class StockMove(models.Model):
    _inherit = "stock.move"

    def _create_out_svl(self, forced_quantity=None):
        """
        Loop over self to process moves individually.
        """
        res = self.env["stock.valuation.layer"]
        for move in self:
            # === svl_move_context ===
            # Add the current move in the context as svl_move
            move = move.with_context(svl_move=move)
            res |= super(StockMove, move)._create_out_svl(
                forced_quantity=forced_quantity
            )
        return res
