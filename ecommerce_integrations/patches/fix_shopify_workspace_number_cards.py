import frappe


def execute():
	"""Re-apply the Shopify workspace after fixing the number_cards child field name
	(number_card -> number_card_name), so the KPI cards actually bind and render."""
	frappe.reload_doc("shopify", "workspace", "shopify")
	frappe.clear_cache()
