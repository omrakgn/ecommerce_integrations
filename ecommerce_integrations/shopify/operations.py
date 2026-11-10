"""Shopify cards for the operations board (multichannel_core, /app/operations-board).

Listed in hooks.py under `operations_board_sources`. The board calls `cards(ctx)` and shows what
it returns; ctx carries `today` and `now` (whole seconds). A card is a dict of group, key, title,
count, tone (critical / warning / neutral), hint, and the doctype and list filters a click opens.
Contract: multichannel_core/operations/sources.py `card`, hub docs/plans/operasyon-ekrani.md.

A card counts work that waits for a person, never work that may rightly wait.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date

from ecommerce_integrations.shopify.constants import MODULE_NAME, SETTING_DOCTYPE

# Error rows of the integration log are never deleted; a window keeps the count about now.
FAILED_EVENT_DAYS = 7
# The daily refund-payment job keeps trying for 7 days before it leaves a note for a person.
REFUND_PAYMENT_WAIT_DAYS = 7


def card(group, key, title, count, tone, hint, doctype, filters, order=0):
	return {
		"group": group,
		"key": key,
		"title": title,
		"count": count,
		"tone": tone,
		"hint": hint,
		"doctype": doctype,
		"filters": filters,
		"order": order,
	}


def enabled():
	return bool(frappe.db.get_single_value(SETTING_DOCTYPE, "enable_shopify"))


def cards(ctx):
	if not enabled():
		return []
	return [failed_events(ctx), refunds_without_payment(ctx)]


def failed_events(ctx):
	since = add_to_date(ctx.now, days=-FAILED_EVENT_DAYS).strftime("%Y-%m-%d %H:%M:%S")
	filters = {"integration": MODULE_NAME, "status": "Error", "creation": [">=", since]}
	return card(
		"Orders",
		"shopify.failed_events",
		_("Shopify events that failed"),
		frappe.db.count("Ecommerce Integration Log", filters),
		"warning",
		_(
			"An order, payment, shipment or refund from Shopify that ERPNext could not process in the last 7 days. Open the log, fix the cause, press Retry."
		),
		"Ecommerce Integration Log",
		filters,
		order=10,
	)


def refunds_without_payment(ctx):
	"""Shopify credit notes still without a refund payment once the daily job has given up."""
	from ecommerce_integrations.shopify.refund_payment import unpaid_credit_notes

	before = add_days(ctx.today, -REFUND_PAYMENT_WAIT_DAYS)
	names = []
	for row in unpaid_credit_notes(days=60, limit=500):
		if row.posting_date and str(row.posting_date) < str(before):
			names.append(row.name)
	return card(
		"Invoices",
		"shopify.refund_payment",
		_("Shopify refunds without a refund payment"),
		len(names),
		"warning",
		_(
			"Shopify refunded the money, but ERPNext has no refund payment for these credit notes. Each one has a comment saying why; record the payment by hand."
		),
		"Sales Invoice",
		{"name": ["in", names]},
		order=10,
	)
