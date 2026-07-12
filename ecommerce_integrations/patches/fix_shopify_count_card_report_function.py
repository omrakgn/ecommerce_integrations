import frappe


def execute():
	"""Clear report_function (and currency) on Shopify Count number cards.

	The Number Card controller defaults report_function to 'Sum' and currency to
	the company currency on Sales Order cards; the widget then renders Count cards
	as money (e.g. "€8"). Blanking both makes them plain integers, matching the
	standard ERPNext count cards.
	"""
	count_cards = frappe.get_all(
		"Number Card", filters={"module": "shopify", "function": "Count"}, pluck="name"
	)
	for name in count_cards:
		frappe.db.set_value(
			"Number Card", name, {"report_function": None, "currency": None}, update_modified=False
		)

	frappe.clear_cache()
