"""Shopify iadesinin makbuzu (Yapilacaklar, 2026-10-09).

Dekont kesiliyordu ama paranin saglayicidan musteriye geri gittigi kaydedilmiyordu.

Siteye ihtiyac duymuyor:

    python -m unittest ecommerce_integrations.shopify.test_refund_payment
"""

import importlib
import sys
import types
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import MagicMock

from ecommerce_integrations.shopify import refund_payment as rp

DEKONT = NS(name="SI-RET-1", company="Scarnatti", posting_date="2026-10-08", add_comment=MagicMock())


def _hareket(**alanlar):
	t = {
		"id": 111,
		"kind": "refund",
		"status": "success",
		"gateway": "paypal",
		"amount": "368.60",
		"processed_at": "2026-10-09T10:00:00+02:00",
	}
	t.update(alanlar)
	return t


class Kurulum:
	def __init__(self, var_olan=None, acik=-368.6, hesap="Paypal - SC"):
		self.odeme = MagicMock()
		self.odeme.name = "ACC-PAY-1"
		self.get_payment_entry = MagicMock(return_value=self.odeme)
		self.db = MagicMock()
		self.db.exists.return_value = var_olan
		self.db.get_value.return_value = acik
		fatura = types.ModuleType("ecommerce_integrations.shopify.invoice")
		fatura.get_mode_of_payment = MagicMock(return_value="Paypal")
		fatura.get_mode_of_payment_account = MagicMock(return_value=hesap)
		moduller = {"ecommerce_integrations.shopify.invoice": fatura}
		for yol in [
			"erpnext",
			"erpnext.accounts",
			"erpnext.accounts.doctype",
			"erpnext.accounts.doctype.payment_entry",
		]:
			m = types.ModuleType(yol)
			m.__path__ = []
			moduller[yol] = m
		pe = types.ModuleType("erpnext.accounts.doctype.payment_entry.payment_entry")
		pe.get_payment_entry = self.get_payment_entry
		moduller[pe.__name__] = pe
		self.yamalar = [mock.patch.object(rp.frappe, "db", self.db), mock.patch.dict(sys.modules, moduller)]

	def __enter__(self):
		for y in self.yamalar:
			y.__enter__()
		return self

	def __exit__(self, *a):
		for y in reversed(self.yamalar):
			y.__exit__(*a)


class Makbuz(unittest.TestCase):
	def test_tek_hareket_tam_tutar_makbuz_kurulur(self):
		with Kurulum() as k:
			durum, _ = rp.make_refund_payment(DEKONT, {"transactions": [_hareket()]}, NS())
		self.assertEqual(durum, "created")
		k.get_payment_entry.assert_called_once_with("Sales Invoice", "SI-RET-1", bank_account="Paypal - SC")
		self.assertEqual((k.odeme.reference_no, k.odeme.mode_of_payment), ("111", "Paypal"))
		self.assertEqual(str(k.odeme.posting_date), "2026-10-09")
		k.odeme.submit.assert_called_once()

	def test_tutar_dekonttan_farkliysa_elle(self):
		with Kurulum(acik=-300) as k:
			durum, mesaj = rp.make_refund_payment(DEKONT, {"transactions": [_hareket()]}, NS())
		self.assertEqual(durum, "manual")
		self.assertIn("368.60", mesaj)
		k.get_payment_entry.assert_not_called()

	def test_bolunmus_iade_elle(self):
		# Ilk hareket dekontu tam karsiliyor, ikincisi (kargo, ek tutar) ayrica gitmis: yine elle.
		hareketler = [_hareket(id=1), _hareket(id=2, gateway="gift_card", amount="20")]
		with Kurulum() as k:
			durum, _ = rp.make_refund_payment(DEKONT, {"transactions": hareketler}, NS())
		self.assertEqual(durum, "manual")
		k.get_payment_entry.assert_not_called()

	def test_hesabi_olmayan_saglayici_elle(self):
		with Kurulum(hesap=None) as k:
			durum, mesaj = rp.make_refund_payment(
				DEKONT, {"transactions": [_hareket(gateway="gift_card")]}, NS()
			)
		self.assertEqual(durum, "manual")
		self.assertIn("gift_card", mesaj)
		k.get_payment_entry.assert_not_called()

	def test_basarisiz_hareket_sayilmaz(self):
		with Kurulum() as k:
			durum, _ = rp.make_refund_payment(DEKONT, {"transactions": [_hareket(status="failure")]}, NS())
		self.assertEqual(durum, "manual")
		k.get_payment_entry.assert_not_called()

	def test_ayni_hareket_iki_kez_kurulmaz(self):
		with Kurulum(var_olan="ACC-PAY-0") as k:
			durum, _ = rp.make_refund_payment(DEKONT, {"transactions": [_hareket()]}, NS())
		self.assertEqual(durum, "exists")
		k.get_payment_entry.assert_not_called()


class DekontKalir(unittest.TestCase):
	def test_hata_olursa_yalniz_odeme_geri_alinir_dekonta_not(self):
		dekont = NS(name="SI-RET-1", add_comment=MagicMock())
		db = MagicMock()
		with (
			mock.patch.object(rp.frappe, "db", db),
			mock.patch.object(rp, "make_refund_payment", side_effect=RuntimeError("boom")),
		):
			durum, mesaj = rp.record_refund_payment(dekont, {}, NS())
		db.savepoint.assert_called_once_with("shopify_refund_payment")
		db.rollback.assert_called_once_with(save_point="shopify_refund_payment")
		self.assertEqual(durum, "manual")
		self.assertIn("boom", mesaj)
		dekont.add_comment.assert_called_once()

	def test_kurulunca_not_yok(self):
		dekont = NS(name="SI-RET-1", add_comment=MagicMock())
		with (
			mock.patch.object(rp.frappe, "db", MagicMock()),
			mock.patch.object(rp, "make_refund_payment", return_value=("created", "ok")),
		):
			rp.record_refund_payment(dekont, {}, NS())
		dekont.add_comment.assert_not_called()


class IadeKaydi(unittest.TestCase):
	"""refund.create_credit_note: makbuz kurulamadiysa kayit "Success" gorunmemeli."""

	def _calistir(self, odeme_sonucu):
		dekont = MagicMock()
		dekont.name = "SI-RET-1"
		dekont.items = [NS(item_code="ITEM-1", qty=-1)]
		satis = types.ModuleType("erpnext.accounts.doctype.sales_invoice.sales_invoice")
		satis.make_sales_return = MagicMock(return_value=dekont)
		urun = types.ModuleType("ecommerce_integrations.shopify.product")
		urun.get_item_code = MagicMock(return_value="ITEM-1")
		yardimci = types.ModuleType("ecommerce_integrations.shopify.utils")
		yardimci.create_shopify_log = MagicMock()
		moduller = {satis.__name__: satis, urun.__name__: urun, yardimci.__name__: yardimci}
		for yol in [
			"erpnext",
			"erpnext.accounts",
			"erpnext.accounts.doctype",
			"erpnext.accounts.doctype.sales_invoice",
		]:
			m = types.ModuleType(yol)
			m.__path__ = []
			moduller[yol] = m
		iade = {"id": 9, "order_id": 5, "refund_line_items": [{"quantity": 1, "line_item": {"sku": "X"}}]}
		with mock.patch.dict(sys.modules, moduller):
			sys.modules.pop("ecommerce_integrations.shopify.refund", None)
			# Taze yukleme: `from paket import refund` paketteki eski nesneyi dondururdu.
			refund = importlib.import_module("ecommerce_integrations.shopify.refund")

			db = MagicMock()
			db.get_value.side_effect = [None, "SI-1"]
			with (
				mock.patch.object(refund.frappe, "db", db),
				mock.patch.object(rp, "record_refund_payment", return_value=odeme_sonucu) as kayit,
			):
				sonuc = refund.create_credit_note(iade, NS(sales_invoice_series=None))
			sys.modules.pop("ecommerce_integrations.shopify.refund", None)
		dekont.submit.assert_called_once()
		kayit.assert_called_once()
		return sonuc

	def test_makbuz_kurulursa_basarili(self):
		sonuc = self._calistir(("created", "refund payment ACC-PAY-1"))
		self.assertEqual(sonuc["status"], "Success")
		self.assertIn("ACC-PAY-1", sonuc["message"])

	def test_makbuz_elle_girilecekse_kismi(self):
		sonuc = self._calistir(("manual", "gateway 'gift_card' has no account"))
		self.assertEqual(sonuc["status"], "Partial Success")
		self.assertIn("gift_card", sonuc["message"])


if __name__ == "__main__":
	unittest.main()
