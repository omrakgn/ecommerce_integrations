import frappe


def execute():
	"""Clear the currency on Shopify Count number cards.

	The Number Card controller auto-fills currency (EUR) on currency-bearing
	doctypes like Sales Order. For Count cards that makes the widget render the
	count as money (e.g. "€8"). Blank it so counts show as plain integers.
	"""
	count_cards = frappe.get_all(
		"Number Card", filters={"module": "shopify", "function": "Count"}, pluck="name"
	)
	for name in count_cards:
		frappe.db.set_value("Number Card", name, "currency", None, update_modified=False)

	frappe.clear_cache()
