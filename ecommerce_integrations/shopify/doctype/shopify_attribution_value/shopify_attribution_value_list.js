// Copyright (c) 2026, Frappe and contributors
// For license information, please see LICENSE

frappe.listview_settings["Shopify Attribution Value"] = {
	add_fields: ["is_mapped"],
	get_indicator: function (doc) {
		return doc.is_mapped
			? [__("Mapped"), "green", "is_mapped,=,1"]
			: [__("Unmapped"), "orange", "is_mapped,=,0"];
	},
	onload: function (listview) {
		listview.page.add_inner_button(__("Refresh from Orders"), function () {
			frappe.call({
				method: "ecommerce_integrations.shopify.doctype.shopify_attribution_value.shopify_attribution_value.refresh_now",
				freeze: true,
				freeze_message: __("Scanning Shopify orders…"),
				callback: function (r) {
					frappe.show_alert({ message: __("Attribution catalog refreshed ({0} values)", [r.message]), indicator: "green" });
					listview.refresh();
				},
			});
		});
	},
};
