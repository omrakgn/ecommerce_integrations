"""Siparişin açıldığı vitrin: Shopify'ın `customer_locale` değeri ("nl-BE").

İstenen, siparişin hangi alan adından (.de, .be, .fr) geldiğiydi (Yapılacaklar 12).
Shopify alan adını kaydetmiyor. 2026-10-04'te son 40 siparişin ham verisi:

* sipariş durumu bağlantısı hepsinde mağazanın ana alan adı;
* `landing_site` yalnız yol ve sorgu dizesi, 16 siparişte tamamen boş (çerez onayı);
* `referring_site` yalnız bir siparişte ülke alan adını gösteriyordu;
* `customer_locale` 40 siparişin hepsinde dolu.

`customer_locale` iki parça: müşterinin mağazayı gördüğü dil ve vitrinin açık olduğu
ülke. Ülke teslimat ülkesinden farklı olabiliyor ("nl-BE" ile açılıp Hollanda'ya
giden siparişler var). Sales Order'a ikisi yazılıyor: ham değer ve ülke parçası.

Eski siparişler için `backfill()` konsoldan, deneme kipi varsayılan:

    from ecommerce_integrations.shopify.storefront import backfill, print_report
    r = backfill()
    print_report(r)
    frappe.db.rollback()

Kaynak, entegrasyon kaydında saklanan sipariş verisi. Kayıtlar 120 günde siliniyor
(hooks.py `default_log_clearing_doctypes`); daha eski siparişler boş kalır ve
raporda sayılır.
"""

import json
import re

import frappe

from ecommerce_integrations.shopify.constants import (
	EVENT_MAPPER,
	ORDER_ID_FIELD,
	STOREFRONT_COUNTRY_FIELD,
	STOREFRONT_LOCALE_FIELD,
)


def locale_region(locale):
	"""Two-letter region of a locale ("nl-BE" -> "BE"), or None.

	"en" has no region; "es-419" is a numeric region (Latin America), not a country.
	"""
	parcalar = re.split(r"[-_]", (locale or "").strip())
	for parca in parcalar[1:]:
		if len(parca) == 2 and parca.isalpha():
			return parca.upper()
	return None


def storefront_country(locale):
	"""Country record for the region of `locale`, or None when there is none."""
	bolge = locale_region(locale)
	if not bolge:
		return None
	return frappe.db.get_value("Country", {"code": bolge.lower()}, "name")


def get_storefront(shopify_order: dict) -> dict:
	"""Storefront fields for a Sales Order, keyed by fieldname."""
	locale = (shopify_order.get("customer_locale") or "").strip()[:20]
	return {
		STOREFRONT_LOCALE_FIELD: locale,
		STOREFRONT_COUNTRY_FIELD: storefront_country(locale),
	}


# ---------------------------------------------------------------------------
# Eski siparişler
# ---------------------------------------------------------------------------

LOG_DOCTYPE = "Ecommerce Integration Log"


def backfill(dry_run=True, limit=None, examples=15):
	"""Fill the storefront fields of existing Shopify Sales Orders from stored payloads.

	`dry_run=True` (the default) writes nothing. A Sales Order that already has a
	locale is left alone, so the run can be repeated. Fields are written without
	saving the document (it is submitted) and without changing `modified`.
	"""
	rapor = {
		"logs": 0,
		"unreadable": 0,
		"no_locale": 0,
		"no_order": 0,
		"already": 0,
		"filled": 0,
		"left_empty": 0,
		"countries": {},
		"examples": [],
	}
	secenek = {"limit": int(limit)} if limit else {}
	adlar = frappe.get_all(
		LOG_DOCTYPE,
		filters={"method": EVENT_MAPPER["orders/create"]},
		pluck="name",
		order_by="creation desc",
		**secenek,
	)

	gorulen = set()
	for ad in adlar:
		rapor["logs"] += 1
		ham = frappe.db.get_value(LOG_DOCTYPE, ad, "request_data")
		try:
			siparis = json.loads(ham or "")
		except ValueError:
			rapor["unreadable"] += 1
			continue
		if not isinstance(siparis, dict) or not siparis.get("id"):
			rapor["unreadable"] += 1
			continue

		# Aynı sipariş için birden çok kayıt olabiliyor (yeniden deneme, eski sipariş
		# senkronu); en yenisi yeter.
		kimlik = str(siparis["id"])
		if kimlik in gorulen:
			continue
		gorulen.add(kimlik)

		degerler = get_storefront(siparis)
		if not degerler[STOREFRONT_LOCALE_FIELD]:
			rapor["no_locale"] += 1
			continue

		satislar = frappe.get_all(
			"Sales Order", filters={ORDER_ID_FIELD: kimlik}, fields=["name", STOREFRONT_LOCALE_FIELD]
		)
		if not satislar:
			rapor["no_order"] += 1
			continue

		for so in satislar:
			if so.get(STOREFRONT_LOCALE_FIELD):
				rapor["already"] += 1
				continue
			rapor["filled"] += 1
			ulke = degerler[STOREFRONT_COUNTRY_FIELD] or "-"
			rapor["countries"][ulke] = rapor["countries"].get(ulke, 0) + 1
			if len(rapor["examples"]) < examples:
				rapor["examples"].append(
					(so.name, siparis.get("name"), degerler[STOREFRONT_LOCALE_FIELD], ulke)
				)
			if not dry_run:
				frappe.db.set_value("Sales Order", so.name, degerler, update_modified=False)

	# Kaydı silinmiş (120 günden eski) ya da Shopify'ın dil göndermediği siparişler.
	bos = frappe.db.count(
		"Sales Order",
		filters={ORDER_ID_FIELD: ["is", "set"], STOREFRONT_LOCALE_FIELD: ["is", "not set"]},
	)
	rapor["left_empty"] = bos - rapor["filled"] if dry_run else bos
	return rapor


def print_report(rapor):
	"""Console-friendly summary of `backfill`'s report."""
	print(
		f"kayıt {rapor['logs']} | doldurulacak {rapor['filled']} | zaten dolu {rapor['already']}"
		f" | dil yok {rapor['no_locale']} | siparişi yok {rapor['no_order']}"
		f" | okunamadı {rapor['unreadable']}"
	)
	print(f"boş kalacak Shopify siparişi: {rapor['left_empty']}")
	print("--- ülkeye göre")
	for ulke, sayi in sorted(rapor["countries"].items(), key=lambda x: -x[1]):
		print(f"    {ulke}: {sayi}")
	print("--- örnekler")
	for so, numara, locale, ulke in rapor["examples"]:
		print(f"    {so} ({numara}) {locale} -> {ulke}")
