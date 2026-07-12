import frappe


def execute():
	"""Re-import the Shopify workspace so the new Links section is applied."""
	frappe.reload_doc("shopify", "workspace", "shopify")
	frappe.clear_cache()
