// Copyright (c) 2026, Frappe and contributors
// For license information, please see LICENSE

frappe.ui.form.on("Shopify Sales Partner Rule", {
	onload: function (frm) {
		load_match_value_options(frm);
	},
	mapping_type: function (frm) {
		// New dimension -> refresh suggestions, clear a now-irrelevant value.
		frm.set_value("mapping_value", "");
		load_match_value_options(frm);
	},
});

function load_match_value_options(frm) {
	if (!frm.doc.mapping_type) {
		frm.set_df_property("mapping_value", "options", "");
		return;
	}
	frappe.call({
		method: "ecommerce_integrations.shopify.doctype.shopify_sales_partner_rule.shopify_sales_partner_rule.get_observed_values",
		args: { match_on: frm.doc.mapping_type },
		callback: (r) => {
			frm.set_df_property("mapping_value", "options", (r.message || []).join("\n"));
			frm.refresh_field("mapping_value");
		},
	});
}
