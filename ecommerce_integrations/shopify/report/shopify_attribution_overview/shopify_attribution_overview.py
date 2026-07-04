# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe import _

_DIMENSION_FIELD = {
	"Marketing Channel": "shopify_marketing_channel",
	"UTM Source": "shopify_utm_source",
	"UTM Campaign": "shopify_utm_campaign",
	"UTM Medium": "shopify_utm_medium",
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	dimension = filters.get("dimension") or "Marketing Channel"
	field = _DIMENSION_FIELD.get(dimension, "shopify_marketing_channel")

	conditions = [
		"shopify_order_id IS NOT NULL",
		"shopify_order_id != ''",
		f"`{field}` IS NOT NULL",
		f"`{field}` != ''",
		"docstatus < 2",
	]
	params = {}
	if filters.get("from_date"):
		conditions.append("transaction_date >= %(from_date)s")
		params["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("transaction_date <= %(to_date)s")
		params["to_date"] = filters.to_date

	rows = frappe.db.sql(
		f"""
		SELECT `{field}` AS value,
		       COUNT(*) AS orders,
		       SUM(grand_total) AS total_sales,
		       SUM(total_commission) AS total_commission
		FROM `tabSales Order`
		WHERE {" AND ".join(conditions)}
		GROUP BY `{field}`
		ORDER BY orders DESC
		""",
		params,
		as_dict=True,
	)

	rules = _rules_for(dimension)
	mapped_status = filters.get("mapped_status")

	data = []
	for row in rows:
		partner = rules.get((row.value or "").strip().lower())
		is_mapped = bool(partner)
		if mapped_status == "Mapped" and not is_mapped:
			continue
		if mapped_status == "Unmapped" and is_mapped:
			continue
		data.append(
			{
				"value": row.value,
				"orders": row.orders,
				"total_sales": row.total_sales,
				"total_commission": row.total_commission,
				"is_mapped": _("Yes") if is_mapped else _("No"),
				"sales_partner": partner,
			}
		)

	columns = [
		{"label": _(dimension), "fieldname": "value", "fieldtype": "Data", "width": 240},
		{"label": _("Orders"), "fieldname": "orders", "fieldtype": "Int", "width": 90},
		{"label": _("Total Sales"), "fieldname": "total_sales", "fieldtype": "Currency", "width": 130},
		{"label": _("Total Commission"), "fieldname": "total_commission", "fieldtype": "Currency", "width": 150},
		{"label": _("Mapped"), "fieldname": "is_mapped", "fieldtype": "Data", "width": 90},
		{
			"label": _("Sales Partner"),
			"fieldname": "sales_partner",
			"fieldtype": "Link",
			"options": "Sales Partner",
			"width": 180,
		},
	]
	return columns, data


def _rules_for(dimension: str) -> dict:
	rules = frappe.get_all(
		"Shopify Sales Partner Rule",
		filters={"mapping_type": dimension, "enabled": 1},
		fields=["mapping_value", "sales_partner"],
	)
	return {(r.mapping_value or "").strip().lower(): r.sales_partner for r in rules if r.mapping_value}
