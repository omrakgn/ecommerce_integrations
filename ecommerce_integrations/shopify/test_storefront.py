"""Siparişin açıldığı vitrin (Yapılacaklar 12). Örnekler canlıdaki Shopify siparişlerinden.

Siteye ihtiyaç duymuyor:

    python -m unittest ecommerce_integrations.shopify.test_storefront
"""

import importlib
import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from ecommerce_integrations.patches import fix_sales_order_marketplace_field_order as alan_sirasi
from ecommerce_integrations.shopify import storefront as sf
from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	STOREFRONT_COUNTRY_FIELD,
	STOREFRONT_LOCALE_FIELD,
)

_d = sf.frappe._dict

ULKELER = {"be": "Belgium", "nl": "Netherlands", "de": "Germany", "at": "Austria", "ie": "Ireland"}


class Bolge(unittest.TestCase):
	def test_ulke_parcasi(self):
		self.assertEqual(sf.locale_region("nl-BE"), "BE")
		self.assertEqual(sf.locale_region("de-AT"), "AT")
		self.assertEqual(sf.locale_region("en-DE"), "DE")

	def test_alt_cizgi_ve_kucuk_harf(self):
		self.assertEqual(sf.locale_region("pt_BR"), "BR")
		self.assertEqual(sf.locale_region("nl-be"), "BE")

	def test_yazi_bicimli_dil(self):
		self.assertEqual(sf.locale_region("zh-Hant-TW"), "TW")

	def test_ulkesiz(self):
		self.assertIsNone(sf.locale_region("en"))
		self.assertIsNone(sf.locale_region("es-419"), "sayısal bölge ülke değil")
		self.assertIsNone(sf.locale_region(""))
		self.assertIsNone(sf.locale_region(None))


class SahteDb:
	"""Ülke kayıtları, entegrasyon kayıtları, satış siparişleri."""

	def __init__(self, kayitlar=(), satislar=()):
		self.kayitlar = [_d(k) for k in kayitlar]
		self.satislar = [_d(s) for s in satislar]
		self.yazilan = []

	def get_value(self, doctype, name, field=None, *a, **k):
		if doctype == "Country":
			return ULKELER.get(name["code"])
		if doctype == sf.LOG_DOCTYPE:
			for kayit in self.kayitlar:
				if kayit.name == name:
					return kayit.request_data
			return None
		return None

	def get_all(self, doctype, filters=None, fields=None, pluck=None, order_by=None, limit=None, **k):
		if doctype == sf.LOG_DOCTYPE:
			satirlar = [r for r in self.kayitlar if r.method == filters["method"]]
			if limit:
				satirlar = satirlar[:limit]
			return [r[pluck] for r in satirlar]
		if doctype == "Sales Order":
			return [s for s in self.satislar if s.get(ORDER_ID_FIELD) == filters[ORDER_ID_FIELD]]
		raise AssertionError(doctype)

	def count(self, doctype, filters=None):
		return len([s for s in self.satislar if s.get(ORDER_ID_FIELD) and not s.get(STOREFRONT_LOCALE_FIELD)])

	def set_value(self, doctype, name, values, update_modified=True, **k):
		"""Gerçek veritabanı gibi: aynı işlemdeki sonraki sayım yazılanı görür."""
		self.yazilan.append((doctype, name, values, update_modified))
		for s in self.satislar:
			if s.name == name:
				s.update(values)


YONTEM = "ecommerce_integrations.shopify.order.sync_sales_order"


def kayit(ad, siparis, yontem=YONTEM):
	ham = siparis if isinstance(siparis, str) else json.dumps(siparis)
	return {"name": ad, "method": yontem, "request_data": ham}


def calistir(db, **k):
	with mock.patch.object(sf.frappe, "db", db), mock.patch.object(sf.frappe, "get_all", db.get_all):
		return sf.backfill(**k)


class Degerler(unittest.TestCase):
	def test_dil_ve_ulke(self):
		db = SahteDb()
		with mock.patch.object(sf.frappe, "db", db):
			self.assertEqual(
				sf.get_storefront({"customer_locale": "nl-BE"}),
				{STOREFRONT_LOCALE_FIELD: "nl-BE", STOREFRONT_COUNTRY_FIELD: "Belgium"},
			)

	def test_dil_yoksa_bos(self):
		db = SahteDb()
		with mock.patch.object(sf.frappe, "db", db):
			self.assertEqual(
				sf.get_storefront({"customer_locale": None}),
				{STOREFRONT_LOCALE_FIELD: "", STOREFRONT_COUNTRY_FIELD: None},
			)
			self.assertEqual(sf.get_storefront({"customer_locale": "en"})[STOREFRONT_COUNTRY_FIELD], None)

	def test_bilinmeyen_ulke_kodu(self):
		db = SahteDb()
		with mock.patch.object(sf.frappe, "db", db):
			self.assertIsNone(sf.storefront_country("xx-ZZ"))


class Doldurma(unittest.TestCase):
	def test_deneme_kipi_yazmaz_sayar(self):
		db = SahteDb(
			kayitlar=[kayit("L1", {"id": 11, "name": "#1402", "customer_locale": "nl-BE"})],
			satislar=[{"name": "SO-1", ORDER_ID_FIELD: "11"}, {"name": "SO-ESKI", ORDER_ID_FIELD: "5"}],
		)
		r = calistir(db)
		self.assertEqual(db.yazilan, [])
		self.assertEqual((r["filled"], r["left_empty"]), (1, 1))
		self.assertEqual(r["examples"], [("SO-1", "#1402", "nl-BE", "Belgium")])
		self.assertEqual(r["countries"], {"Belgium": 1})

	def test_yazar_modified_degismez(self):
		db = SahteDb(
			kayitlar=[kayit("L1", {"id": 11, "customer_locale": "de-AT"})],
			satislar=[{"name": "SO-1", ORDER_ID_FIELD: "11"}],
		)
		calistir(db, dry_run=False)
		self.assertEqual(
			db.yazilan,
			[
				(
					"Sales Order",
					"SO-1",
					{STOREFRONT_LOCALE_FIELD: "de-AT", STOREFRONT_COUNTRY_FIELD: "Austria"},
					False,
				)
			],
		)

	def test_dolu_olana_dokunmaz(self):
		db = SahteDb(
			kayitlar=[kayit("L1", {"id": 11, "customer_locale": "nl-NL"})],
			satislar=[{"name": "SO-1", ORDER_ID_FIELD: "11", STOREFRONT_LOCALE_FIELD: "nl-BE"}],
		)
		r = calistir(db, dry_run=False)
		self.assertEqual(db.yazilan, [])
		self.assertEqual((r["already"], r["filled"]), (1, 0))

	def test_ayni_siparis_bir_kez(self):
		"""Yeniden deneme aynı sipariş için ikinci kayıt bırakıyor; en yenisi geçerli."""
		db = SahteDb(
			kayitlar=[
				kayit("L2", {"id": 11, "customer_locale": "nl-NL"}),
				kayit("L1", {"id": 11, "customer_locale": "nl-BE"}),
			],
			satislar=[{"name": "SO-1", ORDER_ID_FIELD: "11"}],
		)
		calistir(db, dry_run=False)
		self.assertEqual(len(db.yazilan), 1)
		self.assertEqual(db.yazilan[0][2][STOREFRONT_LOCALE_FIELD], "nl-NL")

	def test_ayni_siparis_denemede_bir_kez_sayilir(self):
		"""Denemede yazım yok; ikinci kayıt atlanmazsa sipariş iki kez sayılırdı."""
		db = SahteDb(
			kayitlar=[
				kayit("L2", {"id": 11, "customer_locale": "nl-NL"}),
				kayit("L1", {"id": 11, "customer_locale": "nl-BE"}),
			],
			satislar=[{"name": "SO-1", ORDER_ID_FIELD: "11"}],
		)
		r = calistir(db)
		self.assertEqual((r["filled"], r["left_empty"]), (1, 0))

	def test_iptal_ve_duzeltilmis_siparisin_ikisi_de(self):
		db = SahteDb(
			kayitlar=[kayit("L1", {"id": 11, "customer_locale": "de-DE"})],
			satislar=[{"name": "SO-1", ORDER_ID_FIELD: "11"}, {"name": "SO-1-1", ORDER_ID_FIELD: "11"}],
		)
		r = calistir(db, dry_run=False)
		self.assertEqual([y[1] for y in db.yazilan], ["SO-1", "SO-1-1"])
		self.assertEqual(r["countries"], {"Germany": 2})

	def test_sayilan_ama_yazilmayanlar(self):
		db = SahteDb(
			kayitlar=[
				kayit("L1", {"id": 11}),
				kayit("L2", {"id": 12, "customer_locale": "nl-BE"}),
				kayit("L3", "{bozuk"),
				kayit("L4", {"name": "kimliksiz"}),
				kayit("L5", {"id": 13, "customer_locale": "en"}),
				kayit("L6", {"id": 14, "customer_locale": "nl-BE"}, yontem="baska.yontem"),
			],
			satislar=[{"name": "SO-1", ORDER_ID_FIELD: "11"}, {"name": "SO-3", ORDER_ID_FIELD: "13"}],
		)
		r = calistir(db, dry_run=False)
		self.assertEqual(
			(r["logs"], r["no_locale"], r["no_order"], r["unreadable"], r["filled"]), (5, 1, 1, 2, 1)
		)
		self.assertEqual(db.yazilan[0][2], {STOREFRONT_LOCALE_FIELD: "en", STOREFRONT_COUNTRY_FIELD: None})
		self.assertEqual(r["countries"], {"-": 1})
		self.assertEqual(r["left_empty"], 1, "dili olmayan SO-1 boş kalır; yazılan SO-3 sayılmaz")

	def test_sinir(self):
		db = SahteDb(
			kayitlar=[kayit("L1", {"id": 11, "customer_locale": "nl-BE"}), kayit("L2", {"id": 12})],
		)
		self.assertEqual(calistir(db, limit=1)["logs"], 1)


class AlanSirasi(unittest.TestCase):
	"""Sales Order'ın alan sırası sabit; yeni alanlar Marketplace sekmesine girmeli."""

	def test_yeni_alanlar_yonlendirenin_arkasinda(self):
		eski = [
			"customer",
			"marketplace_tab",
			"marketplace_shopify_section",
			"shopify_landing_site",
			"shopify_referring_site",
			"connections_tab",
		]
		ps = SimpleNamespace(value=json.dumps(eski), save=mock.Mock())
		db = SimpleNamespace(get_value=lambda *a, **k: "PS-1", exists=lambda *a, **k: True)
		with (
			mock.patch.object(alan_sirasi.frappe, "db", db),
			mock.patch.object(alan_sirasi.frappe, "get_doc", lambda *a, **k: ps, create=True),
			mock.patch.object(alan_sirasi.frappe, "clear_cache", lambda **k: None, create=True),
		):
			alan_sirasi.execute()
		yeni = json.loads(ps.value)
		konum = yeni.index("shopify_referring_site")
		self.assertEqual(
			yeni[konum + 1 : konum + 3], ["shopify_storefront_locale", "shopify_storefront_country"]
		)
		self.assertEqual(yeni[-1], "connections_tab")


# --- Sipariş oluştururken alanlar so_data'ya giriyor ------------------------
#
# order.py Shopify kütüphanesini ve Frappe'nin iç modüllerini yüklüyor. Sunucuda
# hepsi var; yerelde yoksa bu testin süresince sahteleri konuyor.


class _Bos(types.ModuleType):
	def __getattr__(self, ad):
		if ad.startswith("__"):
			raise AttributeError(ad)
		return mock.MagicMock(name=f"{self.__name__}.{ad}")


def _order_modulu():
	try:
		return importlib.import_module("ecommerce_integrations.shopify.order")
	except ImportError:
		pass
	sahteler = {}
	for ad in (
		"shopify",
		"shopify.collection",
		"shopify.resources",
		"ecommerce_integrations.shopify.connection",
		"ecommerce_integrations.shopify.customer",
		"ecommerce_integrations.shopify.product",
		"ecommerce_integrations.shopify.utils",
		"ecommerce_integrations.utils.price_list",
		"ecommerce_integrations.utils.taxation",
	):
		sahteler[ad] = _Bos(ad)
	with mock.patch.dict(sys.modules, sahteler):
		sys.modules.pop("ecommerce_integrations.shopify.order", None)
		return importlib.import_module("ecommerce_integrations.shopify.order")


class Durdur(Exception):
	pass


class SiparisOlusturma(unittest.TestCase):
	def test_vitrin_alanlari_siparise_yazilir(self):
		order = _order_modulu()
		yakalanan = {}

		def get_doc(veri):
			yakalanan.update(veri)
			raise Durdur

		db = SimpleNamespace(
			get_value=lambda doctype, *a, **k: "Belgium" if doctype == "Country" else None,
		)
		ayar = SimpleNamespace(
			default_customer="C", sales_order_series=None, company="X", cost_center="CC", warehouse="W"
		)
		siparis = {
			"id": 11,
			"name": "#1402",
			"created_at": "2026-10-01T10:00:00",
			"line_items": [],
			"taxes_included": True,
			"customer_locale": "nl-BE",
		}
		with (
			mock.patch.object(order.frappe, "db", db),
			mock.patch.object(order.frappe, "get_doc", get_doc),
			mock.patch.object(sf.frappe, "db", db),
			mock.patch.object(order, "get_order_items", lambda *a, **k: [{"item_code": "I"}]),
			mock.patch.object(order, "get_order_taxes", lambda *a, **k: []),
			mock.patch.object(order, "get_discount_info", lambda *a, **k: {}),
			mock.patch.object(order, "get_sales_partner_from_mapping", lambda *a, **k: None),
			mock.patch.object(order, "get_dummy_price_list", lambda *a, **k: "PL"),
			mock.patch.object(order, "get_dummy_tax_category", lambda *a, **k: "TC"),
		):
			with self.assertRaises(Durdur):
				order.create_sales_order(siparis, ayar)
		self.assertEqual(yakalanan[STOREFRONT_LOCALE_FIELD], "nl-BE")
		self.assertEqual(yakalanan[STOREFRONT_COUNTRY_FIELD], "Belgium")


if __name__ == "__main__":
	unittest.main()
