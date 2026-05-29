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
# Order. Instead of blocking the worker with sleep(), the handler re-enqueues
# itself a bounded number of times so the Sales Order has time to appear.
MAX_INVOICE_RETRIES = 5


def prepare_sales_invoice(payload, request_id=None, retry_count=0):
	from ecommerce_integrations.shopify.order import get_sales_order

	order = payload

	frappe.set_user("Administrator")
	setting = frappe.get_doc(SETTING_DOCTYPE)
	frappe.flags.request_id = request_id

	try:
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
				create_shopify_log(
					status="Queued",
					message=(
						"Sales Order not found after retries. If the order is paid, "
						"orders/create will create the invoice."
					),
				)
			return

		create_sales_invoice(order, setting, sales_order)
		create_shopify_log(status="Success")
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)


def create_sales_invoice(shopify_order, setting, so):
	if (
		not frappe.db.get_value("Sales Invoice", {ORDER_ID_FIELD: shopify_order.get("id")}, "name")
		and so.docstatus == 1
		and not so.per_billed
		and cint(setting.sync_sales_invoice)
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
		
		# Set Customer Primary Address and Contact from Customer master
		if sales_invoice.customer:
			customer_doc = frappe.get_doc("Customer", sales_invoice.customer)
			
			# Set Billing Address (Primary Address)
			if customer_doc.customer_primary_address:
				sales_invoice.customer_address = customer_doc.customer_primary_address
				# Trigger address display update
				sales_invoice.run_method("set_customer_address")
			
			# Set Contact Person (Primary Contact)
			if customer_doc.customer_primary_contact:
				sales_invoice.contact_person = customer_doc.customer_primary_contact
				# Get contact details
				contact = frappe.get_doc("Contact", customer_doc.customer_primary_contact)
				sales_invoice.contact_display = " ".join(filter(None, [contact.first_name, contact.last_name]))
				sales_invoice.contact_email = contact.email_id
				sales_invoice.contact_mobile = contact.mobile_no or contact.phone
		
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
			make_payment_entry_against_sales_invoice(sales_invoice, setting, posting_date, payment_gateway)

		if shopify_order.get("note"):
			sales_invoice.add_comment(text=f"Order Note: {shopify_order.get('note')}")


def set_cost_center(items, cost_center):
	for item in items:
		item.cost_center = cost_center


def get_payment_gateway(shopify_order) -> str:
	"""Extract payment gateway name from Shopify order."""
	payment_gateway_names = shopify_order.get("payment_gateway_names") or []
	if payment_gateway_names:
		return payment_gateway_names[0]  # Primary payment method
	return ""


def get_mode_of_payment(payment_gateway: str, setting) -> str | None:
	"""Get ERPNext Mode of Payment based on Shopify payment gateway.
	
	First checks Shopify Payment Gateway Mapping in settings.
	Falls back to finding Mode of Payment with same name.
	"""
	if not payment_gateway:
		return None
	
	# Check if there's a mapping in Shopify Settings
	if hasattr(setting, 'payment_gateway_mapping') and setting.payment_gateway_mapping:
		for mapping in setting.payment_gateway_mapping:
			if mapping.shopify_payment_gateway.lower() == payment_gateway.lower():
				return mapping.mode_of_payment
	
	# Fallback: Check if Mode of Payment exists with same/similar name
	mode_of_payment = frappe.db.get_value(
		"Mode of Payment",
		{"name": ("like", f"%{payment_gateway}%")},
		"name"
	)
	
	return mode_of_payment


def make_payment_entry_against_sales_invoice(doc, setting, posting_date=None, payment_gateway=None):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_entry = get_payment_entry(doc.doctype, doc.name, bank_account=setting.cash_bank_account)
	payment_entry.flags.ignore_mandatory = True
	payment_entry.reference_no = doc.name
	payment_entry.posting_date = posting_date or nowdate()
	payment_entry.reference_date = posting_date or nowdate()
	
	# Set Mode of Payment if available
	if payment_gateway:
		mode_of_payment = get_mode_of_payment(payment_gateway, setting)
		if mode_of_payment:
			payment_entry.mode_of_payment = mode_of_payment
	
	payment_entry.insert(ignore_permissions=True)
	payment_entry.submit()