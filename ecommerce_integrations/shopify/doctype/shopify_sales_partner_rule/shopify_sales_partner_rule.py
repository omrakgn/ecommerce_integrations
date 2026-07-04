# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe.model.document import Document

# Match dimension -> the Sales Order field that holds that value.
_DIMENSION_FIELD = {
	"UTM Source": "shopify_utm_source",
	"UTM Campaign": "shopify_utm_campaign",
	"UTM Medium": "shopify_utm_medium",
	"Marketing Channel": "shopify_marketing_channel",
}


class ShopifySalesPartnerRule(Document):
	def validate(self):
		self.mapping_value = (self.mapping_value or "").strip()
		if not self.mapping_value:
			frappe.throw("Match Value is required.")


@frappe.whitelist()
def get_observed_values(match_on: str) -> list[str]:
	"""Distinct values actually seen in Sales Orders for a match dimension.

	Powers the Match Value autocomplete so new UTM sources / campaigns / channels
	become selectable as soon as an order carries them (free text is still allowed).
	"""
	field = _DIMENSION_FIELD.get(match_on)
	if not field:
		return []

	rows = frappe.get_all(
		"Sales Order",
		filters={field: ["is", "set"]},
		fields=[field],
		group_by=field,
		order_by=field,
		limit=500,
	)
	seen = []
	for row in rows:
		value = (row.get(field) or "").strip()
		if value and value not in seen:
			seen.append(value)
	return seen
