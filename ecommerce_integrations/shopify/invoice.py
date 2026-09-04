import time

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe.utils import cint, cstr, getdate, nowdate

from ecommerce_integrations.shopify.constants import (
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.utils import create_shopify_log

# orders/paid can be processed before orders/create has committed the Sales
# Order. The handler re-enqueues itself a bounded number of times so the Sales
# Order has time to appear.
MAX_INVOICE_RETRIES = 5

# ...but a retry that fires immediately is not a retry. Measured on live data:
# the Sales Order lands 3-25s after orders/paid arrives (median 4s), while the
# old no-delay ladder burned all five attempts inside a second. Every paid order
# therefore logged a false "Sales Order not found after retries" and wasted five
# background jobs. Waiting 2/4/8/16/30s covers the observed window comfortably.
RETRY_BACKOFF_CAP = 30


def prepare_sales_invoice(payload, request_id=None, retry_count=0):
	from ecommerce_integrations.shopify.order import get_sales_order

	order = payload

	frappe.set_user("Administrator")
	setting = frappe.get_doc(SETTING_DOCTYPE)
	frappe.flags.request_id = request_id

	try:
		if retry_count:
			# Wait before looking again, then drop our snapshot so the Sales Order
			# committed by orders/create in another connection becomes visible.
			time.sleep(min(2**retry_count, RETRY_BACKOFF_CAP))
			frappe.db.rollback()

		sales_order = get_sales_order(cstr(order["id"]))

		# Race condition: orders/paid processed before orders/create finished.
		# Re-enqueue this job (bounded) instead of blocking the worker, letting
		# orders/create commit the Sales Order in the meantime. The retry is sent
		# only via the public frappe.enqueue API (no RQ internals) so it stays
		# correct on every Frappe version.
		if not sales_order:
			if retry_count < MAX_INVOICE_RETRIES:
				frappe.enqueue(
					"ecommerce_integrations.shopify.invoice.prepare_sales_invoice",
					queue="short",
					enqueue_after_commit=True,
					payload=payload,
					request_id=request_id,
					retry_count=retry_count + 1,
				)
				create_shopify_log(
					status="Queued",
					message=f"Sales Order not found yet; retry {retry_count + 1}/{MAX_INVOICE_RETRIES} scheduled.",
				)
			else:
				# Not a failure: for a paid order, orders/create builds the invoice
				# itself (see order.create_order), so there is nothing left to do
				# here. Logged as Success so it stops looking like lost data.
				create_shopify_log(
					status="Success",
					message=(
						"Sales Order still not found; nothing to do. A paid order's "
						"invoice is created by the orders/create handler."
					),
				)
			return

		create_sales_invoice(order, setting, sales_order)
		create_shopify_log(status="Success")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def _invoice_already_exists(so, shopify_order):
	"""
	Whether this Shopify order already has an invoice, safe against a second
	worker asking at the same moment.

	Both webhook handlers reach invoice creation for a paid order: orders/create
	builds it inline, and orders/paid builds it once the Sales Order appears.
	On a busy store they land within a second of each other — measured at 1.0s
	and 1.8s apart on two live orders that each ended up with two submitted
	invoices for the same sale.

	A plain read cannot see a row the other transaction has not committed yet,
	so both saw "no invoice" and both created one. Taking a row lock on the
	Sales Order first serialises them: the second waits for the first to
	commit, then reads the invoice it wrote.

	**Both reads have to be locking reads.** MariaDB runs at REPEATABLE READ, so
	a plain SELECT answers from the snapshot the transaction took at its first
	read, which is long before the other worker committed. Locking the Sales
	Order made the second worker wait, and then it asked the question against
	that stale snapshot and still saw no invoice. Three orders were invoiced
	twice this way on 27, 28 and 31 August, 1.4 to 1.9 seconds apart, with the
	Sales Order lock already in place.

	`for_update=True` on the invoice read makes it a locking read as well, and a
	locking read sees the latest committed row rather than the snapshot.
	"""
	frappe.db.get_value("Sales Order", so.name, "name", for_update=True)
	return bool(
		frappe.db.get_value(
			"Sales Invoice",
			{ORDER_ID_FIELD: str(shopify_order.get("id"))},
			"name",
			for_update=True,
		)
	)


def create_sales_invoice(shopify_order, setting, so):
	# Cheap checks first; the lock is taken only when an invoice is actually due.
	if (
		cint(setting.sync_sales_invoice)
		and so.docstatus == 1
		and not so.per_billed
		and not _invoice_already_exists(so, shopify_order)
	):
		posting_date = getdate(shopify_order.get("created_at")) or nowdate()

		sales_invoice = make_sales_invoice(so.name, ignore_permissions=True)
		sales_invoice.set(ORDER_ID_FIELD, str(shopify_order.get("id")))
		sales_invoice.set(ORDER_NUMBER_FIELD, shopify_order.get("name"))
		sales_invoice.set_posting_time = 1
		sales_invoice.posting_date = posting_date
		sales_invoice.due_date = posting_date
		sales_invoice.naming_series = setting.sales_invoice_series or "SI-Shopify-"
		
		# Ensure customer is set (required for tax withholding)
		if not sales_invoice.customer and so.customer:
			sales_invoice.customer = so.customer
		elif not sales_invoice.customer:
			# Fallback to default customer from settings
			sales_invoice.customer = setting.default_customer
		
		# Set Customer Primary Address and Contact from Customer master.
		# Wrapped defensively: a missing/broken Contact or Address must never block
		# invoice creation for a paid order — worst case the invoice is created
		# without a pre-filled contact and can be corrected manually.
		if sales_invoice.customer:
			try:
				customer_doc = frappe.get_doc("Customer", sales_invoice.customer)

				# Billing Address (Primary Address)
				if customer_doc.customer_primary_address:
					sales_invoice.customer_address = customer_doc.customer_primary_address
					sales_invoice.run_method("set_customer_address")

				# Contact Person (Primary Contact)
				if customer_doc.customer_primary_contact and frappe.db.exists(
					"Contact", customer_doc.customer_primary_contact
				):
					sales_invoice.contact_person = customer_doc.customer_primary_contact
					contact = frappe.get_doc("Contact", customer_doc.customer_primary_contact)
					sales_invoice.contact_display = " ".join(
						filter(None, [contact.first_name, contact.last_name])
					)
					sales_invoice.contact_email = contact.email_id
					sales_invoice.contact_mobile = contact.mobile_no or contact.phone
			except Exception:
				frappe.log_error(
					title="Shopify: could not set customer address/contact on invoice",
					message=frappe.get_traceback(),
				)
		
		# Ensure debit_to is set
		if not sales_invoice.debit_to:
			sales_invoice.debit_to = frappe.db.get_value(
				"Company", sales_invoice.company, "default_receivable_account"
			)
		
		sales_invoice.flags.ignore_mandatory = True
		set_cost_center(sales_invoice.items, setting.cost_center)
		sales_invoice.insert(ignore_mandatory=True)
		sales_invoice.submit()
		if sales_invoice.grand_total > 0:
			# Get payment method from Shopify order
			payment_gateway = get_payment_gateway(shopify_order)
			make_payment_entry_against_sales_invoice(
				sales_invoice,
				setting,
				posting_date,
				payment_gateway,
				gateways=get_payment_gateway_names(shopify_order),
			)

		if shopify_order.get("note"):
			sales_invoice.add_comment(text=f"Order Note: {shopify_order.get('note')}")


def set_cost_center(items, cost_center):
	for item in items:
		item.cost_center = cost_center


def get_payment_gateway_names(shopify_order) -> list:
	"""Every gateway Shopify reported for this order, in its own order."""
	return shopify_order.get("payment_gateway_names") or []


def get_payment_gateway(shopify_order) -> str:
	"""Primary payment gateway — the one the Mode of Payment is derived from.

	Shopify can report several gateways for one order (gift card + card, or
	Shopify Payments + Klarna). ERPNext gets a single Payment Entry, so the whole
	amount lands on the first gateway's account. That is an accounting error the
	operator has to split by hand, which is why it is surfaced rather than logged
	to a file nobody reads — see make_payment_entry_against_sales_invoice.
	"""
	payment_gateway_names = get_payment_gateway_names(shopify_order)
	if not payment_gateway_names:
		return ""
	return payment_gateway_names[0]


def get_mode_of_payment(payment_gateway: str, setting) -> str | None:
	"""Get ERPNext Mode of Payment based on Shopify payment gateway.

	First checks Shopify Payment Gateway Mapping in settings, then falls back to
	an *exact* Mode of Payment name match. A fuzzy `LIKE %gateway%` fallback was
	removed on purpose: it could silently pick the wrong Mode of Payment (e.g.
	gateway "Cash on Delivery" matching "Cash"), posting the Payment Entry to the
	wrong account. When nothing matches we log and return None so the mismatch is
	visible rather than hidden.
	"""
	if not payment_gateway:
		return None

	# 1. Explicit mapping in Shopify Setting (case-insensitive)
	for mapping in setting.payment_gateway_mapping or []:
		if (mapping.shopify_payment_gateway or "").lower() == payment_gateway.lower():
			return mapping.mode_of_payment

	# 2. Exact Mode of Payment name match
	mode_of_payment = frappe.db.get_value("Mode of Payment", payment_gateway, "name")
	if mode_of_payment:
		return mode_of_payment

	# 3. Nothing matched — surface it instead of guessing an account.
	frappe.log_error(
		title="Shopify: Mode of Payment not mapped",
		message=(
			f"No Payment Gateway Mapping and no exact Mode of Payment for Shopify "
			f"gateway '{payment_gateway}'. Payment Entry will be created without a "
			f"Mode of Payment. Add a mapping in Shopify Setting to fix this."
		),
	)
	return None


def get_mode_of_payment_account(mode_of_payment: str, company: str) -> str | None:
	"""Account configured for this Mode of Payment in this company, if any."""
	if not mode_of_payment or not company:
		return None
	return frappe.db.get_value(
		"Mode of Payment Account",
		{"parent": mode_of_payment, "company": company},
		"default_account",
	)


def make_payment_entry_against_sales_invoice(
	doc, setting, posting_date=None, payment_gateway=None, gateways=None
):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	# Resolve the Mode of Payment *before* building the entry, so its account can
	# be handed to get_payment_entry. Assigning mode_of_payment afterwards does
	# NOT move the money: ERPNext derives paid_to from the Mode of Payment only in
	# the browser (payment_entry.js), never in validate(). Setting it server-side
	# left every gateway posting to setting.cash_bank_account, so the per-gateway
	# accounts (Klarna, PayPal, ...) stayed empty while the entry was labelled
	# correctly.
	mode_of_payment = get_mode_of_payment(payment_gateway, setting) if payment_gateway else None
	bank_account = get_mode_of_payment_account(mode_of_payment, doc.company) or setting.cash_bank_account

	payment_entry = get_payment_entry(doc.doctype, doc.name, bank_account=bank_account)
	payment_entry.flags.ignore_mandatory = True
	payment_entry.reference_no = doc.name
	payment_entry.posting_date = posting_date or nowdate()
	payment_entry.reference_date = posting_date or nowdate()

	if mode_of_payment:
		payment_entry.mode_of_payment = mode_of_payment

	# Split payment: ERPNext gets one Payment Entry, so the whole amount sits on
	# the first gateway's account. Say so on the entry itself and in Error Log,
	# because nothing else would reveal it until someone reconciles the accounts.
	if gateways and len(gateways) > 1:
		note = (
			f"Shopify split payment across: {', '.join(gateways)}. "
			f"The full amount is posted to the account of '{gateways[0]}'. "
			f"Split it manually across the other gateways' accounts."
		)
		# `custom_remarks` olmadan bu not kaybolur: ERPNext'in `validate()`
		# metodu `set_remarks()` calistiriyor ve o, isaret konulmamissa `remarks`
		# alanini kendi sablonuyla bastan yaziyor. Not yaziliyordu, insert
		# sirasinda siliniyordu ve kimse fark etmiyordu.
		payment_entry.custom_remarks = 1
		payment_entry.remarks = note
		frappe.log_error(title="Shopify: split payment posted to one account", message=f"{doc.name}: {note}")

	payment_entry.insert(ignore_permissions=True)
	payment_entry.submit()
