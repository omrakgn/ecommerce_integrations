"""Shopify refunds -> ERPNext Credit Notes.

Shopify fires `refunds/create` for full and partial refunds alike. Before this
module existed the event was not subscribed to at all, so a refunded order stayed
fully paid in ERPNext: no Credit Note, no correction to the receivable.

What a refund credits is decided by Shopify, not by us: `refund_line_items`
carries the exact quantity refunded per order line. We mirror that onto a return
Sales Invoice built from the original one.

Stock is deliberately left alone (`update_stock = 0`), exactly as the Bol
integration does: the original invoice did not move stock either (the Delivery
Note did), so a Credit Note must not. Shopify's `restock` flag is recorded in the
remarks so goods coming back can be received on a separate return Delivery Note.
"""

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from frappe.utils import cint, cstr, flt, getdate

from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	REFUND_ID_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.product import get_item_code
from ecommerce_integrations.shopify.utils import create_shopify_log


def prepare_credit_note(payload, request_id=None):
	"""Webhook entry point for `refunds/create`."""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id
	refund = payload

	try:
		setting = frappe.get_doc(SETTING_DOCTYPE)
		if not cint(setting.sync_refunds):
			create_shopify_log(
				status="Invalid",
				message="Refund sync is disabled in Shopify Setting; no Credit Note created.",
			)
			return

		result = create_credit_note(refund, setting)
		create_shopify_log(status=result["status"], message=result["message"])
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def create_credit_note(refund, setting) -> dict:
	"""Build and submit the Credit Note for one Shopify refund.

	Returns {"status": <log status>, "message": str}. Every early exit says why in
	the message — a refund that cannot be credited must never look like a success.
	"""
	refund_id = cstr(refund.get("id"))
	order_id = cstr(refund.get("order_id"))

	existing = frappe.db.get_value("Sales Invoice", {REFUND_ID_FIELD: refund_id}, "name")
	if existing:
		return {"status": "Success", "message": f"Refund {refund_id} already credited by {existing}."}

	invoice = frappe.db.get_value(
		"Sales Invoice",
		{ORDER_ID_FIELD: order_id, "docstatus": 1, "is_return": 0},
		"name",
	)
	if not invoice:
		return {
			"status": "Invalid",
			"message": (
				f"No submitted Sales Invoice for Shopify order {order_id}; "
				f"refund {refund_id} needs a manual Credit Note."
			),
		}

	refunded_qty, unmapped = _refunded_quantities(refund)

	if not refunded_qty:
		# Shipping-only or adjustment-only refunds carry no line items. Crediting
		# them correctly needs the shipping/tax accounts, which we will not guess.
		amount = _refund_amount(refund)
		return {
			"status": "Invalid",
			"message": (
				f"Refund {refund_id} on order {order_id} has no refunded line items "
				f"(amount {amount}). Likely a shipping or adjustment refund — "
				f"create the Credit Note manually against {invoice}."
			),
		}

	credit_note = make_sales_return(invoice)
	credit_note.set(REFUND_ID_FIELD, refund_id)

	kept = []
	for row in credit_note.items:
		qty = refunded_qty.get(row.item_code)
		if qty:
			row.qty = -abs(qty)
			kept.append(row)

	if not kept:
		return {
			"status": "Invalid",
			"message": (
				f"Refund {refund_id}: none of the refunded items {sorted(refunded_qty)} "
				f"appear on invoice {invoice}. Credit Note not created."
			),
		}

	credit_note.items = kept
	# Accounting-only reversal — see module docstring.
	credit_note.update_stock = 0
	if setting.sales_invoice_series:
		credit_note.naming_series = setting.sales_invoice_series
	if refund.get("created_at"):
		credit_note.set_posting_time = 1
		credit_note.posting_date = getdate(refund.get("created_at"))

	credit_note.remarks = _remarks(refund, unmapped)
	credit_note.flags.ignore_mandatory = True
	credit_note.insert(ignore_permissions=True, ignore_mandatory=True)
	credit_note.submit()

	message = f"Credit Note {credit_note.name} created for Shopify refund {refund_id}."
	if unmapped:
		# Partial credit is still better than none, but it must be visible.
		message += f" WARNING: unmapped refunded items skipped: {unmapped}."
		return {"status": "Partial Success", "message": message}
	return {"status": "Success", "message": message}


def _refunded_quantities(refund) -> tuple[dict, list]:
	"""{item_code: refunded qty} plus the Shopify lines we could not map."""
	quantities: dict[str, float] = {}
	unmapped: list[str] = []

	for line in refund.get("refund_line_items") or []:
		qty = flt(line.get("quantity"))
		if qty <= 0:
			continue
		shopify_item = line.get("line_item") or {}
		item_code = get_item_code(shopify_item)
		if not item_code:
			unmapped.append(cstr(shopify_item.get("sku") or shopify_item.get("variant_id")))
			continue
		quantities[item_code] = quantities.get(item_code, 0) + qty

	return quantities, unmapped


def _refund_amount(refund) -> float:
	"""Total actually refunded, summed over successful refund transactions."""
	return sum(
		flt(t.get("amount"))
		for t in (refund.get("transactions") or [])
		if t.get("kind") == "refund" and t.get("status") == "success"
	)


def _remarks(refund, unmapped) -> str:
	parts = [f"Shopify refund {refund.get('id')}"]
	if refund.get("note"):
		parts.append(f"Note: {refund.get('note')}")
	if refund.get("restock"):
		parts.append("Shopify restocked these goods — receive them on a return Delivery Note.")
	if unmapped:
		parts.append(f"Unmapped items not credited: {', '.join(unmapped)}")
	return "\n".join(parts)
