"""Shopify refund -> the payment that sends the money back (Payment Entry, Pay).

`refund.py` builds the Credit Note, but the money leaving the payment gateway was
never recorded: the Credit Note stayed open with a negative balance (as if we
still owed the customer) and the gateway account never saw the refund, so it did
not match what Shopify paid out (Yapilacaklar, 2026-10-09: 7 of 9 Shopify credit
notes had no refund payment, 3,709.70).

The refund payment mirrors the sale's receipt (`invoice.make_payment_entry_against_sales_invoice`):
same gateway mapping (Payment Gateway Mapping -> Mode of Payment -> account),
dated when Shopify processed the refund, reference = the refund transaction id so
it is never entered twice.

Only built when ONE successful refund transaction covers the whole Credit Note.
Everything else is left to a person, because a wrong allocation is worse than a
missing one: a refund split over gateways, a refund larger or smaller than the
Credit Note (shipping or an adjustment refunded too), a gateway with no account
(gift card, store credit, unmapped).
"""

import frappe
from frappe.utils import cstr, flt, getdate


def successful_refunds(refund) -> list:
	"""Shopify refund transactions that actually moved money back."""
	return [
		t
		for t in (refund.get("transactions") or [])
		if t.get("kind") == "refund" and t.get("status") == "success"
	]


def make_refund_payment(credit_note, refund, setting) -> tuple:
	"""("created" | "exists" | "manual", message)."""
	hareketler = successful_refunds(refund)
	if not hareketler:
		return "manual", "Shopify reported no successful refund transaction"

	for t in hareketler:
		if frappe.db.exists("Payment Entry", {"reference_no": cstr(t.get("id")), "docstatus": 1}):
			return "exists", f"refund payment for transaction {t.get('id')} already exists"

	if len(hareketler) > 1:
		yollar = ", ".join(cstr(t.get("gateway")) for t in hareketler)
		return "manual", f"the money went back in {len(hareketler)} transactions ({yollar})"

	t = hareketler[0]
	tutar = flt(t.get("amount"))
	acik = abs(flt(frappe.db.get_value("Sales Invoice", credit_note.name, "outstanding_amount")))
	if abs(tutar - acik) > 0.01:
		return "manual", f"refunded {tutar:.2f} but the credit note is {acik:.2f}"

	from ecommerce_integrations.shopify.invoice import (
		get_mode_of_payment,
		get_mode_of_payment_account,
	)

	gateway = cstr(t.get("gateway"))
	mode_of_payment = get_mode_of_payment(gateway, setting)
	account = get_mode_of_payment_account(mode_of_payment, credit_note.company)
	if not account:
		return "manual", f"gateway '{gateway}' has no account (gift card, store credit or not mapped)"

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	tarih = getdate(t.get("processed_at") or t.get("created_at") or credit_note.posting_date)
	payment = get_payment_entry("Sales Invoice", credit_note.name, bank_account=account)
	payment.flags.ignore_mandatory = True
	payment.mode_of_payment = mode_of_payment
	payment.reference_no = cstr(t.get("id"))
	payment.reference_date = tarih
	payment.posting_date = tarih
	payment.insert(ignore_permissions=True)
	payment.submit()
	return "created", f"refund payment {payment.name} ({gateway}, {tutar:.2f})"


def record_refund_payment(credit_note, refund, setting) -> tuple:
	"""Build the refund payment without ever undoing the Credit Note.

	Runs in its own savepoint: a failure rolls back only the payment. Whatever
	is not built automatically is said on the Credit Note, where the person
	entering it will look.
	"""
	frappe.db.savepoint("shopify_refund_payment")
	try:
		durum, mesaj = make_refund_payment(credit_note, refund, setting)
	except Exception as e:  # dekont kalmali, yalniz odeme geri alinir
		frappe.db.rollback(save_point="shopify_refund_payment")
		durum, mesaj = "manual", f"refund payment failed: {e}"

	if durum == "manual":
		credit_note.add_comment(
			"Comment",
			f"Refund payment not created: {mesaj}. Enter it by hand: Payment Entry (Pay) "
			"against this credit note, from the account the money went back through.",
		)
	return durum, mesaj
