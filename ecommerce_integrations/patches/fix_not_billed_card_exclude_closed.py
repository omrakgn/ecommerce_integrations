import json

import frappe


def execute():
	"""Exclude Closed Sales Orders from the 'Shopify Orders Not Billed' KPI.

	A Closed order is intentionally finalized regardless of billing, so it should
	not count as not-billed."""
	name = "Shopify Orders Not Billed"
	if not frappe.db.exists("Number Card", name):
		return

	filters = [
		["Sales Order", "shopify_order_id", "is", "set"],
		["Sales Order", "company", "=", "Scarnatti"],
		["Sales Order", "per_billed", "<", 100],
		["Sales Order", "docstatus", "=", 1],
		["Sales Order", "status", "!=", "Closed"],
	]
	frappe.db.set_value("Number Card", name, "filters_json", json.dumps(filters), update_modified=False)
	frappe.clear_cache()
