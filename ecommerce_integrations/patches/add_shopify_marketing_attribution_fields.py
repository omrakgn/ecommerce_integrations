import frappe

from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE
from ecommerce_integrations.shopify.doctype.shopify_setting.shopify_setting import (
	setup_custom_fields,
)


def execute():
	"""Create the Shopify marketing attribution custom fields on Sales Order.

	setup_custom_fields() is idempotent (create_custom_fields skips existing
	fields), so this only adds the new UTM / marketing channel fields.
	"""
	frappe.reload_doc("shopify", "doctype", "shopify_setting")

	settings = frappe.get_doc(SETTING_DOCTYPE)
	if settings.is_enabled():
		setup_custom_fields()
