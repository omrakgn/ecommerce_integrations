"""Telefon numarasını uluslararası biçime (E.164, "+491627473889") getirmek.

Shopify adresindeki telefon müşterinin ödeme sırasında yazdığı gibi geliyor:
"01627473889", "0031643986782", "4917683083443". Kargo firmaları yalnız "+" ile
başlayan numarayı kabul ediyor ve ERPNext'teki gönderi bu numarayı reddediyordu
(Yapılacaklar 11; 2026-10-03'te Shopify iletişim kişilerinin 173/281'i).

Yorumu `phonenumbers` yapıyor (Frappe'nin kendi bağımlılığı, sunucuda kurulu):
ülkelerin numara uzunluğunu ve önek kurallarını o biliyor, el yazımı bir tablo
bilmiyor.

**Geçerli olmak yetmiyor.** "4917683083443" Almanya'ya göre yerel yorumlanınca
+49 4917683083443 çıkıyor ve kütüphane bunu geçerli sayıyor (uzun sabit hat
numaraları var). Doğrusu +4917683083443. Bu yüzden 0 ile başlamayan ve ülkenin
kendi koduyla başlayan numara önce "+" eklenerek yorumlanıyor.

Hiçbir yorum geçerli değilse numara olduğu gibi döner: yanlış bir numara yazmak,
uyarı veren bir numarayı bırakmaktan kötü. Gönderi tarafı onu yine yakalar.

Aynı kural `multichannel_core/phones.py`'de, mevcut kayıtların toplu düzeltmesi
için. İki app birbirine bağımlı olmasın diye kopya; biri değişirse öteki de.
"""

import re


def international(phone, country_code=None):
	"""E.164 form of `phone`, read against the ISO country of its address, or `phone` unchanged."""
	raw = (phone or "").strip()
	if not raw:
		return None if phone is None else raw
	try:
		import phonenumbers
	except ImportError:
		return raw

	digits = re.sub(r"\D", "", raw)
	if not digits:
		return raw
	region = (country_code or "").strip().upper() or None

	if raw.startswith("+"):
		adaylar = [("+" + digits, None)]
	elif digits.startswith("00"):
		adaylar = [("+" + digits[2:], None)]
	else:
		arti = ("+" + digits, None)
		yerel = (digits, region) if region else None
		kod = ""
		if region:
			try:
				kod = str(phonenumbers.country_code_for_region(region) or "")
			except Exception:  # bilinmeyen bölge kodu, yerel deneme düşer
				kod = ""
		if kod and kod != "0" and not digits.startswith("0") and digits.startswith(kod):
			adaylar = [arti, yerel]
		else:
			adaylar = [yerel, arti]

	for aday in adaylar:
		if not aday:
			continue
		metin, bolge = aday
		try:
			numara = phonenumbers.parse(metin, bolge)
		except phonenumbers.NumberParseException:
			continue
		if phonenumbers.is_valid_number(numara):
			return phonenumbers.format_number(numara, phonenumbers.PhoneNumberFormat.E164)
	return raw
