"""Shopify müşterisinin iletişim kişisi: e-postasız da kuruluyor, eksikse sonradan tamamlanıyor.

#1421 (2026-10-16): müşteri telefonla sipariş verdi, Shopify e-posta göndermedi ve
kişi hiç kurulmadı. Site gerektirmeyen bir test; Frappe'nin yalnız bu modülün
kullandığı parçaları yerine konuyor.
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

# #1421'in Shopify'dan gelen müşterisi: ad var, e-posta yok, telefon adreste.
ISABEL = {
	"id": 25711091679572,
	"first_name": "Isabel",
	"last_name": "Bechthold",
	"email": None,
	"phone": None,
	"default_address": {},
	"billing_address": {
		"first_name": "Isabel",
		"last_name": "Bechthold",
		"phone": "+491733890898",
		"country_code": "DE",
	},
	"shipping_address": {"phone": "+491733890898", "country_code": "DE"},
	"order_email": "",
	"order_phone": "+491733890898",
}


class KisiAlanlari(unittest.TestCase):
	def test_yalniz_telefonla_kisi_kurulur(self):
		alanlar = sc._map_contact_fields(ISABEL)
		self.assertEqual(alanlar["first_name"], "Isabel")
		self.assertEqual(alanlar["last_name"], "Bechthold")
		self.assertEqual(alanlar["phone_nos"], [{"phone": "+491733890898", "is_primary_phone": True}])
		self.assertNotIn("email_ids", alanlar)

	def test_ne_eposta_ne_telefon_kisi_yok(self):
		self.assertIsNone(sc._map_contact_fields({"first_name": "A", "email": None, "billing_address": ""}))

	def test_siparisin_epostasi_yedek(self):
		alanlar = sc._map_contact_fields({"first_name": "A", "email": None, "order_email": "a@example.com"})
		self.assertEqual(alanlar["email_ids"], [{"email_id": "a@example.com", "is_primary": True}])

	def test_musterinin_epostasi_once(self):
		alanlar = sc._map_contact_fields(
			{"first_name": "A", "email": "m@example.com", "order_email": "s@example.com"}
		)
		self.assertEqual(alanlar["email_ids"][0]["email_id"], "m@example.com")

	def test_siparisin_telefonu_yedek(self):
		alanlar = sc._map_contact_fields(
			{"first_name": "A", "order_phone": "01627473889", "billing_address": {"country_code": "DE"}}
		)
		self.assertEqual(alanlar["phone_nos"][0]["phone"], "+491627473889")

	def test_adsiz_musteride_adres_adi(self):
		alanlar = sc._map_contact_fields(
			{"email": "a@example.com", "billing_address": {"first_name": "Liesje", "last_name": "Festjens"}}
		)
		self.assertEqual((alanlar["first_name"], alanlar["last_name"]), ("Liesje", "Festjens"))

	def test_yalniz_soyad_ad_olur(self):
		"""Shopify'ın ödeme sayfası yalnız soyadını zorunlu tutuyor; ERPNext'te ad zorunlu."""
		alanlar = sc._map_contact_fields(
			{"first_name": None, "last_name": "Kusters", "email": "k@example.com"}
		)
		self.assertEqual((alanlar["first_name"], alanlar["last_name"]), ("Kusters", None))

	def test_adres_tam_adi_son_care(self):
		alanlar = sc._map_contact_fields(
			{"email": "a@example.com", "shipping_address": {"name": "S. Willhelm"}}
		)
		self.assertEqual(alanlar["first_name"], "S. Willhelm")

	def test_adsiz_kisi_yok(self):
		self.assertIsNone(sc._map_contact_fields({"email": "a@example.com"}))


class EksikKisiTamamlanir(unittest.TestCase):
	def nesne(self):
		n = sc.ShopifyCustomer.__new__(sc.ShopifyCustomer)
		n.customer_id = "25711091679572"
		n.customer_id_field = "shopify_customer_id"
		return n

	def setUp(self):
		self.db = mock.Mock()
		self.eski_db = sc.frappe.db
		sc.frappe.db = self.db
		self.addCleanup(setattr, sc.frappe, "db", self.eski_db)

	def test_kisisi_olana_dokunmaz(self):
		self.db.get_value.return_value = "Isabel Bechthold"
		self.db.exists.return_value = "Isabel Bechthold-Isabel Bechthold"
		with mock.patch.object(sc.EcommerceCustomer, "create_customer_contact") as ust:
			self.nesne().ensure_contact(ISABEL)
		ust.assert_not_called()
		filtre = self.db.exists.call_args.args[1]
		self.assertEqual(
			filtre, {"parenttype": "Contact", "link_doctype": "Customer", "link_name": "Isabel Bechthold"}
		)

	def test_kisisi_olmayana_kurar(self):
		self.db.get_value.return_value = "Isabel Bechthold"
		self.db.exists.return_value = None
		with mock.patch.object(sc.EcommerceCustomer, "create_customer_contact") as ust:
			self.nesne().ensure_contact(ISABEL)
		self.assertEqual(ust.call_args.args[0]["phone_nos"][0]["phone"], "+491733890898")

	def test_kurulamayan_kisi_siparisi_durdurmaz(self):
		self.db.get_value.return_value = "Isabel Bechthold"
		self.db.exists.return_value = None
		with (
			mock.patch.object(sc.EcommerceCustomer, "create_customer_contact", side_effect=ValueError("x")),
			mock.patch.object(sc.frappe, "log_error", create=True) as log,
			mock.patch.object(sc.frappe, "get_traceback", create=True, return_value="tb"),
		):
			self.nesne().ensure_contact(ISABEL)
		self.db.rollback.assert_called_once_with(save_point="shopify_contact")
		log.assert_called_once()


if __name__ == "__main__":
	unittest.main()
