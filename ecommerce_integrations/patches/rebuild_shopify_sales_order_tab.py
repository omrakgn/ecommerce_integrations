import frappe

from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE
from ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting import (
	setup_custom_fields,
)


def execute():
	"""Rebuild the Shopify Sales Order custom fields so they land in the Marketplace tab.

	Updating `insert_after` on an existing Custom Field does NOT reposition it — the
	stored `idx` is kept, so the fields stayed at their old spot while the empty tab
	sat at the end. Deleting and recreating them recomputes idx from insert_after.

	Deleting a Custom Field does NOT drop its database column, so field DATA
	(shopify_order_id, backfilled marketing channel, etc.) is preserved; only the
	field definition/position is rebuilt.
	"""
	frappe.reload_doc("shopify", "doctype", "shopify_setting")

	stale = frappe.get_all(
		"Custom Field",
		filters={"dt": "Sales Order", "fieldname": ["like", "shopify%"]},
		pluck="name",
	)
	stale += frappe.get_all(
		"Custom Field",
		filters={"dt": "Sales Order", "fieldname": ["like", "marketplace%"]},
		pluck="name",
	)
	for name in stale:
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)

	settings = frappe.get_doc(SETTING_DOCTYPE)
	if settings.is_enabled():
		setup_custom_fields()

	frappe.clear_cache(doctype="Sales Order")
