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
from frappe.utils import flt

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

	results = []
	for row in doc.get("shipment_delivery_note") or []:
		if not row.delivery_note:
			continue
		try:
			outcome = _fulfil_delivery_note(setting, doc, row.delivery_note)
		except Exception:
			create_shopify_log(
				status="Error",
				method="ecommerce_integrations.shopify.tracking.push_tracking",
				message=f"Shipment {doc.name}, Delivery Note {row.delivery_note}",
			)
			continue
		if outcome:
			results.append(outcome)

	return {"fulfilled": results}


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
	if not claimed:
		return None
	payload = {
		"fulfillment": {
			"line_items_by_fulfillment_order": [
				{"fulfillment_order_id": fo_id, "fulfillment_order_line_items": items}
				for fo_id, items in claimed.items()
			],
			"tracking_info": {"number": number, "url": url},
			"notify_customer": bool(notify),
		}
	}
	response = _post(setting, "fulfillments.json", payload)
	return ((response or {}).get("fulfillment") or {}).get("id")


def _fulfil_per_parcel(setting, shipment, available, parcels, numbers, urls):
	"""One fulfillment per parcel, each carrying its own tracking number."""
	notify = setting.get("notify_customer_on_tracking_push")
	created = []
	for index, (_parcel_no, wanted) in enumerate(parcels):
		claimed = _claim(available, wanted)
		if not claimed:
			continue
		fulfillment_id = _post_fulfillment(
			setting, claimed,
			numbers[index] if index < len(numbers) else None,
			urls[index] if index < len(urls) else None,
			notify,
		)
		if fulfillment_id:
			created.append(fulfillment_id)
			_set_company(setting, fulfillment_id, shipment.get("carrier"))
	return created


def _fulfil_whole(setting, shipment, available, numbers, urls):
	wanted = {}
	for line in available:
		wanted[line["item_code"]] = wanted.get(line["item_code"], 0) + line["qty"]

	fulfillment_id = _post_fulfillment(
		setting, _claim(available, wanted),
		numbers[0] if numbers else None,
		urls[0] if urls else None,
		setting.get("notify_customer_on_tracking_push"),
	)
	if not fulfillment_id:
		return []
	_set_company(setting, fulfillment_id, shipment.get("carrier"))
	return [fulfillment_id]


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
