"""Shopify iadesinin makbuzu (Yapilacaklar, 2026-10-09).

Dekont kesiliyordu ama paranin saglayicidan musteriye geri gittigi kaydedilmiyordu.
Shopify Payments iadesi webhook'a "pending" geliyor; gunluk kontrol sonradan kuruyor.

Siteye ihtiyac duymuyor:

    python -m unittest ecommerce_integrations.shopify.test_refund_payment
"""

import datetime
import importlib
import sys
import types
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import MagicMock

from ecommerce_integrations.shopify import refund_payment as rp

DEKONT = NS(name="SI-RET-1", company="Scarnatti", posting_date="2026-10-08", add_comment=MagicMock())


def _gun_once(gun):
	return (datetime.date.today() - datetime.timedelta(days=gun)).isoformat()


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


def _paket_modulleri(*yollar):
	moduller = {}
	for yol in yollar:
		m = types.ModuleType(yol)
		m.__path__ = []
		moduller[yol] = m
	return moduller


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
		moduller = _paket_modulleri(
			"erpnext",
			"erpnext.accounts",
			"erpnext.accounts.doctype",
			"erpnext.accounts.doctype.payment_entry",
		)
		moduller[fatura.__name__] = fatura
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
	def _kur(self, hareketler, **kurulum):
		with Kurulum(**kurulum) as k:
			durum, mesaj = rp.make_refund_payment(DEKONT, {"transactions": hareketler}, NS())
		return k, durum, mesaj

	def test_tek_hareket_tam_tutar_makbuz_kurulur(self):
		k, durum, _ = self._kur([_hareket()])
		self.assertEqual(durum, "created")
		k.get_payment_entry.assert_called_once_with("Sales Invoice", "SI-RET-1", bank_account="Paypal - SC")
		self.assertEqual((k.odeme.reference_no, k.odeme.mode_of_payment), ("111", "Paypal"))
		self.assertEqual(str(k.odeme.posting_date), "2026-10-09")
		k.odeme.submit.assert_called_once()

	def test_tutar_dekonttan_farkliysa_elle(self):
		k, durum, mesaj = self._kur([_hareket()], acik=-300)
		self.assertEqual(durum, "manual")
		self.assertIn("368.60", mesaj)
		k.get_payment_entry.assert_not_called()

	def test_bolunmus_iade_elle(self):
		# Ilk hareket dekontu tam karsiliyor, ikincisi (kargo, ek tutar) ayrica gitmis: yine elle.
		k, durum, _ = self._kur([_hareket(id=1), _hareket(id=2, gateway="gift_card", amount="20")])
		self.assertEqual(durum, "manual")
		k.get_payment_entry.assert_not_called()

	def test_hesabi_olmayan_saglayici_elle(self):
		k, durum, mesaj = self._kur([_hareket(gateway="gift_card")], hesap=None)
		self.assertEqual(durum, "manual")
		self.assertIn("gift_card", mesaj)
		k.get_payment_entry.assert_not_called()

	def test_basarisiz_hareket_elle(self):
		k, durum, mesaj = self._kur([_hareket(status="failure")])
		self.assertEqual(durum, "manual")
		self.assertIn("failure", mesaj)
		k.get_payment_entry.assert_not_called()

	def test_ayni_hareket_iki_kez_kurulmaz(self):
		k, durum, _ = self._kur([_hareket()], var_olan="ACC-PAY-0")
		self.assertEqual(durum, "exists")
		k.get_payment_entry.assert_not_called()

	def test_bekleyen_iade_bekler(self):
		# 2026-09-15-SALES-INVOICE-NR-00003: Shopify Payments iadesi webhook'a pending geldi.
		k, durum, _ = self._kur([_hareket(status="pending", gateway="shopify_payments")])
		self.assertEqual(durum, "pending")
		k.get_payment_entry.assert_not_called()

	def test_bir_parcasi_bekliyorsa_bekler(self):
		k, durum, _ = self._kur([_hareket(id=1), _hareket(id=2, status="pending", amount="20")])
		self.assertEqual(durum, "pending")
		k.get_payment_entry.assert_not_called()

	def test_para_hareketi_olmayan_iade_elle(self):
		# 2026-09-21-SALES-INVOICE-NR-00001: Shopify iadesinde hic hareket yok.
		k, durum, mesaj = self._kur([])
		self.assertEqual(durum, "manual")
		self.assertIn("no money", mesaj)
		k.get_payment_entry.assert_not_called()


class DekontNotu(unittest.TestCase):
	def _kaydet(self, sonuc=None, hata=None, not_var=None, retry=False, gun=0):
		dekont = NS(name="SI-RET-1", posting_date=_gun_once(gun), add_comment=MagicMock())
		db = MagicMock()
		db.exists.return_value = not_var
		yama = (
			mock.patch.object(rp, "make_refund_payment", side_effect=hata)
			if hata
			else mock.patch.object(rp, "make_refund_payment", return_value=sonuc)
		)
		with mock.patch.object(rp.frappe, "db", db), yama:
			durum, mesaj = rp.record_refund_payment(dekont, {}, NS(), retry=retry)
		return dekont, db, durum, mesaj

	def test_hata_olursa_yalniz_odeme_geri_alinir_dekonta_not(self):
		dekont, db, durum, mesaj = self._kaydet(hata=RuntimeError("boom"))
		db.savepoint.assert_called_once_with("shopify_refund_payment")
		db.rollback.assert_called_once_with(save_point="shopify_refund_payment")
		self.assertEqual(durum, "manual")
		self.assertIn("boom", mesaj)
		dekont.add_comment.assert_called_once()
		self.assertTrue(dekont.add_comment.call_args[0][1].startswith(rp.NOTE_PREFIX))

	def test_kurulunca_not_yok(self):
		dekont, *_ = self._kaydet(sonuc=("created", "ok"))
		dekont.add_comment.assert_not_called()

	def test_ayni_not_iki_kez_yazilmaz(self):
		dekont, _, durum, _ = self._kaydet(sonuc=("manual", "x"), not_var="COMM-1")
		self.assertEqual(durum, "manual")
		dekont.add_comment.assert_not_called()

	def test_bekleyen_iadede_not_yok(self):
		dekont, _, durum, _ = self._kaydet(sonuc=("pending", "p"))
		self.assertEqual(durum, "pending")
		dekont.add_comment.assert_not_called()

	def test_gunluk_kontrol_bir_hafta_bekler(self):
		dekont, _, durum, _ = self._kaydet(sonuc=("pending", "p"), retry=True, gun=rp.PENDING_GRACE_DAYS - 1)
		self.assertEqual(durum, "pending")
		dekont.add_comment.assert_not_called()

	def test_gunluk_kontrol_bir_hafta_sonra_elle(self):
		dekont, _, durum, mesaj = self._kaydet(sonuc=("pending", "p"), retry=True, gun=rp.PENDING_GRACE_DAYS)
		self.assertEqual(durum, "manual")
		self.assertIn("still reports the refund as pending", mesaj)
		dekont.add_comment.assert_called_once()

	def test_gec_kurulan_odeme_dekonta_yazilir(self):
		# Dekontta eski "not created" notu olabilir; gec kurulan odeme bunu duzeltir.
		dekont, *_ = self._kaydet(sonuc=("created", "refund payment ACC-PAY-9"), retry=True)
		dekont.add_comment.assert_called_once()
		self.assertIn("ACC-PAY-9", dekont.add_comment.call_args[0][1])


class GunlukKontrol(unittest.TestCase):
	def _calistir(self, satirlar, sonuclar=None, hatalar=None, acik=True):
		sonuclar = sonuclar or {}
		hatalar = hatalar or {}
		ayar = NS(is_enabled=lambda: acik, get=lambda alan: 1)

		def belge(doctype, name=None):
			if doctype == rp.SETTING_DOCTYPE:
				return ayar
			return NS(name=name)

		def oku(refund_id, order_id):
			if refund_id in hatalar:
				raise hatalar[refund_id]
			return {"id": refund_id}

		def kaydet(credit_note, refund, setting, retry=False):
			self.assertTrue(retry)
			return sonuclar[credit_note.name]

		baglanti = types.ModuleType("ecommerce_integrations.shopify.connection")
		baglanti.temp_shopify_session = lambda fn: fn
		yardimci = types.ModuleType("ecommerce_integrations.shopify.utils")
		yardimci.create_shopify_log = MagicMock()
		izleyici = MagicMock()
		db = izleyici.db
		with (
			mock.patch.dict(sys.modules, {baglanti.__name__: baglanti, yardimci.__name__: yardimci}),
			mock.patch.object(rp.frappe, "db", db),
			mock.patch.object(rp.frappe, "get_doc", side_effect=belge),
			mock.patch.object(rp, "unpaid_credit_notes", return_value=satirlar) as adaylar,
			mock.patch.object(rp, "_fetch_refund", side_effect=oku) as okuma,
			mock.patch.object(rp, "record_refund_payment", side_effect=kaydet) as kayit,
		):
			izleyici.attach_mock(okuma, "okuma")
			rp.retry_refund_payments()
		return NS(log=yardimci.create_shopify_log, db=db, adaylar=adaylar, kayit=kayit, izleyici=izleyici)

	def test_kapaliysa_bakmaz(self):
		s = self._calistir([], acik=False)
		s.adaylar.assert_not_called()

	def test_kurulan_ve_bakilamayan_kaydedilir(self):
		satirlar = [
			NS(name="A", refund_id="1", order_id="10"),
			NS(name="B", refund_id="2", order_id="20"),
			NS(name="C", refund_id="3", order_id=None),
		]
		s = self._calistir(
			satirlar,
			sonuclar={"A": ("created", "refund payment ACC-PAY-7")},
			hatalar={"2": OSError("timeout")},
		)
		s.kayit.assert_called_once()
		self.assertEqual(s.db.rollback.call_count, 2)
		s.log.assert_called_once()
		alanlar = s.log.call_args.kwargs
		self.assertEqual(alanlar["status"], "Partial Success")
		self.assertIn("ACC-PAY-7", alanlar["message"])
		self.assertIn("B: timeout", alanlar["message"])
		self.assertIn("C: no Shopify order id", alanlar["message"])

	def test_degisiklik_yoksa_kayit_acilmaz(self):
		satirlar = [NS(name="A", refund_id="1", order_id="10"), NS(name="B", refund_id="2", order_id="20")]
		s = self._calistir(satirlar, sonuclar={"A": ("pending", "p"), "B": ("manual", "m")})
		self.assertEqual(s.kayit.call_count, 2)
		s.log.assert_not_called()

	def test_shopify_beklenmeden_once_islem_kapanir(self):
		# docs/olay-islem-icinde-bekleme.md: acik islemle disariyi bekleme.
		s = self._calistir([NS(name="A", refund_id="1", order_id="10")], sonuclar={"A": ("pending", "p")})
		adlar = [c[0] for c in s.izleyici.mock_calls]
		self.assertIn("db.commit", adlar)
		self.assertLess(adlar.index("db.commit"), adlar.index("okuma"))


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
		moduller = _paket_modulleri(
			"erpnext",
			"erpnext.accounts",
			"erpnext.accounts.doctype",
			"erpnext.accounts.doctype.sales_invoice",
		)
		moduller.update({satis.__name__: satis, urun.__name__: urun, yardimci.__name__: yardimci})
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

	def test_bekleyen_iade_basarili_gunluk_kontrol_soylenir(self):
		sonuc = self._calistir(("pending", "Shopify has not completed the refund yet (pending)"))
		self.assertEqual(sonuc["status"], "Success")
		self.assertIn("daily check", sonuc["message"])


if __name__ == "__main__":
	unittest.main()
