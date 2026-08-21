"""Push tracking from ERPNext to Shopify.

Shopify normally learns about a shipment from SendCloud, because SendCloud holds
the Shopify integration. Labels built in ERPNext never pass through it, so the
order stays unfulfilled in Shopify and the customer is told nothing.

This closes that gap, and only that: it reports parcels that already exist.

Off unless switched on. Fulfilling an order in Shopify emails the customer and
moves the order out of the open queue — a side effect nobody should get by
installing an app.
"""

import json

import frappe
import requests
from frappe import _
from frappe.utils import add_days, flt, nowdate

from ecommerce_integrations.shopify.constants import (
	API_VERSION,
	FULLFILLMENT_ID_FIELD,
	ORDER_ID_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


def push_tracking_on_submit(doc, method=None):
	"""Hook: a Shipment was submitted, tell Shopify about it.

	Rarely the right moment on its own — the label is usually bought after the
	submit — but harmless, and correct for a shipment whose number is already
	there.
	"""
	push_tracking(doc.name)


def push_tracking_on_label(shipment=None, **kwargs):
	"""Hook: `shipment_label_created` — the tracking number now exists.

	This is the moment that matters. `awb_number` is written after the submit and
	with `db_set`, so no document event sees it; `erpnext_shipping` announces it
	instead and this listens. Without it Shopify learned about a parcel only on
	the next hourly scan, up to an hour after the label was printed.

	Absent that app, the hook is never fired and nothing here runs.
	"""
	if shipment:
		push_tracking(shipment)


@frappe.whitelist()
def push_tracking(shipment):
	"""Report this shipment's parcels to Shopify as fulfillments.

	Returns {"skipped": reason} or {"fulfilled": [...]}. Never raises into the
	submit: a shipment that physically left must not be blocked because Shopify
	was unreachable.
	"""
	setting = frappe.get_cached_doc(SETTING_DOCTYPE)
	if not setting.is_enabled() or not setting.get("push_tracking_to_shopify"):
		return {"skipped": "disabled"}

	doc = frappe.get_doc("Shipment", shipment)
	if not doc.get("awb_number"):
		return {"skipped": "no tracking number"}

	# Aynı irsaliye tabloda birden fazla satırda görünüyor — koli başına bir satır.
	# Tekilleştirilmezse aynı irsaliye için koli sayısı kadar çağrı yapılır; ikinci
	# çağrı Shopify'a gidip "gönderilecek bir şey kalmamış" cevabını alır. Yanlış
	# sonuç doğurmaz ama boşuna istek atar ve logda "iki kez denendi" ile "iki kez
	# bildirildi" birbirine karışır.
	seen = []
	for row in doc.get("shipment_delivery_note") or []:
		if row.delivery_note and row.delivery_note not in seen:
			seen.append(row.delivery_note)

	results = []
	errors = []
	unsent = []
	for delivery_note in seen:
		try:
			outcome = _fulfil_delivery_note(setting, doc, delivery_note)
		except Exception as exception:
			# Hatanın kendisi loga yazılıyor. Önceki hâli yalnız gönderi ve
			# irsaliye adını kaydediyordu, yani "bir şey oldu" diyip ne olduğunu
			# söylemiyordu — teşhis baştan yapılmak zorunda kalıyordu.
			create_shopify_log(
				status="Error",
				method="ecommerce_integrations.shopify.tracking.push_tracking",
				message=f"Shipment {doc.name}, Delivery Note {delivery_note}: {exception}",
			)
			frappe.log_error(
				message=frappe.get_traceback(),
				title=f"Shopify tracking failed for {doc.name}",
			)
			errors.append(f"{delivery_note}: {exception}")
			continue
		if outcome:
			results.append(outcome)
			for message in outcome.get("unsent") or []:
				unsent.append(f"{delivery_note}: {message}")

	# Hata ve gitmeyen takip numarası, gönderiyi engellememeli ama kaybolmamalı
	# da: çağıran görsün diye sonuçla birlikte dönüyor. Aksi hâlde reddedilmiş
	# bir istek, "yapacak iş yoktu" ile aynı görünür.
	return {"fulfilled": results, "errors": errors, "unsent": unsent}


@frappe.whitelist()
def retrack_shipment(shipment):
	"""Put this shipment's tracking on the fulfillments Shopify already holds.

	For a parcel that replaces one already reported: the first attempt came back
	— address not found, nobody home — and the goods left again under a new
	number. Shopify counts those lines as fulfilled, so a new fulfillment is
	refused; the number on the fulfillment that exists has to be rewritten.

	Deliberate rather than automatic, and that is the point. "Nothing left to
	fulfil" alone cannot tell a replacement parcel from an extra one: an order
	SendCloud already fulfilled looks exactly the same, and rewriting there would
	take away the tracking of a parcel the customer is still waiting for. Only
	the person sending the replacement knows which it is.

	The label hook covers the narrower case on its own — a fulfillment we created
	ourselves, whose id we hold. Everything Shopify learned from SendCloud needs
	this call.

	See docs/plans/basarisiz-teslimat.md.
	"""
	setting = frappe.get_cached_doc(SETTING_DOCTYPE)
	if not setting.is_enabled() or not setting.get("push_tracking_to_shopify"):
		return {"skipped": "disabled"}

	doc = frappe.get_doc("Shipment", shipment)
	if not doc.get("awb_number"):
		return {"skipped": "no tracking number"}

	seen = []
	for row in doc.get("shipment_delivery_note") or []:
		if row.delivery_note and row.delivery_note not in seen:
			seen.append(row.delivery_note)

	results = []
	for delivery_note in seen:
		dn = frappe.db.get_value(
			"Delivery Note", delivery_note,
			["name", "is_return", ORDER_ID_FIELD, FULLFILLMENT_ID_FIELD],
			as_dict=True,
		)
		if not dn or not dn.get(ORDER_ID_FIELD) or dn.get("is_return"):
			continue
		outcome = _retrack_delivery_note(setting, doc, dn, dn.get(ORDER_ID_FIELD))
		if outcome:
			results.append(outcome)

	return {"retracked": results}


def _fulfil_delivery_note(setting, shipment, delivery_note):
	dn = frappe.db.get_value(
		"Delivery Note", delivery_note,
		["name", "is_return", ORDER_ID_FIELD, FULLFILLMENT_ID_FIELD],
		as_dict=True,
	)
	if not dn or not dn.get(ORDER_ID_FIELD):
		return None  # Shopify siparişi değil

	# İade irsaliyesi Shopify sipariş numarasını **taşıyor**: `make_sales_return`
	# alanları aslından kopyalıyor. Ona bağlı bir gönderi (müşteriden bize gelen
	# paket) buraya düşerse, iade etiketinin numarası müşterinin gidiş
	# fulfillment'ının üstüne yazılır — müşteri kendi kargosunu tümden kaybeder.
	# Bugün iade kargosu irsaliye bağlamıyor, ama bu bir tesadüf; kural burada
	# duruyor ki bağlandığı gün sessizce bozulmasın.
	if dn.get("is_return"):
		return None

	order_id = dn.get(ORDER_ID_FIELD)

	# Ölçüt Shopify'ın kendi durumu: hâlâ gönderilebilir satır var mı?
	#
	# Önceden irsaliyedeki fulfillment id'sine bakılıp, doluysa çıkılıyordu.
	# Amaç müşteriye ikinci bir "kargoya verildi" e-postası göndermemekti ama
	# tek bir kontrol iki ayrı durumu birden eliyordu:
	#
	#   gönderilebilir satır VAR → bölünmüş gönderinin ikinci kolisi. Bir irsaliye
	#       kutuya sığmadığında bilerek birkaç gönderiye bölünüyor; ikincisinin
	#       takip numarası Shopify'a **hiç** gitmiyordu.
	#   gönderilebilir satır YOK → bu koli daha önce bildirilmiş bir kolinin
	#       yerine geçiyor: teslimat başarısız olmuş, paket dönmüş, yeni numarayla
	#       yeniden gönderilmiş. Yeni fulfillment açılamaz (Shopify o satırları
	#       karşılanmış sayıyor), duran fulfillment'ın takip bilgisi yeniden
	#       yazılıyor. Bkz. docs/plans/basarisiz-teslimat.md
	#
	# Mükerrer bildirim korumasını Shopify'ın kendisi veriyor: bir kez fulfillment
	# açıldıktan sonra o satırlar gönderilebilir listesinden düşüyor, dolayısıyla
	# kanca ikinci kez tetiklenirse alttaki dal boş kalıyor. Kendi alanımıza
	# bakmak, gerçeği kendi kaydımızdan okumaktı — bu projede kataloglanmış bir
	# hata ailesi (bkz. docs/sessiz-varsayilan-tuzaklari.md).
	available = _fulfillable_lines(setting, order_id)
	if not available:
		if dn.get(FULLFILLMENT_ID_FIELD):
			return _retrack_delivery_note(setting, shipment, dn, order_id)
		return None  # Shopify tarafında gönderilecek bir şey kalmamış

	numbers = [t.strip() for t in (shipment.awb_number or "").split(",") if t.strip()]
	urls = [u.strip() for u in (shipment.tracking_url or "").split(",") if u.strip()]
	parcels = _parcel_contents(shipment)

	# Parça bazında bildirmek, hangi kutunun hangi numarayı taşıdığı **söylenmişse**
	# doğru. Sayıların tutması yetmiyor: iki liste eşit uzunlukta olup ayrı
	# sıralarda olabiliyor ve o zaman her kutu komşusunun numarasını alıyor.
	# Eşleme kurulamıyorsa tek fulfillment açılıp neyin eksik kaldığı yazılıyor —
	# az bildirmek, müşteriyi yanlış kutuya yönlendirmekten iyidir.
	pairing = _pair_parcels_with_tracking(shipment, parcels) if len(parcels) > 1 else None

	if pairing:
		fulfillments, unsent = _fulfil_per_parcel(setting, shipment, available, pairing)
	else:
		fulfillments, unsent = _fulfil_whole(setting, shipment, available, parcels, numbers, urls)

	if not fulfillments:
		if unsent:
			# Hiç fulfillment açılmadı ama sebebi biliniyor: söyle, yoksa bu da
			# "yapacak iş yoktu" ile aynı görünür.
			create_shopify_log(
				status="Success",
				method="ecommerce_integrations.shopify.tracking.push_tracking",
				message=f"Shopify order {order_id}: nothing reported from {shipment.name} — "
				+ "; ".join(unsent),
			)
		return None

	# Üstüne yazmak yerine ekleniyor. Bir irsaliye birden çok gönderiyle
	# çıkabiliyor; ilk gönderinin fulfillment id'si silinirse onu ne
	# `repair_tracking` ne de yeniden gönderim bir daha bulabiliyor.
	stored = [f.strip() for f in str(dn.get(FULLFILLMENT_ID_FIELD) or "").split(",") if f.strip()]
	for fulfillment_id in fulfillments:
		if str(fulfillment_id) not in stored:
			stored.append(str(fulfillment_id))

	frappe.db.set_value(
		"Delivery Note", dn.name, FULLFILLMENT_ID_FIELD,
		", ".join(stored), update_modified=False,
	)
	create_shopify_log(
		status="Success",
		method="ecommerce_integrations.shopify.tracking.push_tracking",
		message=(
			f"Shopify order {order_id}: {len(fulfillments)} fulfillment(s) from {shipment.name}"
			+ ((" — " + "; ".join(unsent)) if unsent else "")
		),
	)
	return {"delivery_note": dn.name, "fulfillments": fulfillments, "unsent": unsent}


def _retrack_delivery_note(setting, shipment, dn, order_id):
	"""Rewrite the tracking on fulfillments that already exist.

	Used when a parcel goes out a second time for the same lines: the first
	attempt came back — address not found, nobody home — and the goods left again
	under a new number. Shopify counts those lines as fulfilled, so a new
	fulfillment is refused; the only way to reach the customer is to replace the
	tracking on the fulfillment that is already there.

	Not a return: the sale stands and no credit note follows. See
	docs/plans/basarisiz-teslimat.md for the whole flow.
	"""
	numbers = [t.strip() for t in (shipment.awb_number or "").split(",") if t.strip()]
	urls = [u.strip() for u in (shipment.tracking_url or "").split(",") if u.strip()]
	carriers = _parcel_carriers(shipment)
	notify = setting.get("notify_customer_on_tracking_push")

	# Saklanan fulfillment id'leri koli sırasında oluşturuldu, `awb_number` ise
	# taşıyıcının sırasında yazıldı. İkisi aynı sıra değil. Eşleme kurulabiliyorsa
	# numaralar koli sırasına diziliyor; kurulamıyorsa aşağıdaki sayı kontrolü
	# devreye giriyor ve hiçbir şey yazılmıyor.
	pairing = _pair_parcels_with_tracking(shipment, _parcel_contents(shipment))
	if pairing:
		numbers = [row[2] for row in pairing]
		urls = [row[3] or "" for row in pairing]
		carriers = {index: row[4] for index, row in enumerate(pairing, start=1) if row[4]}

	# Shopify'da gerçekten duran fulfillment'lar. Yazmadan önce okunuyor: numara
	# zaten aynıysa hiçbir çağrı yapılmıyor, çünkü etiket kancası birden fazla kez
	# tetiklenebiliyor ve her tetiklenmede müşteriye yeni bir kargo e-postası
	# gitmesi kabul edilemez.
	current = _fulfillment_tracking(setting, order_id)

	stored = [f.strip() for f in str(dn.get(FULLFILLMENT_ID_FIELD) or "").split(",") if f.strip()]
	if not stored:
		# Fulfillment'ı biz açmamışız. Olağan durum bu: normal yolda Shopify'ı
		# SendCloud haberdar ediyor, ERPNext yalnız kendi bastığı etiketleri
		# bildiriyor. Kendi alanımıza bakıp "kayıt yok" demek, Shopify'da gözle
		# görülen fulfillment'ı yok saymak olurdu — ve yeniden gönderilen paketin
		# numarası yine hiçbir yere gitmezdi.
		stored = list(current.keys())

	# Shopify'ın listesinde olmayan id: iptal edilmiş ya da artık yok. Üstüne
	# yazmaya çalışmak hata verir.
	unknown = [f for f in stored if str(f) not in current]
	stored = [f for f in stored if str(f) in current]

	if not stored:
		return None

	# Sıraya güvenmek ancak saklanan id'ler ile bu gönderinin numaraları birebir
	# karşılık geliyorsa doğru. İrsaliye birden çok gönderiyle çıktıysa id'ler
	# birden çok gönderiye ait oluyor ve indeksle eşlemek, bir kolinin numarasını
	# başka bir kolinin fulfillment'ına yazmak demek — müşteri o kutuyu tümden
	# kaybeder. Sayılar tutmuyorsa tahmin etmek yerine duruyoruz.
	if len(stored) != len(numbers):
		reason = (
			f"{len(stored)} fulfillment on the delivery note but {len(numbers)} "
			f"tracking number(s) on {shipment.name} — cannot tell which belongs to which, "
			"nothing rewritten"
		)
		create_shopify_log(
			status="Success",
			method="ecommerce_integrations.shopify.tracking.push_tracking",
			message=f"Shopify order {order_id}: {reason}",
		)
		return {"delivery_note": dn.name, "retracked": [], "unsent": [reason]}

	retracked = []
	unsent = []
	if unknown:
		unsent.append(
			_("not on Shopify any more, left alone: {0}").format(", ".join(str(f) for f in unknown))
		)
	for index, fulfillment_id in enumerate(stored):
		number = numbers[index] if index < len(numbers) else None
		if not number:
			# Numarasız güncelleme, duran numarayı **siler**: bu uç nokta
			# `tracking_info`'yu değiştiriyor, birleştirmiyor. Yazmaktansa
			# dokunmamak doğru.
			unsent.append(
				_("fulfillment {0}: no tracking number in this shipment, left as it was").format(
					fulfillment_id
				)
			)
			continue
		if current.get(str(fulfillment_id)) == number:
			continue  # aynı numara zaten duruyor

		_update_tracking(
			setting,
			fulfillment_id,
			number,
			urls[index] if index < len(urls) else None,
			carriers.get(index + 1) or shipment.get("carrier"),
			notify,
		)
		retracked.append(fulfillment_id)

	if retracked:
		create_shopify_log(
			status="Success",
			method="ecommerce_integrations.shopify.tracking.push_tracking",
			message=(
				f"Shopify order {order_id}: tracking rewritten on {len(retracked)} "
				f"fulfillment(s) from {shipment.name} — reshipped after a failed delivery"
				+ ((" — " + "; ".join(unsent)) if unsent else "")
			),
		)

	return {"delivery_note": dn.name, "retracked": retracked, "unsent": unsent}


def _fulfillment_tracking(setting, order_id):
	"""{fulfillment_id: tracking_number} as Shopify holds it right now.

	Read before writing so an unchanged number is left alone. `update_tracking`
	notifies the customer when asked to, and the label hook can fire more than
	once for one parcel — without this check every extra firing would be another
	shipping e-mail for a parcel that had not moved.
	"""
	data = _get(setting, f"orders/{order_id}/fulfillments.json")
	tracking = {}
	for row in (data or {}).get("fulfillments") or []:
		# İptal edilmiş fulfillment güncellenemez ve müşteriye de görünmez.
		if row.get("id") is None or row.get("status") != "success":
			continue
		tracking[str(row["id"])] = row.get("tracking_number")
	return tracking


def _unsent_note(numbers, parcels):
	"""Neyin gitmediğini söyle. Sessiz kalırsa iki kutulu müşteri birini takip
	edebildiğinde sebebi hiçbir yerde görünmez."""
	if len(numbers) <= 1:
		return ""
	reason = (
		"parcel items are not filled in"
		if not parcels
		else "the carrier's parcel list could not be matched to the parcel contents, "
		f"so none of the {len(numbers)} numbers could be tied to a box with certainty"
	)
	return (
		f"sent as one fulfillment because {reason}, so only {numbers[0]} reached Shopify;"
		f" the others ({', '.join(numbers[1:])}) did not"
	)


def _parcel_contents(shipment):
	"""[(parcel_no, {item_code: qty})] — koli sırasına göre.

	Sıra önemli: takip numaraları da koli sırasında üretiliyor ve ikisi indeksle
	eşleşiyor.
	"""
	by_parcel = {}
	for row in shipment.get("custom_parcel_items") or []:
		if not row.get("item_code") or not row.get("parcel_no"):
			continue
		qty = flt(row.qty)
		if qty <= 0:
			continue
		no = int(row.parcel_no)
		by_parcel.setdefault(no, {})
		by_parcel[no][row.item_code] = by_parcel[no].get(row.item_code, 0) + qty

	return [(no, by_parcel[no]) for no in sorted(by_parcel)]


def _fulfillable_lines(setting, order_id):
	"""Shopify'ın hâlâ gönderilebilir kalemleri, ERPNext ürün koduna bağlanmış.

	[{fulfillment_order_id, line_id, item_code, qty}]. Quantities are consumed as
	parcels claim them.

	Matching runs through the order's own line items, which carry the SKU, joined
	to the fulfillment order lines by line_item_id. A line that resolves to no
	ERPNext item is skipped rather than guessed at — a fulfillment naming the
	wrong product is worse than one line missing.
	"""
	order = _get(setting, f"orders/{order_id}.json?fields=id,line_items")
	line_items = ((order or {}).get("order") or {}).get("line_items") or []

	by_line_id = {}
	for li in line_items:
		by_line_id[li.get("id")] = _resolve_item_code(li)

	lines = []
	for fo in _get_fulfillment_orders(setting, order_id):
		if fo.get("status") not in ("open", "in_progress"):
			continue
		for li in fo.get("line_items") or []:
			qty = flt(li.get("fulfillable_quantity") or li.get("quantity") or 0)
			if qty <= 0:
				continue
			item_code = by_line_id.get(li.get("line_item_id"))
			if not item_code:
				continue
			lines.append({
				"fulfillment_order_id": fo.get("id"),
				"line_id": li.get("id"),
				"item_code": item_code,
				"qty": qty,
			})
	return lines


def _resolve_item_code(line_item):
	sku = (line_item.get("sku") or "").strip()
	if sku and frappe.db.exists("Item", sku):
		return sku

	from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_item import ecommerce_item

	try:
		return ecommerce_item.get_erpnext_item_code(
			integration="shopify",
			integration_item_code=str(line_item.get("product_id") or ""),
			variant_id=str(line_item.get("variant_id") or "") or None,
		)
	except Exception:
		return None


def _claim(available, wanted):
	"""Take {item_code: qty} out of the fulfillable lines.

	Quantities are decremented as they are claimed, so an item split across two
	parcels is not reported twice — the second box can only claim what the first
	left.

	Returns {fulfillment_order_id: [{"id": line_id, "quantity": n}]}.
	"""
	claimed = {}
	for item_code, qty in wanted.items():
		remaining = qty
		for line in available:
			if remaining <= 0:
				break
			if line["item_code"] != item_code or line["qty"] <= 0:
				continue
			take = min(remaining, line["qty"])
			line["qty"] -= take
			remaining -= take
			claimed.setdefault(line["fulfillment_order_id"], []).append(
				{"id": line["line_id"], "quantity": int(take)}
			)
	return claimed


def _post_fulfillment(setting, claimed, number, url, notify, carrier=None):
	"""One fulfillment per fulfillment order. Returns the ids created.

	Not one fulfillment spanning several. Splitting an order in Shopify — after
	the shipment was built, which is normal — puts its lines into separate
	fulfillment orders, and a fulfillment cannot cover more than one of them:
	they can sit at different locations. A combined request is refused outright.

	The same tracking number goes on each, which Shopify allows. The customer
	sees one number against every line it actually covers.
	"""
	created = []
	for fo_id, items in (claimed or {}).items():
		if not items:
			continue
		payload = {
			"fulfillment": {
				"line_items_by_fulfillment_order": [
					{"fulfillment_order_id": fo_id, "fulfillment_order_line_items": items}
				],
				# Taşıyıcı adı burada gidiyor, ayrı bir güncelleme çağrısında değil.
				# `fulfillments/{id}/update_tracking.json` `tracking_info`'yu
				# birleştirmiyor, **değiştiriyor**: yalnız `company` gönderen bir
				# çağrı numarayı ve bağlantıyı siliyor. Shopify'da fulfillment
				# oluşup takip numarasının boş kalmasının sebebi buydu.
				"tracking_info": {"number": number, "url": url, "company": carrier},
				"notify_customer": bool(notify),
			}
		}
		response = _post(setting, "fulfillments.json", payload)
		fulfillment_id = ((response or {}).get("fulfillment") or {}).get("id")
		if fulfillment_id:
			created.append(fulfillment_id)
	return created


def _pair_parcels_with_tracking(shipment, parcels):
	"""[(parcel_no, wanted, number, url, carrier)] or None.

	Pairs each parcel with the tracking number that parcel actually carries.

	Not by position. `awb_number` is a comma-joined string built in the carrier's
	parcel order, and `custom_parcel_items` is read in `parcel_no` order — the two
	orders are unrelated. A shipment of one pillow box and one mattress box went
	out with the mattress first at the carrier and the pillow first in our table,
	so index pairing put the mattress's number on the pillow and the pillow's on
	the mattress. The customer clicked their mattress and watched a pillow.

	`custom_tracking_details` is written parcel by parcel from the carrier's own
	response and holds the tracking number together with the SKUs that parcel
	contained. That pairing is the only one that is actually stated rather than
	assumed.

	Returns None when the pairing cannot be made with certainty. The caller then
	falls back to a single fulfillment, which reports less but never points a
	customer at the wrong box.
	"""
	raw = shipment.get("custom_tracking_details")
	if not raw:
		return None

	try:
		details = json.loads(raw)
	except (ValueError, TypeError):
		return None
	if not isinstance(details, list) or not details:
		return None

	# SKU listesi virgülle birleştirilmiş metin olarak duruyor ve kaynağında 50
	# karaktere kırpılıyor; uzun bir ürün kodu eşleşmez, o zaman eşleme kurulamaz
	# ve tek fulfillment'a düşülür — yanlış eşleştirmektense az bildirmek.
	kalan = []
	for row in details:
		if not isinstance(row, dict):
			return None
		number = (row.get("tracking_number") or "").strip()
		if not number:
			return None
		skus = frozenset(s.strip() for s in (row.get("sku") or "").split(",") if s.strip())
		if not skus:
			return None
		kalan.append({
			"skus": skus,
			"number": number,
			"url": (row.get("tracking_url") or "").strip() or None,
			"carrier": (row.get("carrier") or "").strip() or None,
		})

	eslesme = []
	for parcel_no, wanted in parcels:
		aranan = frozenset(wanted.keys())
		bulunan = None
		for index, aday in enumerate(kalan):
			if aday["skus"] == aranan:
				bulunan = kalan.pop(index)
				break
		if not bulunan:
			# Bir koli eşleşmediyse hepsinden vazgeçiliyor. Kısmen doğru bir
			# eşleme, hangi satırın doğru olduğunu bilmeden kullanılamaz.
			return None
		eslesme.append((parcel_no, wanted, bulunan["number"], bulunan["url"], bulunan["carrier"]))

	return eslesme


def _fulfil_per_parcel(setting, shipment, available, pairing):
	"""A parcel's own tracking number on every fulfillment it covers.

	A parcel can hold lines from two fulfillment orders, and each of those needs
	its own fulfillment — so one parcel may produce more than one, all carrying
	that parcel's number.
	"""
	notify = setting.get("notify_customer_on_tracking_push")
	created = []
	unsent = []
	for parcel_no, wanted, number, url, carrier in pairing:
		claimed = _claim(available, wanted)
		if not claimed:
			# Bu kolinin içeriği Shopify tarafında zaten karşılanmış: fulfillment
			# açılmıyor ve numarası hiçbir yere gitmiyor. Sessiz kalırsa müşteri
			# bir kutuyu takip edemez ve sebebi hiçbir kayıtta görünmez.
			unsent.append(
				_("parcel {0} ({1}): nothing left to report — {2}").format(
					parcel_no, number or _("no number"),
					", ".join(f"{code} x{qty:g}" for code, qty in wanted.items()),
				)
			)
			continue
		# Taşıyıcı adı da kolinin kendisinden geliyor. Gönderi başlığındaki
		# `carrier`, koliler ayrı taşıyıcılarla gittiğinde "DPD, FEDEX" gibi
		# birleşik bir metin oluyor ve onu Shopify'a yazmak takip bağlantısını
		# çalışmaz hâle getirir.
		for fulfillment_id in _post_fulfillment(
			setting, claimed,
			number,
			url,
			notify,
			carrier or shipment.get("carrier"),
		):
			created.append(fulfillment_id)
	return created, unsent


def _parcel_carriers(shipment):
	"""{parcel_no: carrier} where each box says who carried it.

	Boxes can go with different carriers — a 2 kg pillow on a parcel service and
	a 32 kg mattress on freight — and the shipment header then holds them joined
	together. Sending that joined string to Shopify as the carrier name leaves
	the customer with a tracking link that resolves to nothing.
	"""
	carriers = {}
	for index, row in enumerate(shipment.get("shipment_parcel") or [], start=1):
		name = row.get("custom_shipping_carrier")
		if name:
			carriers[index] = name
	return carriers


def _fulfil_whole(setting, shipment, available, parcels, numbers, urls):
	wanted = {}
	for line in available:
		wanted[line["item_code"]] = wanted.get(line["item_code"], 0) + line["qty"]

	carriers = _parcel_carriers(shipment)
	# Tek fulfillment açılıyorsa taşıyıcı da tek olmalı; koliler ayrı taşıyıcılarla
	# gittiyse başlıktaki birleşik metin yerine ilk kolinin taşıyıcısı yazılıyor.
	carrier = carriers.get(1) or shipment.get("carrier")
	created = _post_fulfillment(
		setting, _claim(available, wanted),
		numbers[0] if numbers else None,
		urls[0] if urls else None,
		setting.get("notify_customer_on_tracking_push"),
		carrier,
	)
	note = _unsent_note(numbers, parcels)
	return created, ([note] if note else [])


@frappe.whitelist()
def repair_tracking(shipment, notify=0):
	"""Put the tracking numbers back on fulfillments that lost them.

	Needed because of a fault since fixed: the carrier name used to be set in a
	second call to `update_tracking`, and that endpoint **replaces** tracking
	info rather than merging it — sending only the company wiped the number and
	the link. Fulfillments created before the fix carry a carrier and nothing
	else.

	The ids stored on the delivery note are in parcel order, the same order the
	tracking numbers are joined in, so each fulfillment gets its own number back.
	"""
	notify = frappe.parse_json(notify) if isinstance(notify, str) else notify
	setting = frappe.get_cached_doc(SETTING_DOCTYPE)
	doc = frappe.get_doc("Shipment", shipment)

	numbers = [t.strip() for t in (doc.awb_number or "").split(",") if t.strip()]
	urls = [u.strip() for u in (doc.tracking_url or "").split(",") if u.strip()]
	carriers = _parcel_carriers(doc)

	# Fulfillment id'leri koli sırasında saklandı, `awb_number` taşıyıcının
	# sırasında yazıldı — ikisi aynı sıra değil. Bu araç sırayı düzeltmeden
	# yazsaydı, onarmaya çalıştığı belgeye kutuların numaralarını çapraz koyardı.
	pairing = _pair_parcels_with_tracking(doc, _parcel_contents(doc))
	if pairing:
		numbers = [row[2] for row in pairing]
		urls = [row[3] or "" for row in pairing]
		carriers = {index: row[4] for index, row in enumerate(pairing, start=1) if row[4]}

	repaired = []
	skipped = []
	for row in doc.get("shipment_delivery_note") or []:
		if not row.delivery_note:
			continue
		stored = frappe.db.get_value("Delivery Note", row.delivery_note, FULLFILLMENT_ID_FIELD)
		if not stored:
			continue
		ids = [f.strip() for f in str(stored).split(",") if f.strip()]
		for index, fulfillment_id in enumerate(ids):
			number = numbers[index] if index < len(numbers) else None
			if not number:
				# Numarasız güncelleme duran numarayı **siler**: bu uç nokta
				# `tracking_info`'yu değiştiriyor, birleştirmiyor. Aracın kendisi
				# tam da düzeltmeye çalıştığı hatayı yapıyordu — saklanan id
				# sayısı takip numarası sayısından fazla olduğunda.
				skipped.append(fulfillment_id)
				continue
			_update_tracking(
				setting,
				fulfillment_id,
				number,
				urls[index] if index < len(urls) else None,
				carriers.get(index + 1) or doc.get("carrier"),
				notify,
			)
			repaired.append(fulfillment_id)
		break  # aynı irsaliye koli başına tekrarlıyor

	return {"repaired": repaired, "skipped": skipped}


def _update_tracking(setting, fulfillment_id, number, url, carrier, notify=False):
	"""Rewrite a fulfillment's tracking info in full.

	Every field goes every time. This endpoint replaces the object, so a partial
	update is a deletion of whatever was left out — which is how the numbers went
	missing in the first place.
	"""
	_post(
		setting, f"fulfillments/{fulfillment_id}/update_tracking.json",
		{
			"fulfillment": {
				"tracking_info": {"number": number, "url": url, "company": carrier},
				"notify_customer": bool(notify),
			}
		},
	)


def _get_fulfillment_orders(setting, order_id):
	data = _get(setting, f"orders/{order_id}/fulfillment_orders.json")
	return (data or {}).get("fulfillment_orders") or []


def _headers(setting):
	return {
		"X-Shopify-Access-Token": setting.get_password("password"),
		"Content-Type": "application/json",
		"Accept": "application/json",
	}


def _url(setting, path):
	shop = (setting.shopify_url or "").replace("https://", "").replace("http://", "").strip("/")
	return f"https://{shop}/admin/api/{API_VERSION}/{path}"


def _get(setting, path):
	response = requests.get(_url(setting, path), headers=_headers(setting), timeout=30)
	if not response.ok:
		frappe.throw(_("Shopify {0}: {1}").format(response.status_code, response.text[:300]))
	return response.json()


def _post(setting, path, payload):
	response = requests.post(
		_url(setting, path), headers=_headers(setting), data=json.dumps(payload), timeout=30
	)
	if not response.ok:
		frappe.throw(_("Shopify {0}: {1}").format(response.status_code, response.text[:300]))
	return response.json()


# ---------------------------------------------------------------------------
# Onay anı yetmiyor — takip numarası sonradan yazılıyor
# ---------------------------------------------------------------------------
#
# `on_submit` doğru an değil. Etiket, gönderi **onaylandıktan sonra** alınıyor ve
# `awb_number` erpnext_shipping tarafından `db_set` ile yazılıyor — `db_set`
# hiçbir doküman olayı tetiklemez. Yani kanca, numara henüz boşken çalışıp
# "takip numarası yok" diyerek çıkıyor, numara yazılırken de hiçbir şey olmuyor.
#
# Bu yüzden asıl tetikleyici bir tarama. Kanca yine duruyor: numarası onay
# anında hazır olan bir gönderi varsa Shopify'ı hemen öğrensin.

# Taramanın geriye bakma penceresi. Küçük tutuluyor: geçmişe dönük bir süpürme,
# aylar önce kapanmış siparişler için müşteriye "kargoya verildi" e-postası
# gönderir.
SCAN_DAYS = 7


@frappe.whitelist()
def push_unsent_tracking(days=SCAN_DAYS, limit=50, dry_run=True):
	"""Report shipments whose tracking never reached Shopify.

	Bounded by age on purpose. A sweep over history would fulfil orders that were
	closed months ago, and Shopify emails the customer when an order is
	fulfilled — the loudest possible way to be wrong.

	Defaults to a dry run: it tells Shopify things, and something that tells a
	customer something should have to be asked twice.
	"""
	dry_run = frappe.parse_json(dry_run) if isinstance(dry_run, str) else dry_run
	days = int(days)
	limit = int(limit)

	setting = frappe.get_cached_doc(SETTING_DOCTYPE)
	if not setting.is_enabled() or not setting.get("push_tracking_to_shopify"):
		return {"skipped": "disabled"}

	cutoff = add_days(nowdate(), -days)
	results = {"pushed": [], "unsent": [], "skipped": [], "failed": [], "dry_run": bool(dry_run)}

	shipments = frappe.get_all(
		"Shipment",
		filters={"docstatus": 1, "awb_number": ["is", "set"], "creation": [">=", cutoff]},
		fields=["name"],
		order_by="creation desc",
		limit=limit,
	)

	for row in shipments:
		notes = frappe.get_all(
			"Shipment Delivery Note", filters={"parent": row.name}, pluck="delivery_note"
		)
		wanted = []
		for delivery_note in notes:
			if not delivery_note:
				continue
			state = frappe.db.get_value(
				"Delivery Note", delivery_note, [ORDER_ID_FIELD, FULLFILLMENT_ID_FIELD], as_dict=True
			)
			if not state or not state.get(ORDER_ID_FIELD):
				continue  # Shopify siparişi değil
			if state.get(FULLFILLMENT_ID_FIELD):
				continue  # zaten bildirilmiş
			if delivery_note not in wanted:
				wanted.append(delivery_note)

		if not wanted:
			continue

		if dry_run:
			results["pushed"].append(f"{row.name}: {', '.join(wanted)}")
			continue

		try:
			outcome = push_tracking(row.name)
			for message in outcome.get("unsent") or []:
				results["unsent"].append(f"{row.name}: {message}")
			if outcome.get("errors"):
				for message in outcome["errors"]:
					results["failed"].append(f"{row.name}: {message}")
			if outcome.get("fulfilled"):
				# `fulfilled` irsaliye başına bir kayıt taşıyor; fulfillment sayısı
				# içindeki listede. Öncesi irsaliye sayısını "fulfillment" diye
				# yazıyordu ve iki koliyle çıkan bir gönderi "1" görünüyordu.
				count = 0
				notes = []
				for entry in outcome["fulfilled"]:
					count += len(entry.get("fulfillments") or [])
					notes.append(entry.get("delivery_note"))
				results["pushed"].append(
					f"{row.name}: {count} fulfillment(s) on {', '.join(str(n) for n in notes)}"
				)
			elif not outcome.get("errors"):
				results["skipped"].append(f"{row.name}: {outcome.get('skipped') or 'nothing to send'}")
		except Exception as exception:
			results["failed"].append(f"{row.name}: {exception}")

	return results


def scan_unsent_tracking():
	"""Scheduler: send the tracking that the submit hook could not."""
	results = push_unsent_tracking(dry_run=False)
	if results.get("failed"):
		frappe.log_error(
			message="\n".join(results["failed"]),
			title="Shopify tracking could not be pushed",
		)
