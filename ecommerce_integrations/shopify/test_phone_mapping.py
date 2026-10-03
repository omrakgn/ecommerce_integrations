"""Shopify müşteri ve adres telefonları uluslararası biçimde kaydediliyor (Yapılacaklar 11).

Site gerektirmeyen bir test; Frappe'nin yalnız bu modülün kullandığı parçaları
yerine konuyor.
"""

import sys
import types
import unittest
from unittest import mock

if "frappe.utils.nestedset" not in sys.modules:
	_nestedset = types.ModuleType("frappe.utils.nestedset")
	_nestedset.get_root_of = lambda doctype: "All"
	sys.modules["frappe.utils.nestedset"] = _nestedset

from ecommerce_integrations.shopify import customer as sc


class AdresTelefonu(unittest.TestCase):
	def test_shopify_adresindeki_yerel_numara_cevrilir(self):
		alanlar = sc._map_address_fields(
			{
				"id": 1,
				"address1": "Str 1",
				"country": "Germany",
				"country_code": "DE",
				"phone": "01627473889",
			},
			"Angelika Reiter",
			"Shipping",
			None,
		)
		self.assertEqual(alanlar["phone"], "+491627473889")

	def test_ulke_kodu_olan_ama_artisiz_numara(self):
		alanlar = sc._map_address_fields(
			{"id": 1, "country_code": "DE", "phone": "4917683083443"}, "Simon Krieger", "Billing", None
		)
		self.assertEqual(alanlar["phone"], "+4917683083443")

	def test_cevrilemeyen_numara_oldugu_gibi(self):
		alanlar = sc._map_address_fields(
			{"id": 1, "country_code": "BE", "phone": "0612345678"}, "X", "Billing", None
		)
		self.assertEqual(alanlar["phone"], "0612345678")


class KisiTelefonu(unittest.TestCase):
	def kur(self, musteri):
		with mock.patch.object(sc.EcommerceCustomer, "create_customer_contact") as ust:
			nesne = sc.ShopifyCustomer.__new__(sc.ShopifyCustomer)
			nesne.create_customer_contact(musteri)
		return ust.call_args.args[0] if ust.called else None

	def test_adresin_ulkesiyle_cevrilir(self):
		alanlar = self.kur(
			{
				"first_name": "Nico",
				"email": "n@example.com",
				"phone": None,
				"default_address": {"phone": "0638400399", "country_code": "NL"},
			}
		)
		self.assertEqual(alanlar["phone_nos"], [{"phone": "+31638400399", "is_primary_phone": True}])

	def test_varsayilan_adres_yoksa_fatura_adresi(self):
		"""Siparişten gelen müşteride `default_address` boş olabiliyor."""
		alanlar = self.kur(
			{
				"first_name": "Bastian",
				"email": "b@example.com",
				"billing_address": {"phone": "015772865778", "country_code": "DE"},
			}
		)
		self.assertEqual(alanlar["phone_nos"][0]["phone"], "+4915772865778")

	def test_bos_metin_adres_patlatmaz(self):
		"""`sync_sales_order` adres yoksa boş metin koyuyor."""
		alanlar = self.kur(
			{"first_name": "A", "email": "a@example.com", "billing_address": "", "phone": "+491627473889"}
		)
		self.assertEqual(alanlar["phone_nos"][0]["phone"], "+491627473889")


if __name__ == "__main__":
	unittest.main()
