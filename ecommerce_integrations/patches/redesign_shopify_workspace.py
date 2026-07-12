import frappe


def execute():
	"""Re-apply the redesigned Shopify workspace layout (shortcuts on top, then KPIs,
	charts and recent lists)."""
	frappe.reload_doc("shopify", "workspace", "shopify")
	frappe.clear_cache()
