# Copyright 2017-2020 ForgeFlow, S.L.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockRequest(models.AbstractModel):
    _name = "stock.request.abstract"
    _description = "Stock Request Template"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        warehouse = None
        if "warehouse_id" not in res and res.get("company_id"):
            warehouse = self.env["stock.warehouse"].search(
                [("company_id", "=", res["company_id"])], limit=1
            )
        if warehouse:
            res["warehouse_id"] = warehouse.id
            res["location_id"] = warehouse.lot_stock_id.id
        return res

    @api.depends(
        "product_id",
        "product_uom_id",
        "product_uom_qty",
        "product_id.product_tmpl_id.uom_id",
    )
    def _compute_product_qty(self):
        for rec in self:
            rec.product_qty = rec.product_uom_id._compute_quantity(
                rec.product_uom_qty, rec.product_id.product_tmpl_id.uom_id
            )

    name = fields.Char(copy=False, required=True, readonly=True, default="/")
    warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Warehouse",
        check_company=True,
        ondelete="cascade",
        required=True,
    )
    location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Location",
        domain="not allow_virtual_location and "
        "[('usage', 'in', ['internal', 'transit'])] or []",
        ondelete="cascade",
        required=True,
    )
    product_id = fields.Many2one(
        "product.product",
        "Product",
        domain=[("type", "in", ["product", "consu"])],
        ondelete="cascade",
        required=True,
    )
    allow_virtual_location = fields.Boolean(
        related="company_id.stock_request_allow_virtual_loc", readonly=True
    )
    allowed_uom_categ_id = fields.Many2one(related="product_id.uom_id.category_id")
    product_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="Product Unit of Measure",
        domain="[('category_id', '=?', allowed_uom_categ_id)]",
        required=True,
        default=lambda self: self._context.get("product_uom_id", False),
    )
    product_uom_qty = fields.Float(
        "Quantity",
        digits="Product Unit of Measure",
        required=True,
        help="Quantity, specified in the unit of measure indicated in the request.",
    )
    product_qty = fields.Float(
        "Real Quantity",
        compute="_compute_product_qty",
        store=True,
        copy=False,
        digits="Product Unit of Measure",
        help="Quantity in the default UoM of the product",
    )
    procurement_group_id = fields.Many2one(
        "procurement.group",
        "Procurement Group",
        help="Moves created through this stock request will be put in this "
        "procurement group. If none is given, the moves generated by "
        "procurement rules will be grouped into one big picking.",
    )
    company_id = fields.Many2one(
        "res.company", "Company", required=True, default=lambda self: self.env.company
    )
    route_id = fields.Many2one(
        "stock.route",
        string="Route",
        domain="[('id', 'in', route_ids)]",
        ondelete="restrict",
    )

    route_ids = fields.Many2many(
        "stock.route",
        string="Routes",
        compute="_compute_route_ids",
        readonly=True,
    )

    _sql_constraints = [
        ("name_uniq", "unique(name, company_id)", "Name must be unique")
    ]

    @api.depends("product_id", "warehouse_id", "location_id")
    def _compute_route_ids(self):
        route_obj = self.env["stock.route"]
        routes = route_obj.search(
            [("warehouse_ids", "in", self.mapped("warehouse_id").ids)]
        )
        routes_by_warehouse = {}
        for route in routes:
            for warehouse in route.warehouse_ids:
                routes_by_warehouse.setdefault(warehouse.id, self.env["stock.route"])
                routes_by_warehouse[warehouse.id] |= route
        for record in self:
            routes = route_obj
            if record.product_id:
                routes += record.product_id.mapped(
                    "route_ids"
                ) | record.product_id.mapped("categ_id").mapped("total_route_ids")
            if record.warehouse_id and routes_by_warehouse.get(record.warehouse_id.id):
                routes |= routes_by_warehouse[record.warehouse_id.id]
            parents = record.get_parents().ids
            # ruff: noqa: B023
            record.route_ids = routes.filtered(
                lambda r: any(p.location_dest_id.id in parents for p in r.rule_ids)
            )

    def get_parents(self):
        location = self.location_id
        result = location
        while location.location_id:
            location = location.location_id
            result |= location
        return result

    @api.constrains(
        "company_id", "product_id", "warehouse_id", "location_id", "route_id"
    )
    def _check_company_constrains(self):
        """Check if the related models have the same company"""
        for rec in self:
            if (
                rec.product_id.company_id
                and rec.product_id.company_id != rec.company_id
            ):
                raise ValidationError(
                    _(
                        "You have entered a product that is assigned "
                        "to another company."
                    )
                )
            if (
                rec.location_id.company_id
                and rec.location_id.company_id != rec.company_id
            ):
                raise ValidationError(
                    _(
                        "You have entered a location that is "
                        "assigned to another company."
                    )
                )
            if rec.warehouse_id.company_id != rec.company_id:
                raise ValidationError(
                    _(
                        "You have entered a warehouse that is "
                        "assigned to another company."
                    )
                )
            if (
                rec.route_id
                and rec.route_id.company_id
                and rec.route_id.company_id != rec.company_id
            ):
                raise ValidationError(
                    _(
                        "You have entered a route that is "
                        "assigned to another company."
                    )
                )

    @api.constrains("product_id")
    def _check_product_uom(self):
        """Check if the UoM has the same category as the
        product standard UoM"""
        if any(
            request.product_id.uom_id.category_id != request.product_uom_id.category_id
            for request in self
        ):
            raise ValidationError(
                _(
                    "You have to select a product unit of measure in the "
                    "same category than the default unit "
                    "of measure of the product"
                )
            )

    @api.constrains("product_qty")
    def _check_qty(self):
        for rec in self:
            if rec.product_qty <= 0:
                raise ValidationError(
                    _("Stock Request product quantity has to be strictly positive.")
                )

    @api.onchange("warehouse_id")
    def onchange_warehouse_id(self):
        """Finds location id for changed warehouse."""
        if self._name == "stock.request" and self.order_id:
            # When the stock request is created from an order the wh and
            # location are taken from the order and we rely on it to change
            # all request associated. Thus, no need to apply
            # the onchange, as it could lead to inconsistencies.
            return
        if self.warehouse_id:
            loc_wh = self.location_id.warehouse_id
            if self.warehouse_id != loc_wh:
                self.location_id = self.warehouse_id.lot_stock_id.id
            if self.warehouse_id.company_id != self.company_id:
                self.company_id = self.warehouse_id.company_id

    @api.onchange("location_id")
    def onchange_location_id(self):
        if self.location_id:
            loc_wh = self.location_id.warehouse_id
            if loc_wh and self.warehouse_id != loc_wh:
                self.warehouse_id = loc_wh
                self.with_context(no_change_childs=True).onchange_warehouse_id()

    @api.onchange("company_id")
    def onchange_company_id(self):
        """Sets a default warehouse when the company is changed."""
        if self.company_id and (
            not self.warehouse_id or self.warehouse_id.company_id != self.company_id
        ):
            self.warehouse_id = self.env["stock.warehouse"].search(
                [
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", self.company_id.id),
                ],
                limit=1,
            )
            self.onchange_warehouse_id()

    @api.onchange("product_id")
    def onchange_product_id(self):
        if self.product_id:
            self.product_uom_id = self.product_id.uom_id
