# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe.model.document import Document

# Catalog dimension -> (Sales Order field, matching Sales Partner Rule mapping_type)
_DIMENSIONS = {
	"Marketing Channel": "shopify_marketing_channel",
	"UTM Source": "shopify_utm_source",
	"UTM Campaign": "shopify_utm_campaign",
	"UTM Medium": "shopify_utm_medium",
}


class ShopifyAttributionValue(Document):
	pass


def refresh_attribution_values():
	"""Rebuild the attribution catalog from Shopify Sales Orders.

	For each dimension, group orders by the stored value and upsert one catalog
	row per distinct value with order count, total sales, first/last seen and
	whether a Sales Partner Rule already targets it. Safe to run repeatedly
	(idempotent upsert); called daily by the scheduler and on demand.
	"""
	for dimension, field in _DIMENSIONS.items():
		rows = frappe.db.sql(
			f"""
			SELECT `{field}` AS value,
			       COUNT(*) AS order_count,
			       SUM(grand_total) AS total_sales,
			       MIN(creation) AS first_seen,
			       MAX(creation) AS last_seen
			FROM `tabSales Order`
			WHERE shopify_order_id IS NOT NULL AND shopify_order_id != ''
			  AND `{field}` IS NOT NULL AND `{field}` != ''
			GROUP BY `{field}`
			""",
			as_dict=True,
		)

		rules = _rules_for(dimension)
		seen_values = set()

		for row in rows:
			value = (row.value or "").strip()
			if not value:
				continue
			seen_values.add(value)
			partner = rules.get(value.lower())

			payload = {
				"order_count": row.order_count,
				"total_sales": row.total_sales,
				"first_seen": row.first_seen,
				"last_seen": row.last_seen,
				"is_mapped": 1 if partner else 0,
				"sales_partner": partner,
			}

			existing = frappe.db.get_value(
				"Shopify Attribution Value", {"dimension": dimension, "value": value}, "name"
			)
			if existing:
				frappe.db.set_value("Shopify Attribution Value", existing, payload, update_modified=False)
			else:
				frappe.get_doc(
					{
						"doctype": "Shopify Attribution Value",
						"dimension": dimension,
						"value": value,
						**payload,
					}
				).insert(ignore_permissions=True)

		# Drop catalog rows whose value no longer appears in any order.
		stale = frappe.get_all(
			"Shopify Attribution Value",
			filters={"dimension": dimension, "value": ["not in", list(seen_values) or [""]]},
			pluck="name",
		)
		for name in stale:
			frappe.delete_doc("Shopify Attribution Value", name, ignore_permissions=True, force=True)

	frappe.db.commit()


def _rules_for(dimension: str) -> dict:
	"""lowercased mapping_value -> sales_partner for enabled rules of this dimension."""
	rules = frappe.get_all(
		"Shopify Sales Partner Rule",
		filters={"mapping_type": dimension, "enabled": 1},
		fields=["mapping_value", "sales_partner"],
	)
	return {(r.mapping_value or "").strip().lower(): r.sales_partner for r in rules if r.mapping_value}


@frappe.whitelist()
def refresh_now():
	"""Manually refresh the catalog (list-view button)."""
	frappe.only_for(("System Manager", "Sales Manager"))
	refresh_attribution_values()
	return frappe.db.count("Shopify Attribution Value")
