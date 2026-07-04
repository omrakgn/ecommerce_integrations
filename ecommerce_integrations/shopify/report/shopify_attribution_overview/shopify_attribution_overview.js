// Copyright (c) 2026, Frappe and contributors
// For license information, please see LICENSE

const DIMENSION_FIELD = {
	"Marketing Channel": "shopify_marketing_channel",
	"UTM Source": "shopify_utm_source",
	"UTM Campaign": "shopify_utm_campaign",
	"UTM Medium": "shopify_utm_medium",
};

frappe.query_reports["Shopify Attribution Overview"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "dimension",
			label: __("Dimension"),
			fieldtype: "Select",
			options: ["Marketing Channel", "UTM Source", "UTM Campaign", "UTM Medium"],
			default: "Marketing Channel",
			reqd: 1,
		},
		{
			fieldname: "mapped_status",
			label: __("Mapped Status"),
			fieldtype: "Select",
			options: ["", "Mapped", "Unmapped"],
			default: "",
		},
	],

	// Make the Value cell link to the underlying Sales Orders (same attribution
	// value + the report's date range).
	formatter: function (value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname !== "value" || !data || !data.value) {
			return formatted;
		}

		const report = frappe.query_report;
		const dimension = report.get_filter_value("dimension") || "Marketing Channel";
		const field = DIMENSION_FIELD[dimension] || "shopify_marketing_channel";
		const from_date = report.get_filter_value("from_date");
		const to_date = report.get_filter_value("to_date");

		const params = new URLSearchParams();
		params.append(field, data.value);
		if (from_date && to_date) {
			params.append("transaction_date", JSON.stringify(["between", [from_date, to_date]]));
		}

		const url = `/app/sales-order?${params.toString()}`;
		return `<a href="${url}">${frappe.utils.escape_html(data.value)}</a>`;
	},
};
