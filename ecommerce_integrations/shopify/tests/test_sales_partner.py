# Copyright (c) 2026, Frappe and Contributors
# See LICENSE

import frappe

from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE
from ecommerce_integrations.shopify.order import get_sales_partner_from_mapping

from .utils import TestCase


class TestSalesPartnerRules(TestCase):
	"""Attribution -> Sales Partner matching via 'Shopify Sales Partner Rule'."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.db.set_single_value(SETTING_DOCTYPE, "enable_sales_partner_mapping", 1)

		cls._make_partner("_Test SP Meta")
		cls._make_partner("_Test SP Google")
		cls._make_partner("_Test SP Influencer")

		frappe.db.delete("Shopify Sales Partner Rule")
		cls._make_rule("disc-influencer", "Discount Code", "INFLUENCER10", "_Test SP Influencer", 15, 10)
		cls._make_rule("utm-ig", "UTM Source", "ig", "_Test SP Meta", 10, 30)
		cls._make_rule("chan-google-ads", "Marketing Channel", "Google Ads", "_Test SP Google", 8, 40)

	@staticmethod
	def _make_partner(name):
		if not frappe.db.exists("Sales Partner", name):
			frappe.get_doc(
				{"doctype": "Sales Partner", "partner_name": name, "commission_rate": 0}
			).insert(ignore_permissions=True)

	@staticmethod
	def _make_rule(rule_name, mtype, mval, sp, rate, prio):
		frappe.get_doc(
			{
				"doctype": "Shopify Sales Partner Rule",
				"rule_name": rule_name,
				"enabled": 1,
				"priority": prio,
				"mapping_type": mtype,
				"mapping_value": mval,
				"sales_partner": sp,
				"commission_rate": rate,
			}
		).insert(ignore_permissions=True)

	def _setting(self):
		return frappe.get_doc(SETTING_DOCTYPE)

	def test_utm_source_match(self):
		order = {"landing_site": "/?utm_source=ig&utm_medium=paid_social"}
		res = get_sales_partner_from_mapping(order, self._setting())
		self.assertEqual(res["sales_partner"], "_Test SP Meta")
		self.assertEqual(res["commission_rate"], 10)

	def test_marketing_channel_match(self):
		# gclid -> derived channel "Google Ads"
		order = {"landing_site": "/?gclid=abc123"}
		res = get_sales_partner_from_mapping(order, self._setting())
		self.assertEqual(res["sales_partner"], "_Test SP Google")

	def test_discount_code_wins_by_priority(self):
		# Matches both the discount rule (priority 10) and the ig rule (priority 30);
		# the lower priority number must win.
		order = {"landing_site": "/?utm_source=ig", "discount_codes": [{"code": "INFLUENCER10"}]}
		res = get_sales_partner_from_mapping(order, self._setting())
		self.assertEqual(res["sales_partner"], "_Test SP Influencer")

	def test_no_match_returns_none(self):
		order = {"landing_site": "/?utm_source=tiktok"}
		self.assertIsNone(get_sales_partner_from_mapping(order, self._setting()))

	def test_disabled_switch_returns_none(self):
		frappe.db.set_single_value(SETTING_DOCTYPE, "enable_sales_partner_mapping", 0)
		try:
			order = {"landing_site": "/?utm_source=ig"}
			self.assertIsNone(get_sales_partner_from_mapping(order, self._setting()))
		finally:
			frappe.db.set_single_value(SETTING_DOCTYPE, "enable_sales_partner_mapping", 1)
