import json

import frappe

# The Shopify block, in display order, that must sit inside the Marketplace tab.
SHOPIFY_TAB_BLOCK = [
	"marketplace_tab",  # Tab Break — opens the "Marketplace" tab
	"marketplace_shopify_section",  # Section Break — "Shopify" header
	"shopify_order_id",
	"shopify_order_number",
	"shopify_order_status",
	"shopify_discount_codes",
	"shopify_payment_method",
	"shopify_marketing_channel",
	"shopify_utm_source",
	"shopify_utm_medium",
	"shopify_utm_campaign",
	"shopify_utm_content",
	"shopify_utm_term",
	"shopify_landing_site",
	"shopify_referring_site",
]


def execute():
	"""Relocate the Shopify fields into the Marketplace tab within the pinned field_order.

	Sales Order has a `field_order` Property Setter (created via Customize Form) that
	hard-pins the whole field sequence and overrides `insert_after`. That is why the
	Shopify fields stayed at the top of the Details tab and the new Marketplace tab
	never rendered. We surgically rewrite that list — pull the Shopify block out of
	its old positions and reinsert it (tab + section + fields) right before the native
	Connections tab — instead of deleting the Property Setter (which would also undo
	any other Customize Form field ordering).
	"""
	ps_name = frappe.db.get_value(
		"Property Setter", {"doc_type": "Sales Order", "property": "field_order"}, "name"
	)
	if not ps_name:
		# No pinned order — insert_after already governs, nothing to do.
		return

	ps = frappe.get_doc("Property Setter", ps_name)
	try:
		order = json.loads(ps.value)
	except (TypeError, ValueError):
		return

	# Only place fields that actually exist as custom fields.
	block = [
		f
		for f in SHOPIFY_TAB_BLOCK
		if frappe.db.exists("Custom Field", {"dt": "Sales Order", "fieldname": f})
	]
	if not block:
		return

	remaining = [f for f in order if f not in block]

	if "connections_tab" in remaining:
		pos = remaining.index("connections_tab")  # keep Connections as the last tab
	else:
		pos = len(remaining)

	new_order = remaining[:pos] + block + remaining[pos:]

	ps.value = json.dumps(new_order)
	ps.save(ignore_permissions=True)
	frappe.clear_cache(doctype="Sales Order")
