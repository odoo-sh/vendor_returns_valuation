# copyright 2022 Sodexis
# license OPL-1 (see license file for full copyright and licensing details).

from odoo import models
from odoo.tools import float_compare, float_is_zero



# === Extend product.product ===
class ProductProduct(models.Model):
    _inherit = "product.product"

    def _run_fifo(self, quantity, company):
        # Ensure we have only one product record in self
        self.ensure_one()

        # Only process fifo products.
        # If costing is not fifo, then give control to base method
        # to process it using the general flow.
        if self.cost_method != 'fifo':
            return super()._run_fifo(quantity, company)

        # Grab the move that we are actually creating the valuation for.
        # It will be in the context that we added in [[stock_move.py#svl_move_context]]
        svl_move = self.env.context.get("svl_move")

        # Our specs is to process vendor returns only.
        # In the current move (return move) odoo will add a reference to the origin move
        # that it is returned from on the field **orgin_returned_move_id**.
        # If the current move doesn't hold value in that field. It is not considered as retrun
        # then we give the control to base method.
        # If the current move hold the value in origin returned move, then
        # we need check the origin is of type **incoming** (Receipt). If not, then give
        # control to base method to process it using the general flow.
        if svl_move and (
            not svl_move.origin_returned_move_id
            or svl_move.origin_returned_move_id.picking_id.picking_type_code
            != "incoming"
        ):
            return super()._run_fifo(quantity, company)

        # === Check whether origin move's quantity was used. ===

        # Origin move from which the current move is created
        origin_move = svl_move.origin_returned_move_id
        # Current move quantity (quantity we are returning)
        svl_move_qty = svl_move.product_uom_qty
        # Origin move's stock valuations
        origin_move_layers = origin_move.sudo().stock_valuation_layer_ids
        # Origin move's total available quantity
        origin_move_remaining_qty = sum(origin_move_layers.mapped("remaining_qty"))
        # Product UOM decimal accuracy for rounding move quantity and origin move quantity
        digits = self.env.ref('product.decimal_product_uom').digits or 2
        # Comparing the current move quantity and available origin move quantity.
        # Result of float_compare should be zero. It means both values are same.
        # As per our specs, we can return the origin move as the quantity is matched.
        # If the result is not zero, then we have to post the message on the return picking
        # and give control to base metod.
        if float_compare(svl_move_qty, origin_move_remaining_qty, precision_digits=digits) != 0:
            svl_move.picking_id.message_post(
                body="Some or all units for product {0} (ID: {1}) have already been consumed in other operations. FIFO costing will be used.".format(svl_move.product_id.default_code, svl_move.product_id.id),
                subtype_xmlid="mail.mt_note",
            )
            return super()._run_fifo(quantity, company)


        # === Find back incoming stock valuation layers (called candidates here) to value `quantity`.===

        # Quantities we are returning
        qty_to_take_on_candidates = quantity

        # All stock valuation layers for the product that have remaining qty
        all_candidates = self.env['stock.valuation.layer'].sudo().search([
            ('product_id', '=', self.id),
            ('remaining_qty', '>', 0),
            ('company_id', '=', company.id),
        ])

        # We consider here that our origin move layers only.
        # As we are going to return the products that we received in the current Receipt.
        # In this place odoo will consider all the candidates (all stock valuation for that product) in general flow.
        candidates = origin_move_layers

        # Remaining candidates except origin move candidates.
        # We will use that to compute cost price of the product.
        # It is as same as the base method
        remaining_candidates = all_candidates - candidates

        # new standard_price computation to be updated on the product
        new_standard_price = 0

        # to accumulate the value taken on the candidates
        tmp_value = 0

        # Loop over the origin move values and deduct the stock from it as needed for the return move
        for candidate in candidates:

            # Qty to be returned. It is min value of the available in current svl or the qty we wish to return
            qty_taken_on_candidate = min(qty_to_take_on_candidates, candidate.remaining_qty)

            # unit cost of the svl
            candidate_unit_cost = candidate.remaining_value / candidate.remaining_qty

            # current svl unit cost is considered as the new standard price for now.
            new_standard_price = candidate_unit_cost

            # values to taken from the current candidate.
            value_taken_on_candidate = qty_taken_on_candidate * candidate_unit_cost
            value_taken_on_candidate = candidate.currency_id.round(value_taken_on_candidate)

            # new remaining value for the current candidate
            new_remaining_value = candidate.remaining_value - value_taken_on_candidate

            # values to be updated on the current candidate
            candidate_vals = {
                'remaining_qty': candidate.remaining_qty - qty_taken_on_candidate,
                'remaining_value': new_remaining_value,
            }

            # updating current candidate with the candidate values
            candidate.write(candidate_vals)

            # updating the quantites to return from the quantity returned using the current candidate
            qty_to_take_on_candidates -= qty_taken_on_candidate
            tmp_value += value_taken_on_candidate

            # **float_is_zero** is used to find whether the given float is zero or not.
            # if zero, it will return True else return False
            # here we check that all the quantites that we returning is returned.
            # And all the quantities are used in the current svl in the return process
            # if so, the we will update the new_standard_price using the next candidates records
            if float_is_zero(qty_to_take_on_candidates, precision_rounding=self.uom_id.rounding):
                if float_is_zero(candidate.remaining_qty, precision_rounding=self.uom_id.rounding):
                    next_candidates = remaining_candidates.filtered(lambda svl: svl.remaining_qty > 0)
                    new_standard_price = next_candidates and next_candidates[0].unit_cost or new_standard_price
                break

        # Update the standard price of the product with the price of the last used candidate (new_standard_price), if any.
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

        # returns the value of new valuation to be created for the returned move from the origin move.
        return vals
