import frappe


def execute():
	"""Force-apply the simplified Shopify workspace (shortcuts + Revenue Trend + Recent
	lists). The Group By charts and number cards were dropped because frappe-charts
	renders them with NaN geometry in this install, which broke the whole panel.

	force=True because a plain reload_doc can skip re-importing an existing workspace,
	which is what left the earlier layout stuck.
	"""
	frappe.reload_doc("shopify", "workspace", "shopify", force=True)
	frappe.clear_cache()
