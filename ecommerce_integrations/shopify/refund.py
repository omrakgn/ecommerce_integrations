"""Shopify refunds -> ERPNext Credit Notes.

Shopify fires `refunds/create` for full and partial refunds alike. Before this
module existed the event was not subscribed to at all, so a refunded order stayed
fully paid in ERPNext: no Credit Note, no correction to the receivable.

What a refund credits is decided by Shopify, not by us: `refund_line_items`
carries the exact quantity refunded per order line. We mirror that onto a return
Sales Invoice built from the original one.

Stock is deliberately left alone (`update_stock = 0`), exactly as the Bol
integration does: the original invoice did not move stock either (the Delivery
Note did), so a Credit Note must not. What Shopify did with the goods is written
in the remarks, per line (`restock_type`): goods coming back are received on a
separate return Delivery Note, goods taken off the order before shipping never
left the warehouse.

A refund without refunded items moves money only. When PayPal refuses the
refund that carried the items, the money goes back in a second refund without
them; that money belongs to the credit note already made (`_money_only_refund`).
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

	refunded_qty, unmapped = _refunded_quantities(refund)

	invoice = _invoice_for_refund(order_id, refunded_qty)
	if not invoice:
		return {
			"status": "Invalid",
			"message": (
				f"No submitted Sales Invoice for Shopify order {order_id}; "
				f"refund {refund_id} needs a manual Credit Note."
			),
		}

	if not refunded_qty:
		if unmapped:
			return {
				"status": "Invalid",
				"message": (
					f"Refund {refund_id} on order {order_id}: none of the refunded items could be "
					f"matched to an ERPNext item ({', '.join(unmapped)}). "
					f"Create the Credit Note manually against {invoice}."
				),
			}
		return _money_only_refund(refund, order_id, invoice, setting)

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

	# Paranin geri gidisi: dekonta bagli odeme (bkz. refund_payment). Kurulamazsa
	# dekont kalir, dekonta not duser.
	from ecommerce_integrations.shopify.refund_payment import record_refund_payment

	odeme_durumu, odeme_mesaji = record_refund_payment(credit_note, refund, setting)
	message += f" Payment: {odeme_mesaji}."
	if odeme_durumu == "pending":
		message += " The daily check builds the payment once Shopify completes the refund."

	if unmapped:
		# Partial credit is still better than none, but it must be visible.
		message += f" WARNING: unmapped refunded items skipped: {unmapped}."
		return {"status": "Partial Success", "message": message}
	if odeme_durumu == "manual":
		return {"status": "Partial Success", "message": message}
	return {"status": "Success", "message": message}


def _invoice_for_refund(order_id, refunded_qty) -> str | None:
	"""The order's invoice the refunded items were sold on.

	An order can carry more than one invoice (a zero-value one added later, a
	split billing). Taking whichever the database returned first credited the
	wrong one: the refund of #1216 looked at a 0.00 invoice from August, found
	none of its items there, and no credit note was made
	(docs/shopify-ciro-mutabakati.md §3.2). The invoice holding the most of the
	refunded items wins, the oldest on a tie; with no items to match, the oldest.
	"""
	invoices = frappe.get_all(
		"Sales Invoice",
		filters={ORDER_ID_FIELD: order_id, "docstatus": 1, "is_return": 0},
		pluck="name",
		order_by="posting_date asc, creation asc",
	)
	if not invoices:
		return None
	if len(invoices) == 1 or not refunded_qty:
		return invoices[0]

	best, best_count = invoices[0], 0
	for name in invoices:
		codes = set(
			frappe.get_all(
				"Sales Invoice Item",
				filters={"parent": name, "parenttype": "Sales Invoice", "qty": [">", 0]},
				pluck="item_code",
			)
		)
		count = len(codes & set(refunded_qty))
		if count > best_count:
			best, best_count = name, count
	return best


def _money_only_refund(refund, order_id, invoice, setting) -> dict:
	"""A refund without refunded items: money only, or an attempt that moved none.

	September 2026 (docs/shopify-ciro-mutabakati.md §3.1): PayPal refused the
	refund that carried the items, so its credit note was made without a
	payment, and the money went back days later in a second refund with no
	items. The log then said "create the Credit Note manually", which would have
	credited the same goods twice. That money belongs to the credit note already
	there, and its payment is attached to it.

	An attempt that moved no money needs nothing. A refund whose amount matches
	no credit note on the order (shipping, a goodwill amount) still needs a
	person, as before: crediting it needs accounts we will not guess.
	"""
	from ecommerce_integrations.shopify.refund_payment import (
		record_refund_payment,
		refund_transactions,
	)

	refund_id = cstr(refund.get("id"))
	hareketler = refund_transactions(refund)
	tutar = 0.0
	for t in hareketler:
		if t.get("status") in ("success", "pending"):
			tutar += flt(t.get("amount"))

	if not tutar:
		durumlar = ", ".join(sorted({cstr(t.get("status")) for t in hareketler})) or "no refund transaction"
		return {
			"status": "Success",
			"message": (
				f"Refund {refund_id} on order {order_id} refunded no items and moved no money "
				f"({durumlar}). Nothing to credit."
			),
		}

	adaylar = _credit_notes_for_amount(order_id, tutar)
	odemesiz = [r for r in adaylar if not r.payment and abs(flt(r.outstanding_amount) + tutar) < 0.01]

	if len(odemesiz) == 1:
		credit_note = frappe.get_doc("Sales Invoice", odemesiz[0].name)
		durum, mesaj = record_refund_payment(credit_note, refund, setting)
		if durum == "created":
			credit_note.add_comment(
				"Comment",
				f"Refund payment from Shopify refund {refund_id}: the refund that credited these "
				f"goods moved no money, this one did. {mesaj}.",
			)
			return {
				"status": "Success",
				"message": f"Refund {refund_id} carries the money for {credit_note.name}: {mesaj}.",
			}
		if durum == "exists":
			return {"status": "Success", "message": f"Refund {refund_id} for {credit_note.name}: {mesaj}."}
		if durum == "pending":
			neden = "Shopify reports it pending. Once it completes, enter"
		else:
			neden = f"the payment was not created ({mesaj}). Enter"
		return {
			"status": "Invalid",
			"message": (
				f"Refund {refund_id} carries the money for {credit_note.name}, but {neden} the "
				f"payment (Pay) against {credit_note.name} by hand. Do not create another credit note."
			),
		}

	if len(odemesiz) > 1:
		neden = (
			f"several unpaid credit notes of {tutar:.2f} on the order "
			f"({', '.join(r.name for r in odemesiz)}). Enter the payment against the right one by hand"
		)
	elif adaylar:
		neden = (
			f"credit note {', '.join(r.name for r in adaylar)} of this amount is already settled. "
			"Check that its payment is this refund; do not create another credit note"
		)
	else:
		neden = (
			f"no credit note of {tutar:.2f} on the order. Likely a shipping or adjustment refund: "
			f"create the Credit Note manually against {invoice}"
		)
	return {
		"status": "Invalid",
		"message": f"Refund {refund_id} on order {order_id} has no refunded items (amount {tutar:.2f}); {neden}.",
	}


def _credit_notes_for_amount(order_id, amount) -> list:
	"""The order's submitted credit notes of this amount, each with its payment if any."""
	return frappe.db.sql(
		f"""
		select si.name, si.outstanding_amount,
			(select pe.name from `tabPayment Entry Reference` per
				join `tabPayment Entry` pe on pe.name = per.parent
				where per.reference_doctype = 'Sales Invoice' and per.reference_name = si.name
					and pe.docstatus = 1
				limit 1) as payment
		from `tabSales Invoice` si
		where si.docstatus = 1 and si.is_return = 1 and si.{ORDER_ID_FIELD} = %(order)s
			and abs(si.grand_total + %(amount)s) < 0.01
		order by si.posting_date, si.creation
		""",
		{"order": order_id, "amount": flt(amount)},
		as_dict=True,
	)


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


def _remarks(refund, unmapped) -> str:
	parts = [f"Shopify refund {refund.get('id')}"]
	if refund.get("note"):
		parts.append(f"Note: {refund.get('note')}")
	removed, returned, typed = _restock_lines(refund)
	if removed:
		parts.append(
			f"Removed from the order before shipping: {', '.join(removed)}. "
			"Nothing comes back: do not receive these on a return Delivery Note."
		)
	if returned:
		parts.append(
			f"Shopify restocked these goods: {', '.join(returned)}. Receive them on a return Delivery Note."
		)
	elif refund.get("restock") and not typed:
		# Satir turu olmayan eski yuk: yalniz iade duzeyindeki isaret var.
		parts.append("Shopify restocked these goods. Receive them on a return Delivery Note.")
	if unmapped:
		parts.append(f"Unmapped items not credited: {', '.join(unmapped)}")
	return "\n".join(parts)


def _restock_lines(refund) -> tuple[list, list, bool]:
	"""(taken off before shipping, coming back, any line typed), from each line's `restock_type`.

	`cancel`: the line was never fulfilled and was taken off the order (a size
	changed before shipping, a cancelled pre-order). Nothing left the
	warehouse, so nothing comes back. `return`, and the older `legacy_restock`:
	shipped goods Shopify put back in stock. `no_restock`: neither.

	The remark used to read only the refund's `restock` flag and told the
	warehouse to receive goods that had never left: #1393, an 80x200 swapped
	for a 90x200 before shipping (docs/shopify-ciro-mutabakati.md §3.3).
	"""
	removed, returned, typed = [], [], False
	for line in refund.get("refund_line_items") or []:
		if flt(line.get("quantity")) <= 0:
			continue
		kind = cstr(line.get("restock_type"))
		if kind:
			typed = True
		item = line.get("line_item") or {}
		label = f"{cstr(item.get('sku') or item.get('title') or item.get('variant_id'))} x{cint(line.get('quantity'))}"
		if kind == "cancel":
			removed.append(label)
		elif kind in ("return", "legacy_restock"):
			returned.append(label)
	return removed, returned, typed
