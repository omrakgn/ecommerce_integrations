"""`sync_item_groups` icin acik bir deger yaz: deploy davranisi degistirmesin.

Yeni bir Check alaninin `default: 1` degeri, o alanin `tabSingles`'da satiri
YOKSA okunurken uygulanmiyor: `setting.get("sync_item_groups")` None donuyor ve
kod tarafinda "kapali" gibi okunuyor. Yani yalniz kod yuklenmesi, kategori
eslesmesini sessizce kapatirdi. Oysa kapatma kararini kullanici vermeli ve ne
zaman kapandigi gorunmeli.

Bu yama degeri bir kez acik olarak yaziyor. Kullanici sonra ayarlar ekranindan
kapatinca 0 olarak kaydediliyor ve yama tekrar calismadigi icin geri
acilmiyor.

`frappe.db.has_column` BURADA KULLANILMIYOR: Single DocType'in tablosu yok ve
o cagri `TableMissingError` ile migrate'i durduruyor (yasandi, bkz.
docs/ozellik-kontrol-listesi.md). Alan varligi meta uzerinden soruluyor.

Bkz. docs/shopify-kategori-eslesmesi.md
"""

import frappe

from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE


def execute():
	meta = frappe.get_meta(SETTING_DOCTYPE, cached=False)
	if not meta.has_field("sync_item_groups"):
		return

	mevcut = frappe.db.get_single_value(SETTING_DOCTYPE, "sync_item_groups")
	if mevcut is not None:
		# Kullanici ya da onceki bir kosu zaten bir deger yazmis, dokunmuyoruz.
		return

	frappe.db.set_single_value(SETTING_DOCTYPE, "sync_item_groups", 1)
