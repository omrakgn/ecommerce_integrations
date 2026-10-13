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

A refund that is still "pending" when the webhook arrives (Shopify Payments does
this: 2026-09-15-SALES-INVOICE-NR-00003 came in pending and turned success
later) is not a reason for a person to step in. `retry_refund_payments` reads
it again from Shopify every day and builds the payment once it has gone through.
"""

import frappe
from frappe.utils import add_days, cint, cstr, date_diff, flt, getdate, nowdate

from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	REFUND_ID_FIELD,
	SETTING_DOCTYPE,
)

# Gunluk kontrol bu kadar eski dekontlara bakar.
RETRY_DAYS = 60
# Bu kadar gun sonra hala "pending" ise dekonta not duser.
PENDING_GRACE_DAYS = 7
NOTE_PREFIX = "Refund payment not created"


def refund_transactions(refund) -> list:
	"""Shopify transactions of this refund that move money back (any status)."""
	return [t for t in (refund.get("transactions") or []) if t.get("kind") == "refund"]


def make_refund_payment(credit_note, refund, setting) -> tuple:
	"""("created" | "exists" | "pending" | "manual", message)."""
	hareketler = refund_transactions(refund)
	if not hareketler:
		return "manual", "the Shopify refund moved no money (no refund transaction)"

	for t in hareketler:
		if frappe.db.exists("Payment Entry", {"reference_no": cstr(t.get("id")), "docstatus": 1}):
			return "exists", f"refund payment for transaction {t.get('id')} already exists"

	# Bir parcasi bile beklemedeyse toplam belli degil: hepsi sonuclanana kadar bekle.
	for t in hareketler:
		if t.get("status") == "pending":
			return "pending", "Shopify has not completed the refund yet (pending)"

	basarili = []
	for t in hareketler:
		if t.get("status") == "success":
			basarili.append(t)
	if not basarili:
		durumlar = ", ".join(sorted({cstr(t.get("status")) for t in hareketler}))
		return "manual", f"the refund transaction did not go through ({durumlar})"

	if len(basarili) > 1:
		yollar = ", ".join(cstr(t.get("gateway")) for t in basarili)
		return "manual", f"the money went back in {len(basarili)} transactions ({yollar})"

	t = basarili[0]
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


def record_refund_payment(credit_note, refund, setting, retry=False) -> tuple:
	"""Build the refund payment without ever undoing the Credit Note.

	Runs in its own savepoint: a failure rolls back only the payment. Whatever
	is not built automatically is said on the Credit Note, where the person
	entering it will look, and said once: the daily retry sees the same Credit
	Note again and must not pile up the same note.

	`retry`: called by the daily check, not the webhook. A refund still pending
	after PENDING_GRACE_DAYS becomes a person's job; a payment built late says so
	on the Credit Note, which may carry an older "not created" note.
	"""
	frappe.db.savepoint("shopify_refund_payment")
	try:
		durum, mesaj = make_refund_payment(credit_note, refund, setting)
	except Exception as e:  # dekont kalmali, yalniz odeme geri alinir
		frappe.db.rollback(save_point="shopify_refund_payment")
		durum, mesaj = "manual", f"refund payment failed: {e}"

	if durum == "pending" and retry and date_diff(nowdate(), credit_note.posting_date) >= PENDING_GRACE_DAYS:
		durum, mesaj = (
			"manual",
			f"Shopify still reports the refund as pending after {PENDING_GRACE_DAYS} days",
		)

	if durum == "manual":
		_note_once(
			credit_note,
			f"{NOTE_PREFIX}: {mesaj}. Enter it by hand: Payment Entry (Pay) "
			"against this credit note, from the account the money went back through.",
		)
	elif durum == "created" and retry:
		credit_note.add_comment(
			"Comment", f"Refund payment created after Shopify completed the refund: {mesaj}."
		)
	return durum, mesaj


def _note_once(credit_note, text):
	if frappe.db.exists(
		"Comment",
		{
			"reference_doctype": "Sales Invoice",
			"reference_name": credit_note.name,
			"comment_type": "Comment",
			"content": ["like", f"{NOTE_PREFIX}%"],
		},
	):
		return
	credit_note.add_comment("Comment", text)


def unpaid_credit_notes(days=RETRY_DAYS, limit=50) -> list:
	"""Shopify credit notes of the last `days` that are still open and have no payment.

	A Credit Note with any submitted payment is left alone, even a partial one: a
	person is already on it.
	"""
	return frappe.db.sql(
		f"""
		select si.name, si.{REFUND_ID_FIELD} as refund_id,
			coalesce(nullif(si.{ORDER_ID_FIELD}, ''), orig.{ORDER_ID_FIELD}) as order_id
		from `tabSales Invoice` si
		left join `tabSales Invoice` orig on orig.name = si.return_against
		where si.docstatus = 1 and si.is_return = 1
			and ifnull(si.{REFUND_ID_FIELD}, '') != ''
			and si.outstanding_amount < 0
			and si.posting_date >= %(cutoff)s
			and not exists (
				select 1 from `tabPayment Entry Reference` per
				join `tabPayment Entry` pe on pe.name = per.parent
				where per.reference_doctype = 'Sales Invoice' and per.reference_name = si.name
					and pe.docstatus = 1
			)
		order by si.posting_date
		limit %(limit)s
		""",
		{"cutoff": add_days(nowdate(), -int(days)), "limit": int(limit)},
		as_dict=True,
	)


def _fetch_refund(refund_id, order_id) -> dict:
	from shopify.resources import Refund

	return Refund.find(refund_id, order_id=order_id).to_dict()


def retry_refund_payments():
	"""Scheduler (daily): build the refund payments the webhook could not.

	Reads each unpaid Shopify credit note's refund again from Shopify, because
	the webhook only saw it at one moment. Silent when nothing changed: a refund
	still pending waits for tomorrow, and a refund a person must enter already
	carries its note on the Credit Note.
	"""
	setting = frappe.get_doc(SETTING_DOCTYPE)
	if not setting.is_enabled() or not cint(setting.get("sync_refunds")):
		return

	adaylar = unpaid_credit_notes()
	if not adaylar:
		return

	from ecommerce_integrations.shopify.connection import temp_shopify_session
	from ecommerce_integrations.shopify.utils import create_shopify_log

	fetch = temp_shopify_session(_fetch_refund)
	kurulan, hatali = [], []
	for row in adaylar:
		# Shopify'i beklerken acik islem kalmasin (docs/olay-islem-icinde-bekleme.md).
		frappe.db.commit()
		try:
			if not row.order_id:
				raise ValueError("no Shopify order id on the credit note or its invoice")
			refund = fetch(row.refund_id, row.order_id)
			if not refund:
				raise ValueError("Shopify returned nothing for this refund")
			credit_note = frappe.get_doc("Sales Invoice", row.name)
			durum, mesaj = record_refund_payment(credit_note, refund, setting, retry=True)
			frappe.db.commit()
		except Exception as e:
			frappe.db.rollback()
			hatali.append(f"{row.name}: {e}")
			continue
		if durum == "created":
			kurulan.append(f"{row.name}: {mesaj}")

	if not kurulan and not hatali:
		return
	status = "Error" if hatali and not kurulan else ("Partial Success" if hatali else "Success")
	create_shopify_log(
		method="ecommerce_integrations.shopify.refund_payment.retry_refund_payments",
		status=status,
		message="\n".join(
			[
				f"Refund payments created late: {len(kurulan)}",
				*kurulan,
				f"Could not check: {len(hatali)}",
				*hatali,
			]
		),
		make_new=True,
	)
