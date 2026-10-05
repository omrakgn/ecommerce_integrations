import frappe

from ecommerce_integrations.patches import fix_sales_order_marketplace_field_order
from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE
from ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting import (
	setup_custom_fields,
)


def execute():
	"""Create the Shopify storefront locale / country fields on Sales Order.

	setup_custom_fields() is idempotent. Sales Order has a pinned field order
	(Property Setter); the marketplace field-order patch, whose block now lists
	the two new fields, puts them in the Marketplace tab after the referring site.
	It only rewrites that Property Setter and gives the same result every run.

	Existing orders are not filled here: that writes to submitted documents and
	is run from the console after a dry run (shopify/storefront.py::backfill).
	"""
	frappe.reload_doc("shopify", "doctype", "shopify_setting")

	settings = frappe.get_doc(SETTING_DOCTYPE)
	if not settings.is_enabled():
		return
	setup_custom_fields()
	fix_sales_order_marketplace_field_order.execute()
