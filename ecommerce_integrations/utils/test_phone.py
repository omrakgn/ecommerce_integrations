"""Telefon numarası çevirici. Örnekler canlıdaki Shopify kayıtlarından (2026-10-03).

Frappe'ye ihtiyaç duymuyor, yalnız `phonenumbers`:

    python -m unittest ecommerce_integrations.utils.test_phone
"""

import unittest

from ecommerce_integrations.utils.phone import international


class Cevirme(unittest.TestCase):
	def test_yerel_bastaki_sifir(self):
		self.assertEqual(international("01627473889", "DE"), "+491627473889")
		self.assertEqual(international("015772865778", "DE"), "+4915772865778")
		self.assertEqual(international("0638400399", "NL"), "+31638400399")
		self.assertEqual(international("0612345678", "FR"), "+33612345678")

	def test_cift_sifirla_uluslararasi(self):
		self.assertEqual(international("0031643986782", "NL"), "+31643986782")
		self.assertEqual(international("0031643986782", None), "+31643986782")

	def test_ulke_kodu_var_arti_yok(self):
		"""Yerel yorum da 'geçerli' çıkıyor (+494917683083443) ama yanlış."""
		self.assertEqual(international("4917683083443", "DE"), "+4917683083443")

	def test_baska_ulke_kodu_arti_yok(self):
		self.assertEqual(international("4917683083443", "NL"), "+4917683083443")

	def test_zaten_uluslararasi_bosluklar_gider(self):
		self.assertEqual(international("+49 162 7473889", "DE"), "+491627473889")
		self.assertEqual(international("+491627473889", None), "+491627473889")

	def test_bastaki_sifiri_kalan_ulke(self):
		"""İtalya'da sabit hatların baştaki 0'ı numaranın parçası; mobil 3 ile başlar."""
		self.assertEqual(international("3123456789", "IT"), "+393123456789")
		self.assertEqual(international("0612345678", "IT"), "+390612345678")

	def test_gecersiz_oldugu_gibi_kalir(self):
		"""Yanlış numara yazmaktansa uyarı verecek numarayı bırakmak."""
		self.assertEqual(international("0612345678", "BE"), "0612345678")
		self.assertEqual(international("12", "DE"), "12")

	def test_ulke_bilinmiyor_yerel_numara_kalir(self):
		self.assertEqual(international("01627473889", None), "01627473889")

	def test_bos(self):
		self.assertIsNone(international(None, "DE"))
		self.assertEqual(international("", "DE"), "")
		self.assertEqual(international("  ", "DE"), "")

	def test_kucuk_harf_ulke_kodu(self):
		self.assertEqual(international("01627473889", "de"), "+491627473889")


if __name__ == "__main__":
	unittest.main()
