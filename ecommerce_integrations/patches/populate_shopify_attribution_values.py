import frappe

from ecommerce_integrations.shopify.doctype.shopify_attribution_value.shopify_attribution_value import (
	refresh_attribution_values,
)


def execute():
	"""Build the Shopify Attribution Value catalog for the first time."""
	frappe.reload_doc("shopify", "doctype", "shopify_attribution_value")
	frappe.reload_doc("shopify", "doctype", "shopify_sales_partner_rule")
	refresh_attribution_values()
