import json

import frappe


def execute():
	"""Exclude Closed Sales Orders from the 'Shopify Orders Not Billed' KPI.

	A Closed order is intentionally finalized regardless of billing, so it should
	not count as not-billed."""
	name = "Shopify Orders Not Billed"
	if not frappe.db.exists("Number Card", name):
		return

	# Sirket adi bu kurulumdan okunuyor. Sabit yazilirsa yama, kartin filtresini
	# baska bir kurulumda var olmayan bir sirkete cevirir ve kart sessizce sifir
	# gosterir.
	company = frappe.defaults.get_global_default("company") or frappe.db.get_value(
		"Company", {}, "name", order_by="creation"
	)
	if not company:
		return

	filters = [
		["Sales Order", "shopify_order_id", "is", "set"],
		["Sales Order", "company", "=", company],
		["Sales Order", "per_billed", "<", 100],
		["Sales Order", "docstatus", "=", 1],
		["Sales Order", "status", "!=", "Closed"],
	]
	frappe.db.set_value("Number Card", name, "filters_json", json.dumps(filters), update_modified=False)
	frappe.clear_cache()
