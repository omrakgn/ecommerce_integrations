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
	"""Remove structural Shopify custom fields that corrupted the v15 Sales Order layout.

	Earlier versions injected a Tab Break (shopify_tab) and a Section Break
	(shopify_marketing_section) as custom fields on Sales Order. On Frappe v15 —
	where Sales Order already uses native tabs — these structural breaks reshuffle
	standard fields and break the form. We delete them and (re)create the Shopify
	fields as plain, depends_on-gated fields (no structural breaks).
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
