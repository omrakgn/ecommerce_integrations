import frappe


def execute():
	"""Clear currency + report_function on Shopify Count number cards.

	The Number Card controller auto-fills currency (EUR) and report_function (Sum)
	on currency-bearing doctypes like Sales Order. For Count cards the widget then
	renders the count as money (e.g. "€8"). Blanking both makes counts show as
	plain integers, matching the standard ERPNext count cards.
	"""
	count_cards = frappe.get_all(
		"Number Card", filters={"module": "shopify", "function": "Count"}, pluck="name"
	)
	for name in count_cards:
		frappe.db.set_value(
			"Number Card", name, {"currency": None, "report_function": None}, update_modified=False
		)

	frappe.clear_cache()
