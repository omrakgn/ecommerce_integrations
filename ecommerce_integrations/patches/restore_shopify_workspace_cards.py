import frappe


def execute():
	"""Re-apply the Shopify workspace: KPI number cards back on (the Group By charts
	that broke frappe-charts stay off), Revenue Trend kept, error-log quick list removed."""
	frappe.reload_doc("shopify", "workspace", "shopify", force=True)
	frappe.clear_cache()
