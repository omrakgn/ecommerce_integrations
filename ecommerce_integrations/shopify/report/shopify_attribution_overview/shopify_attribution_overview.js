// Copyright (c) 2026, Frappe and contributors
// For license information, please see LICENSE

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
};
