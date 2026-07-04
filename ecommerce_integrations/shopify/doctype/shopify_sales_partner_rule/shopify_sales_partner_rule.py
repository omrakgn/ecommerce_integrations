# Copyright (c) 2026, Frappe and contributors
# For license information, please see LICENSE

import frappe
from frappe.model.document import Document


class ShopifySalesPartnerRule(Document):
	def validate(self):
		self.mapping_value = (self.mapping_value or "").strip()
		if not self.mapping_value:
			frappe.throw("Match Value is required.")
