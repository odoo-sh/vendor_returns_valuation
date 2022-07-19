# copyright 2022 Sodexis
# license OPL-1 (see license file for full copyright and licensing details).

from odoo import models


class StockMove(models.Model):
    _inherit = "stock.move"

    def _create_out_svl(self, forced_quantity=None):
        res = self.env["stock.valuation.layer"]
        for move in self:
            move = move.with_context(svl_move=move)
            res |= super(StockMove, move)._create_out_svl(
                forced_quantity=forced_quantity
            )
        return res
