import frappe


def execute():
	"""Move existing Sales Partner mappings (Shopify Setting child table) into the
	new standalone 'Shopify Sales Partner Rule' DocType."""
	frappe.reload_doc("shopify", "doctype", "shopify_sales_partner_rule")
	frappe.reload_doc("shopify", "doctype", "shopify_setting")

	if not frappe.db.table_exists("Shopify Sales Partner Mapping"):
		return

	rows = frappe.get_all(
		"Shopify Sales Partner Mapping",
		filters={"parenttype": "Shopify Setting"},
		fields=["mapping_type", "mapping_value", "sales_partner", "commission_rate"],
	)

	# Old child used "Referral/UTM"; the new rule splits matching into explicit
	# types. A plain URL/param match maps to "Referral URL".
	type_map = {"Discount Code": "Discount Code", "Referral/UTM": "Referral URL"}

	priority = 10
	for r in rows:
		if not r.mapping_value or not r.sales_partner:
			continue

		mtype = type_map.get(r.mapping_type, "Referral URL")
		base = f"{mtype} - {r.mapping_value}"[:130]
		name = base
		suffix = 1
		while frappe.db.exists("Shopify Sales Partner Rule", name):
			suffix += 1
			name = f"{base} ({suffix})"

		frappe.get_doc(
			{
				"doctype": "Shopify Sales Partner Rule",
				"rule_name": name,
				"enabled": 1,
				"priority": priority,
				"mapping_type": mtype,
				"mapping_value": r.mapping_value,
				"sales_partner": r.sales_partner,
				"commission_rate": r.commission_rate or 0,
			}
		).insert(ignore_permissions=True)
		priority += 10
