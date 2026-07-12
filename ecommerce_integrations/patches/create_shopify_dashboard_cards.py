import json

import frappe

COMPANY = "Scarnatti"

_SHOPIFY_SO = [
	["Sales Order", "shopify_order_id", "is", "set"],
	["Sales Order", "company", "=", COMPANY],
	["Sales Order", "docstatus", "<", 2],
]


def _this_month(base):
	return base + [["Sales Order", "transaction_date", "Timespan", "this month"]]


NUMBER_CARDS = [
	# Operations
	dict(
		label="Shopify Integration Errors (This Week)",
		document_type="Ecommerce Integration Log",
		function="Count",
		filters=[
			["Ecommerce Integration Log", "status", "=", "Error"],
			["Ecommerce Integration Log", "creation", "Timespan", "this week"],
		],
	),
	dict(
		label="Shopify Orders To Deliver",
		document_type="Sales Order",
		function="Count",
		filters=[
			["Sales Order", "shopify_order_id", "is", "set"],
			["Sales Order", "company", "=", COMPANY],
			["Sales Order", "status", "=", "To Deliver"],
			["Sales Order", "docstatus", "=", 1],
		],
	),
	dict(
		label="Shopify Orders Not Billed",
		document_type="Sales Order",
		function="Count",
		filters=[
			["Sales Order", "shopify_order_id", "is", "set"],
			["Sales Order", "company", "=", COMPANY],
			["Sales Order", "per_billed", "<", 100],
			["Sales Order", "docstatus", "=", 1],
		],
	),
	dict(
		label="Shopify Unmapped Attribution",
		document_type="Shopify Attribution Value",
		function="Count",
		filters=[["Shopify Attribution Value", "is_mapped", "=", 0]],
	),
	# Sales (this month)
	dict(
		label="Shopify Orders (This Month)",
		document_type="Sales Order",
		function="Count",
		filters=_this_month(_SHOPIFY_SO),
	),
	dict(
		label="Shopify Revenue (This Month)",
		document_type="Sales Order",
		function="Sum",
		aggregate_field="grand_total",
		filters=_this_month(_SHOPIFY_SO),
	),
	dict(
		label="Shopify Avg Order Value (This Month)",
		document_type="Sales Order",
		function="Average",
		aggregate_field="grand_total",
		filters=_this_month(_SHOPIFY_SO),
	),
	dict(
		label="Shopify Commission (This Month)",
		document_type="Sales Order",
		function="Sum",
		aggregate_field="total_commission",
		filters=_this_month(_SHOPIFY_SO),
	),
]

DASHBOARD_CHARTS = [
	dict(
		chart_name="Shopify Revenue Trend",
		chart_type="Sum",
		document_type="Sales Order",
		based_on="transaction_date",
		value_based_on="grand_total",
		timeseries=1,
		time_interval="Monthly",
		timespan="Last Year",
		type="Line",
		filters=_SHOPIFY_SO,
	),
	dict(
		chart_name="Shopify Orders by Channel",
		chart_type="Group By",
		document_type="Sales Order",
		group_by_type="Count",
		group_by_based_on="shopify_marketing_channel",
		type="Donut",
		number_of_groups=10,
		filters=_SHOPIFY_SO,
	),
	dict(
		chart_name="Shopify Orders by Status",
		chart_type="Group By",
		document_type="Sales Order",
		group_by_type="Count",
		group_by_based_on="status",
		type="Donut",
		number_of_groups=8,
		filters=_SHOPIFY_SO,
	),
	dict(
		chart_name="Shopify Orders by Payment Method",
		chart_type="Group By",
		document_type="Sales Order",
		group_by_type="Count",
		group_by_based_on="shopify_payment_method",
		type="Donut",
		number_of_groups=10,
		filters=_SHOPIFY_SO,
	),
	dict(
		chart_name="Shopify Commission by Partner",
		chart_type="Group By",
		document_type="Sales Order",
		group_by_type="Sum",
		aggregate_function_based_on="total_commission",
		group_by_based_on="sales_partner",
		type="Bar",
		number_of_groups=10,
		filters=_SHOPIFY_SO + [["Sales Order", "sales_partner", "is", "set"]],
	),
	dict(
		chart_name="Shopify Orders by UTM Source",
		chart_type="Group By",
		document_type="Sales Order",
		group_by_type="Count",
		group_by_based_on="shopify_utm_source",
		type="Bar",
		number_of_groups=10,
		filters=_SHOPIFY_SO,
	),
]


def execute():
	for card in NUMBER_CARDS:
		try:
			if frappe.db.exists("Number Card", card["label"]):
				continue
			doc = frappe.get_doc(
				{
					"doctype": "Number Card",
					"label": card["label"],
					"module": "shopify",
					"type": "Document Type",
					"document_type": card["document_type"],
					"function": card["function"],
					"aggregate_function_based_on": card.get("aggregate_field"),
					"filters_json": json.dumps(card["filters"]),
					"is_public": 1,
					"show_percentage_stats": 0,
				}
			).insert(ignore_permissions=True)
			# Count cards must NOT carry a currency, otherwise the widget renders
			# the count as money (e.g. "€8"). The controller auto-fills currency on
			# currency-bearing doctypes, so clear it back out for Count cards.
			if card["function"] == "Count":
				frappe.db.set_value("Number Card", doc.name, "currency", None, update_modified=False)
		except Exception:
			frappe.log_error(title=f"Shopify dashboard: number card '{card['label']}'", message=frappe.get_traceback())

	for chart in DASHBOARD_CHARTS:
		try:
			if frappe.db.exists("Dashboard Chart", chart["chart_name"]):
				continue
			doc = {
				"doctype": "Dashboard Chart",
				"chart_name": chart["chart_name"],
				"module": "shopify",
				"chart_type": chart["chart_type"],
				"document_type": chart["document_type"],
				"type": chart["type"],
				"filters_json": json.dumps(chart["filters"]),
				"is_public": 1,
			}
			for key in (
				"based_on",
				"value_based_on",
				"timeseries",
				"time_interval",
				"timespan",
				"group_by_type",
				"group_by_based_on",
				"aggregate_function_based_on",
				"number_of_groups",
			):
				if key in chart:
					doc[key] = chart[key]
			frappe.get_doc(doc).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title=f"Shopify dashboard: chart '{chart['chart_name']}'", message=frappe.get_traceback())

	frappe.db.commit()

	# Apply the updated workspace layout (content + card/chart/quick-list rows).
	frappe.reload_doc("shopify", "workspace", "shopify")
	frappe.clear_cache()
