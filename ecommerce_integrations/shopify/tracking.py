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
	"""Hook: a Shipment was submitted, tell Shopify about it."""
	push_tracking(doc.name)


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
	# Her satır için ayrı çağrı yapmak gereksiz: ilk çağrı fulfillment id'yi yazar,
	# ikincisi onu görüp çıkar. Yine de tekilleştiriliyor, çünkü "iki kez denendi"
	# ile "iki kez bildirildi" arasındaki farkı logdan okumak zor.
	seen = []
	for row in doc.get("shipment_delivery_note") or []:
		if row.delivery_note and row.delivery_note not in seen:
			seen.append(row.delivery_note)

	results = []
	errors = []
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

	# Hata, gönderiyi engellememeli ama kaybolmamalı da: çağıran görsün diye
	# sonuçla birlikte dönüyor. Aksi hâlde reddedilmiş bir istek, "yapacak iş
	# yoktu" ile aynı görünür.
	return {"fulfilled": results, "errors": errors}


def _fulfil_delivery_note(setting, shipment, delivery_note):
	dn = frappe.db.get_value(
		"Delivery Note", delivery_note,
		["name", ORDER_ID_FIELD, FULLFILLMENT_ID_FIELD],
		as_dict=True,
	)
	if not dn or not dn.get(ORDER_ID_FIELD):
		return None  # Shopify siparişi değil

	# Zaten bildirilmişse tekrar etme. İkinci bir fulfillment, müşteriye ikinci bir
	# "kargoya verildi" e-postası demek.
	if dn.get(FULLFILLMENT_ID_FIELD):
		return None

	order_id = dn.get(ORDER_ID_FIELD)
	available = _fulfillable_lines(setting, order_id)
	if not available:
		return None  # Shopify tarafında gönderilecek bir şey kalmamış

	numbers = [t.strip() for t in (shipment.awb_number or "").split(",") if t.strip()]
	urls = [u.strip() for u in (shipment.tracking_url or "").split(",") if u.strip()]
	parcels = _parcel_contents(shipment)

	# Parça bazında bildirmek ancak hangi kutunun hangi numarayı taşıdığı bilinirse
	# doğru olur. Koli-ürün tablosu boşsa ya da koli sayısı takip numarası sayısıyla
	# tutmuyorsa bu bilinmiyor demektir — o hâlde tahmin etmek yerine tek
	# fulfillment açılıp neyin eksik kaldığı yazılır.
	per_parcel = len(parcels) > 1 and len(parcels) == len(numbers)

	if per_parcel:
		fulfillments = _fulfil_per_parcel(setting, shipment, available, parcels, numbers, urls)
	else:
		fulfillments = _fulfil_whole(setting, shipment, available, numbers, urls)

	if not fulfillments:
		return None

	frappe.db.set_value(
		"Delivery Note", dn.name, FULLFILLMENT_ID_FIELD,
		", ".join(str(f) for f in fulfillments), update_modified=False,
	)
	create_shopify_log(
		status="Success",
		method="ecommerce_integrations.shopify.tracking.push_tracking",
		message=(
			f"Shopify order {order_id}: {len(fulfillments)} fulfillment(s) from {shipment.name}"
			+ ("" if per_parcel else _unsent_note(numbers, parcels))
		),
	)
	return {"delivery_note": dn.name, "fulfillments": fulfillments}


def _unsent_note(numbers, parcels):
	"""Neyin gitmediğini söyle. Sessiz kalırsa iki kutulu müşteri birini takip
	edebildiğinde sebebi hiçbir yerde görünmez."""
	if len(numbers) <= 1:
		return ""
	reason = (
		"parcel items are not filled in"
		if not parcels
		else f"{len(parcels)} parcels but {len(numbers)} tracking numbers"
	)
	return (
		f" — sent as one fulfillment because {reason}, so only {numbers[0]} reached Shopify;"
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


def _post_fulfillment(setting, claimed, number, url, notify):
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
				"tracking_info": {"number": number, "url": url},
				"notify_customer": bool(notify),
			}
		}
		response = _post(setting, "fulfillments.json", payload)
		fulfillment_id = ((response or {}).get("fulfillment") or {}).get("id")
		if fulfillment_id:
			created.append(fulfillment_id)
	return created


def _fulfil_per_parcel(setting, shipment, available, parcels, numbers, urls):
	"""A parcel's own tracking number on every fulfillment it covers.

	A parcel can hold lines from two fulfillment orders, and each of those needs
	its own fulfillment — so one parcel may produce more than one, all carrying
	that parcel's number.
	"""
	notify = setting.get("notify_customer_on_tracking_push")
	carriers = _parcel_carriers(shipment)
	created = []
	for index, (parcel_no, wanted) in enumerate(parcels):
		claimed = _claim(available, wanted)
		if not claimed:
			continue
		# Kolinin kendi taşıyıcısı. Gönderi başlığındaki `carrier`, koliler ayrı
		# taşıyıcılarla gittiğinde "DPD, FEDEX" gibi birleşik bir metin oluyor;
		# onu Shopify'a yazmak takip bağlantısını çalışmaz hâle getirir.
		carrier = carriers.get(parcel_no) or shipment.get("carrier")
		for fulfillment_id in _post_fulfillment(
			setting, claimed,
			numbers[index] if index < len(numbers) else None,
			urls[index] if index < len(urls) else None,
			notify,
		):
			created.append(fulfillment_id)
			_set_company(setting, fulfillment_id, carrier)
	return created


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


def _fulfil_whole(setting, shipment, available, numbers, urls):
	wanted = {}
	for line in available:
		wanted[line["item_code"]] = wanted.get(line["item_code"], 0) + line["qty"]

	created = _post_fulfillment(
		setting, _claim(available, wanted),
		numbers[0] if numbers else None,
		urls[0] if urls else None,
		setting.get("notify_customer_on_tracking_push"),
	)
	for fulfillment_id in created:
		_set_company(setting, fulfillment_id, shipment.get("carrier"))
	return created


def _set_company(setting, fulfillment_id, carrier):
	"""Name the carrier in a second call.

	Sending an unrecognised company name while creating the fulfillment can leave
	Shopify without a working tracking link. Set separately, and ignored on
	failure: by then the parcel is already reported, and a missing carrier name is
	a smaller loss than an error on a shipment that has left.
	"""
	if not carrier:
		return
	try:
		_post(
			setting, f"fulfillments/{fulfillment_id}/update_tracking.json",
			{"fulfillment": {"tracking_info": {"company": carrier}, "notify_customer": False}},
		)
	except Exception:
		pass


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
	results = {"pushed": [], "skipped": [], "failed": [], "dry_run": bool(dry_run)}

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
