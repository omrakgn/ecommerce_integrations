# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import frappe

from ecommerce_integrations.shopify.inventory import get_product_bundle_inventory_levels

from .utils import TestCase


class TestBundleInventory(TestCase):
	"""Regression tests for Product Bundle stock sync (ERPNext -> Shopify).

	Locks in two things that broke in production:

	1. The naming gotcha — a Product Bundle's document *name* can differ from its
	   ``new_item_code`` (Shopify-created bundles are named by a numeric id). The
	   component lookup must key on ``Product Bundle.name``, not the item code, or
	   the bundle is silently skipped and never synced.
	2. The buildable quantity is ``floor(component_stock / bundle_qty)`` — e.g. 6
	   mattresses with a 2x bundle => 3 buildable, not 4.
	"""

	WAREHOUSE = "_Test Warehouse - _TC"

	def setUp(self):
		super().setUp()

		self.component = self._make_stock_item("_Test Bundle Component")
		self.bundle_item = self._make_non_stock_item("_Test DUO Bundle Item")

		# Product Bundle with a document name that differs from new_item_code, to
		# reproduce the real Shopify-created data (name == numeric variant id).
		self.bundle_name = "_Test Bundle 999999999999"
		self._make_product_bundle(self.bundle_name, self.bundle_item, self.component, qty=2)

		# Ecommerce Item mapping the bundle to a Shopify variant.
		self.ecom_item = self._make_ecommerce_item(self.bundle_item, variant_id="999999999999")

	# --- tests -------------------------------------------------------------

	def test_bundle_qty_is_floored_min_of_components(self):
		"""6 in stock, 2 per bundle => 3 buildable (not 4)."""
		self._set_stock(self.component, self.WAREHOUSE, 6)

		levels = self._bundle_levels()
		self.assertEqual(len(levels), 1)
		self.assertEqual(levels[0].item_code, self.bundle_item)
		self.assertEqual(levels[0].warehouse, self.WAREHOUSE)
		self.assertEqual(levels[0].actual_qty, 3)

	def test_odd_stock_floors_down(self):
		"""7 in stock, 2 per bundle => floor(3.5) == 3."""
		self._set_stock(self.component, self.WAREHOUSE, 7)
		self.assertEqual(self._bundle_levels()[0].actual_qty, 3)

	def test_zero_component_stock_gives_zero(self):
		self._set_stock(self.component, self.WAREHOUSE, 0)
		self.assertEqual(self._bundle_levels()[0].actual_qty, 0)

	def test_reserved_qty_reduces_buildable(self):
		"""actual 6, reserved 2 => available 4 => floor(4/2) == 2."""
		self._set_stock(self.component, self.WAREHOUSE, 6, reserved=2)
		self.assertEqual(self._bundle_levels()[0].actual_qty, 2)

	# --- helpers -----------------------------------------------------------

	def _bundle_levels(self):
		# inventory_synced_on left blank so the "changed since last sync" guard
		# never short-circuits and the row is always emitted.
		frappe.db.set_value("Ecommerce Item", self.ecom_item, "inventory_synced_on", None)
		return [
			d
			for d in get_product_bundle_inventory_levels((self.WAREHOUSE,), "shopify")
			if d.item_code == self.bundle_item
		]

	def _make_stock_item(self, item_code):
		if not frappe.db.exists("Item", item_code):
			frappe.get_doc({
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"item_group": "_Test Item Group",
				"stock_uom": "Nos",
				"is_stock_item": 1,
			}).insert(ignore_permissions=True)
		return item_code

	def _make_non_stock_item(self, item_code):
		if not frappe.db.exists("Item", item_code):
			frappe.get_doc({
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"item_group": "_Test Item Group",
				"stock_uom": "Nos",
				"is_stock_item": 0,
			}).insert(ignore_permissions=True)
		return item_code

	def _make_product_bundle(self, name, new_item_code, component, qty):
		if frappe.db.exists("Product Bundle", name):
			return
		bundle = frappe.get_doc({
			"doctype": "Product Bundle",
			"new_item_code": new_item_code,
			"items": [{"item_code": component, "qty": qty}],
		}).insert(ignore_permissions=True)
		# Force name != new_item_code to mirror Shopify-created bundles.
		if bundle.name != name:
			frappe.rename_doc("Product Bundle", bundle.name, name, force=True)

	def _make_ecommerce_item(self, item_code, variant_id):
		existing = frappe.db.exists(
			"Ecommerce Item", {"integration": "shopify", "erpnext_item_code": item_code}
		)
		if existing:
			return existing
		return frappe.get_doc({
			"doctype": "Ecommerce Item",
			"integration": "shopify",
			"erpnext_item_code": item_code,
			"integration_item_code": variant_id,
			"variant_id": variant_id,
		}).insert(ignore_permissions=True).name

	def _set_stock(self, item_code, warehouse, actual, reserved=0):
		name = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse})
		if name:
			frappe.db.set_value(
				"Bin", name, {"actual_qty": actual, "reserved_qty": reserved}, update_modified=True
			)
		else:
			frappe.get_doc({
				"doctype": "Bin",
				"item_code": item_code,
				"warehouse": warehouse,
				"stock_uom": "Nos",
				"actual_qty": actual,
				"reserved_qty": reserved,
			}).insert(ignore_permissions=True)
