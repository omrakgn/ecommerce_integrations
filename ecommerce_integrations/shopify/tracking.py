"""Push tracking from ERPNext to Shopify.

Shopify normally learns about a shipment from SendCloud, because SendCloud holds
the Shopify integration. Labels built in ERPNext never pass through it, so the
order stays unfulfilled in Shopify and the customer is told nothing.

This closes that gap, and only that: it reports a parcel that already exists.

Off unless switched on. Fulfilling an order in Shopify emails the customer and
moves the order out of the open queue — a side effect nobody should get by
installing an app.
"""

import json

import frappe
import requests
from frappe import _

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
	"""Create a Shopify fulfillment carrying this shipment's tracking.

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
	fulfillment_orders = _get_fulfillment_orders(setting, order_id)
	open_orders = [f for f in fulfillment_orders if f.get("status") in ("open", "in_progress")]
	if not open_orders:
		return None  # Shopify tarafında gönderilecek bir şey kalmamış

	tracking_numbers = [t.strip() for t in (shipment.awb_number or "").split(",") if t.strip()]
	tracking_urls = [u.strip() for u in (shipment.tracking_url or "").split(",") if u.strip()]

	payload = {
		"fulfillment": {
			"line_items_by_fulfillment_order": [
				{"fulfillment_order_id": f.get("id")} for f in open_orders
			],
			"tracking_info": {
				"number": tracking_numbers[0] if tracking_numbers else None,
				"company": shipment.get("carrier") or None,
				"url": tracking_urls[0] if tracking_urls else None,
			},
			"notify_customer": bool(setting.get("notify_customer_on_tracking_push")),
		}
	}

	response = _post(setting, "fulfillments.json", payload)
	fulfillment = (response or {}).get("fulfillment") or {}
	if not fulfillment.get("id"):
		return None

	frappe.db.set_value(
		"Delivery Note", dn.name, FULLFILLMENT_ID_FIELD, str(fulfillment["id"]),
		update_modified=False,
	)
	create_shopify_log(
		status="Success",
		method="ecommerce_integrations.shopify.tracking.push_tracking",
		message=f"Shopify order {order_id} fulfilled from {shipment.name} "
		f"({tracking_numbers[0] if tracking_numbers else 'no tracking'})",
	)
	return {"delivery_note": dn.name, "fulfillment_id": fulfillment["id"]}


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
