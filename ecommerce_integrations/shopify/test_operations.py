"""Shopify cards of the operations board. No site needed:

python -m unittest ecommerce_integrations.shopify.test_operations
"""

import datetime
import sys
import types
import unittest
from unittest import mock

from ecommerce_integrations.shopify import operations as ops

_d = ops.frappe._dict
TODAY = datetime.date(2026, 11, 20)
NOW = datetime.datetime(2026, 11, 20, 9, 30)


def ctx():
	return _d(today=TODAY, now=NOW)


class CardTests(unittest.TestCase):
	def test_nothing_when_shopify_is_off(self):
		with mock.patch.object(ops, "enabled", return_value=False):
			self.assertEqual(ops.cards(ctx()), [])

	def test_failed_events_of_the_last_week(self):
		with mock.patch.object(ops.frappe.db, "count", return_value=3, create=True) as count:
			item = ops.failed_events(ctx())
		filters = {"integration": "shopify", "status": "Error", "creation": [">=", "2026-11-13 09:30:00"]}
		count.assert_called_once_with("Ecommerce Integration Log", filters)
		self.assertEqual(item["filters"], filters)
		self.assertEqual(item["count"], 3)
		self.assertEqual(item["doctype"], "Ecommerce Integration Log")

	def test_refunds_wait_seven_days_for_the_daily_job(self):
		rows = [
			_d(name="CN-OLD", posting_date=datetime.date(2026, 11, 12)),
			_d(name="CN-EDGE", posting_date=datetime.date(2026, 11, 13)),
			_d(name="CN-NEW", posting_date=datetime.date(2026, 11, 19)),
		]
		# The real module needs the Shopify library; the card only needs this one function.
		fake = types.ModuleType("ecommerce_integrations.shopify.refund_payment")
		fake.unpaid_credit_notes = mock.Mock(return_value=rows)
		unpaid = fake.unpaid_credit_notes
		with mock.patch.dict(sys.modules, {"ecommerce_integrations.shopify.refund_payment": fake}):
			item = ops.refunds_without_payment(ctx())
		unpaid.assert_called_once_with(days=60, limit=500)
		self.assertEqual(item["filters"], {"name": ["in", ["CN-OLD"]]})
		self.assertEqual(item["count"], 1)
		self.assertEqual(item["doctype"], "Sales Invoice")

	def test_both_cards_when_on(self):
		with (
			mock.patch.object(ops, "enabled", return_value=True),
			mock.patch.object(ops, "failed_events", return_value={"key": "a"}),
			mock.patch.object(ops, "refunds_without_payment", return_value={"key": "b"}),
		):
			self.assertEqual([c["key"] for c in ops.cards(ctx())], ["a", "b"])

	def test_hook_points_here(self):
		from ecommerce_integrations import hooks

		self.assertEqual(hooks.operations_board_sources, ["ecommerce_integrations.shopify.operations.cards"])


if __name__ == "__main__":
	unittest.main()
