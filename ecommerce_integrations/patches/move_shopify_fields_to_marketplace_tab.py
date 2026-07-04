import frappe

from ecommerce_integrations.shopify.constants import (
	MARKETING_SECTION_FIELD,
	SETTING_DOCTYPE,
	SHOPIFY_TAB_FIELD,
)
from ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting import (
	setup_custom_fields,
)


def execute():
	"""Move all Shopify Sales Order custom fields into the generic 'Marketplace' tab.

	Deletes leftover structural fields from earlier attempts, then re-runs
	setup_custom_fields which now creates a single Tab Break (anchored after the
	last standard field) plus a 'Shopify' section holding the fields. Field data
	is preserved (columns are kept; only insert_after/layout changes).
	"""
	frappe.reload_doc("shopify", "doctype", "shopify_setting")

	for fieldname in (SHOPIFY_TAB_FIELD, MARKETING_SECTION_FIELD):
		name = frappe.db.get_value("Custom Field", {"dt": "Sales Order", "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)

	settings = frappe.get_doc(SETTING_DOCTYPE)
	if settings.is_enabled():
		setup_custom_fields()

	frappe.clear_cache(doctype="Sales Order")
