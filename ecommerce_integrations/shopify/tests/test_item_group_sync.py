# Copyright (c) 2026, Scarnatti
# See LICENSE

"""Kategori (Item Group) eslesmesi ayari.

Shopify'in `product_type` alani serbest metin ve eslesme acikken orada yazilan
her yeni deger ERPNext'te KALICI bir Item Group aciyor. Canlida boyle birikti:
bir yazim hatasi ("None Stock") ve tutarsiz adlandirma ("HYBRID"), ayrica
`product_type` bos gelen urunler kok gruba dustu (6 urun).

Bu testler ayarin iki yonu de kapattigini ve ACIKKEN bugunku davranisin
birebir korundugunu kilitliyor.

Bkz. docs/shopify-kategori-eslesmesi.md
"""

import frappe
from frappe.utils.nestedset import get_root_of

from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE
from ecommerce_integrations.shopify.product import (
	ShopifyProduct,
	_is_item_group_sync_enabled,
	map_erpnext_item_to_shopify,
)

from .utils import TestCase

BILINMEYEN_TUR = "_Test Shopify Product Type XYZ"
VARSAYILAN_GRUP = "_Test Shopify Unclassified"


class TestItemGroupSync(TestCase):
	def setUp(self):
		super().setUp()
		if not frappe.db.exists("Item Group", VARSAYILAN_GRUP):
			frappe.get_doc(
				{
					"doctype": "Item Group",
					"item_group_name": VARSAYILAN_GRUP,
					"parent_item_group": get_root_of("Item Group"),
					"is_group": 0,
				}
			).insert()
		# Her test kendi ayarini yaziyor; sonunda geri aliniyor.
		self.addCleanup(frappe.db.rollback)

	def _ayar(self, acik, varsayilan=None):
		frappe.db.set_single_value(SETTING_DOCTYPE, "sync_item_groups", 1 if acik else 0)
		frappe.db.set_single_value(SETTING_DOCTYPE, "default_item_group", varsayilan)

	def _urun(self):
		"""Ag baglantisi olmayan bir ShopifyProduct: yalniz ayar okunuyor."""
		return ShopifyProduct(product_id="1", variant_id="1")

	# --- yardimci: alan hic yazilmamissa ACIK -------------------------

	def test_yazilmamis_alan_ACIK_sayiliyor(self):
		"""Yalniz kod yuklenmesi kategori eslesmesini sessizce kapatmamali."""
		self.assertTrue(_is_item_group_sync_enabled(None))

	def test_sifir_kapali_bir_acik(self):
		self.assertFalse(_is_item_group_sync_enabled(0))
		self.assertFalse(_is_item_group_sync_enabled("0"))
		self.assertTrue(_is_item_group_sync_enabled(1))
		self.assertTrue(_is_item_group_sync_enabled("1"))

	# --- KAPALI: Shopify -> ERPNext -----------------------------------

	def test_kapaliyken_grup_YARATILMIYOR(self):
		self._ayar(acik=False, varsayilan=VARSAYILAN_GRUP)
		urun = self._urun()

		grup = urun._get_item_group(BILINMEYEN_TUR)

		self.assertEqual(grup, VARSAYILAN_GRUP)
		self.assertFalse(frappe.db.exists("Item Group", BILINMEYEN_TUR))

	def test_kapaliyken_var_olan_grup_da_KULLANILMIYOR(self):
		"""Shopify'in tur alanina hic bakilmiyor, eslesen bir gruba bile."""
		self._ayar(acik=False, varsayilan=VARSAYILAN_GRUP)
		urun = self._urun()

		grup = urun._get_item_group("_Test Item Group")

		self.assertEqual(grup, VARSAYILAN_GRUP)

	def test_kapali_ve_varsayilan_bos_ise_kok_gruba(self):
		"""Patlamiyor. Kok grup bir agac dugumu, o yuzden alan onerilir."""
		self._ayar(acik=False, varsayilan=None)
		urun = self._urun()

		self.assertEqual(urun._get_item_group(BILINMEYEN_TUR), get_root_of("Item Group"))
		self.assertFalse(frappe.db.exists("Item Group", BILINMEYEN_TUR))

	def test_kapaliyken_bos_tur_de_varsayilana(self):
		"""Canlida kok gruba dusen 6 urunun sebebi bos `product_type`."""
		self._ayar(acik=False, varsayilan=VARSAYILAN_GRUP)
		urun = self._urun()

		self.assertEqual(urun._get_item_group(None), VARSAYILAN_GRUP)
		self.assertEqual(urun._get_item_group(""), VARSAYILAN_GRUP)

	# --- ACIK: bugunku davranis birebir -------------------------------

	def test_acikken_bilinmeyen_tur_grup_ACIYOR(self):
		self._ayar(acik=True, varsayilan=VARSAYILAN_GRUP)
		urun = self._urun()

		grup = urun._get_item_group(BILINMEYEN_TUR)

		self.assertEqual(grup, BILINMEYEN_TUR)
		self.assertTrue(frappe.db.exists("Item Group", BILINMEYEN_TUR))

	def test_acikken_var_olan_grup_kullaniliyor(self):
		self._ayar(acik=True, varsayilan=VARSAYILAN_GRUP)
		urun = self._urun()

		self.assertEqual(urun._get_item_group("_Test Item Group"), "_Test Item Group")

	def test_acikken_bos_tur_kok_gruba(self):
		"""Varsayilan kategori ACIKKEN devreye GIRMIYOR: bugunku davranis."""
		self._ayar(acik=True, varsayilan=VARSAYILAN_GRUP)
		urun = self._urun()

		self.assertEqual(urun._get_item_group(None), get_root_of("Item Group"))

	# --- ERPNext -> Shopify -------------------------------------------

	def _erpnext_urunu(self):
		return frappe._dict(
			{
				"item_name": "Test Urun",
				"description": "aciklama",
				"item_group": "_Test Item Group",
				"weight_uom": None,
				"weight_per_unit": None,
				"disabled": 0,
			}
		)

	def test_kapaliyken_shopify_product_type_DOKUNULMUYOR(self):
		self._ayar(acik=False, varsayilan=VARSAYILAN_GRUP)
		shopify_urunu = frappe._dict({"product_type": "Vitrin Kategorisi"})

		map_erpnext_item_to_shopify(shopify_urunu, self._erpnext_urunu())

		self.assertEqual(shopify_urunu.product_type, "Vitrin Kategorisi")
		# Diger alanlar yine yaziliyor: kapali olan yalniz kategori.
		self.assertEqual(shopify_urunu.title, "Test Urun")

	def test_acikken_shopify_product_type_yaziliyor(self):
		self._ayar(acik=True, varsayilan=VARSAYILAN_GRUP)
		shopify_urunu = frappe._dict({"product_type": "Vitrin Kategorisi"})

		map_erpnext_item_to_shopify(shopify_urunu, self._erpnext_urunu())

		self.assertEqual(shopify_urunu.product_type, "_Test Item Group")
