# copyright 2022 Sodexis
# license OPL-1 (see license file for full copyright and licensing details).

from odoo import models
from odoo.tools import float_compare, float_is_zero


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _run_fifo(self, quantity, company):
        self.ensure_one()

        # pre check and post message and continue general flow
        svl_move = self.env.context.get("svl_move")
        if svl_move and (
            not svl_move.origin_returned_move_id
            or svl_move.origin_returned_move_id.picking_id.picking_type_code
            != "incoming"
        ):
            return super()._run_fifo(quantity, company)

        origin_move = svl_move.origin_returned_move_id
        svl_move_qty = svl_move.product_uom_qty
        origin_move_layers = origin_move.sudo().stock_valuation_layer_ids
        origin_move_remaining_qty = sum(origin_move_layers.mapped("remaining_qty"))
        digits = self.env.ref('product.decimal_product_uom').digits or 2
        if float_compare(svl_move_qty, origin_move_remaining_qty, precision_digits=digits) != 0:
            svl_move.picking_id.message_post(
                body="The product #{0} units have already been consumed in other operations. FIFO costing will be used".format(svl_move.product_id.id),
                subtype_xmlid="mail.mt_note",
            )
            return super()._run_fifo(quantity, company)


        # Find back incoming stock valuation layers (called candidates here) to value `quantity`.
        qty_to_take_on_candidates = quantity
        all_candidates = self.env['stock.valuation.layer'].sudo().search([
            ('product_id', '=', self.id),
            ('remaining_qty', '>', 0),
            ('company_id', '=', company.id),
        ])
        candidates = origin_move_layers
        remaining_candidates = all_candidates - candidates
        new_standard_price = 0
        tmp_value = 0  # to accumulate the value taken on the candidates
        for candidate in candidates:
            qty_taken_on_candidate = min(qty_to_take_on_candidates, candidate.remaining_qty)

            candidate_unit_cost = candidate.remaining_value / candidate.remaining_qty
            new_standard_price = candidate_unit_cost
            value_taken_on_candidate = qty_taken_on_candidate * candidate_unit_cost
            value_taken_on_candidate = candidate.currency_id.round(value_taken_on_candidate)
            new_remaining_value = candidate.remaining_value - value_taken_on_candidate

            candidate_vals = {
                'remaining_qty': candidate.remaining_qty - qty_taken_on_candidate,
                'remaining_value': new_remaining_value,
            }

            candidate.write(candidate_vals)

            qty_to_take_on_candidates -= qty_taken_on_candidate
            tmp_value += value_taken_on_candidate

            # TODO check later
            if float_is_zero(qty_to_take_on_candidates, precision_rounding=self.uom_id.rounding):
                if float_is_zero(candidate.remaining_qty, precision_rounding=self.uom_id.rounding):
                    next_candidates = remaining_candidates.filtered(lambda svl: svl.remaining_qty > 0)
                    new_standard_price = next_candidates and next_candidates[0].unit_cost or new_standard_price
                break

        # Update the standard price with the price of the last used candidate, if any.
        if new_standard_price and self.cost_method == 'fifo':
            self.sudo().with_company(company.id).with_context(disable_auto_svl=True).standard_price = new_standard_price

        # If there's still quantity to value but we're out of candidates, we fall in the
        # negative stock use case. We chose to value the out move at the price of the
        # last out and a correction entry will be made once `_fifo_vacuum` is called.
        vals = {}
        if float_is_zero(qty_to_take_on_candidates, precision_rounding=self.uom_id.rounding):
            vals = {
                'value': -tmp_value,
                'unit_cost': tmp_value / quantity,
            }
        else: # I don't think this will be in our case.
            assert qty_to_take_on_candidates > 0
            last_fifo_price = new_standard_price or self.standard_price
            negative_stock_value = last_fifo_price * -qty_to_take_on_candidates
            tmp_value += abs(negative_stock_value)
            vals = {
                'remaining_qty': -qty_to_take_on_candidates,
                'value': -tmp_value,
                'unit_cost': last_fifo_price,
            }
        return vals
