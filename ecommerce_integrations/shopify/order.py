import json
from typing import Literal, Optional
from urllib.parse import urlparse, parse_qs

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, getdate, nowdate
from shopify.collection import PaginatedIterator
from shopify.resources import Order

from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import (
	CUSTOMER_ID_FIELD,
	DISCOUNT_CODES_FIELD,
	EVENT_MAPPER,
	LANDING_SITE_FIELD,
	MARKETING_CHANNEL_FIELD,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_STATUS_FIELD,
	PAYMENT_METHOD_FIELD,
	REFERRING_SITE_FIELD,
	SETTING_DOCTYPE,
	UTM_CAMPAIGN_FIELD,
	UTM_CONTENT_FIELD,
	UTM_MEDIUM_FIELD,
	UTM_SOURCE_FIELD,
	UTM_TERM_FIELD,
)
from ecommerce_integrations.shopify.customer import ShopifyCustomer
from ecommerce_integrations.shopify.product import create_items_if_not_exist, get_item_code
from ecommerce_integrations.shopify.utils import create_shopify_log
from ecommerce_integrations.utils.price_list import get_dummy_price_list
from ecommerce_integrations.utils.taxation import get_dummy_tax_category

DEFAULT_TAX_FIELDS = {
	"sales_tax": "default_sales_tax_account",
	"shipping": "default_shipping_charges_account",
}


def sync_sales_order(payload, request_id=None):
	order = payload
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	if frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: cstr(order["id"])}):
		create_shopify_log(status="Invalid", message="Sales order already exists, not synced")
		return
	try:
		shopify_customer = order.get("customer") if order.get("customer") is not None else {}
		shopify_customer["billing_address"] = order.get("billing_address", "")
		shopify_customer["shipping_address"] = order.get("shipping_address", "")
		customer_id = shopify_customer.get("id")
		if customer_id:
			customer = ShopifyCustomer(customer_id=customer_id)
			if not customer.is_synced():
				customer.sync_customer(customer=shopify_customer)
			else:
				customer.update_existing_addresses(shopify_customer)

		create_items_if_not_exist(order)

		setting = frappe.get_doc(SETTING_DOCTYPE)
		create_order(order, setting)
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True)
	else:
		create_shopify_log(status="Success")


def create_order(order, setting, company=None):
	# local import to avoid circular dependencies
	from ecommerce_integrations.shopify.fulfillment import create_delivery_note
	from ecommerce_integrations.shopify.invoice import create_sales_invoice

	so = create_sales_order(order, setting, company)
	if so:
		# Commit the Sales Order first to prevent race conditions
		frappe.db.commit()
		
		if order.get("financial_status") == "paid":
			create_sales_invoice(order, setting, so)

		if order.get("fulfillments"):
			create_delivery_note(order, setting, so)
	
	return so


def create_sales_order(shopify_order, setting, company=None):
	customer = setting.default_customer
	if shopify_order.get("customer", {}):
		if customer_id := shopify_order.get("customer", {}).get("id"):
			customer = frappe.db.get_value("Customer", {CUSTOMER_ID_FIELD: customer_id}, "name")

	so = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: shopify_order.get("id")}, "name")

	if not so:
		items = get_order_items(
			shopify_order.get("line_items"),
			setting,
			getdate(shopify_order.get("created_at")),
			taxes_inclusive=shopify_order.get("taxes_included"),
		)

		if not items:
			message = (
				"Following items exists in the shopify order but relevant records were"
				" not found in the shopify Product master"
			)
			product_not_exists = []  # TODO: fix missing items
			message += "\n" + ", ".join(product_not_exists)

			create_shopify_log(status="Error", exception=message, rollback=True)

			return ""

		taxes = get_order_taxes(shopify_order, setting, items)
		
		# Get discount information
		discount_info = get_discount_info(shopify_order)
		
		# Get Sales Partner from mapping
		sales_partner_info = get_sales_partner_from_mapping(shopify_order, setting)
		
		# Get payment method
		payment_gateway_names = shopify_order.get("payment_gateway_names") or []
		payment_method = ", ".join(payment_gateway_names) if payment_gateway_names else ""
		
		# Build Sales Order document
		so_data = {
			"doctype": "Sales Order",
			"naming_series": setting.sales_order_series or "SO-Shopify-",
			ORDER_ID_FIELD: str(shopify_order.get("id")),
			ORDER_NUMBER_FIELD: shopify_order.get("name"),
			"customer": customer,
			"transaction_date": getdate(shopify_order.get("created_at")) or nowdate(),
			"delivery_date": getdate(shopify_order.get("created_at")) or nowdate(),
			"company": setting.company,
			"cost_center": setting.cost_center,
			"set_warehouse": setting.warehouse,
			"po_no": shopify_order.get("name"),
			"po_date": getdate(shopify_order.get("created_at")) or nowdate(),
			"selling_price_list": get_dummy_price_list(),
			"items": items,
			"taxes": taxes,
			"tax_category": get_dummy_tax_category(),
			DISCOUNT_CODES_FIELD: discount_info.get("codes"),
			PAYMENT_METHOD_FIELD: payment_method,
			**get_marketing_attribution(shopify_order),
		}
		
		# Discount is always taken from Shopify's per-item calculation
		# (discount_percentage/rate set in get_order_items), so the Grand Total
		# always matches Shopify. ERPNext Pricing Rules are disabled to avoid
		# double-applying the discount.
		#
		# The discount code itself is kept for reference in DISCOUNT_CODES_FIELD
		# above; the native `coupon_code` field is intentionally NOT set because
		# setting it can trigger coupon validation / Pricing Rule application
		# (even with ignore_pricing_rule), which would either error out or
		# double-discount the order.
		so_data["ignore_pricing_rule"] = 1
		
		so = frappe.get_doc(so_data)
		
		# Add Sales Partner if found
		if sales_partner_info:
			so.sales_partner = sales_partner_info.get("sales_partner")
			if sales_partner_info.get("commission_rate"):
				# Adjust commission rate to calculate on Grand Total (tax-inclusive)
				# ERPNext calculates commission on Net Total, so we need to multiply by (1 + tax_rate)
				# Example: 10% on Grand Total = 10% × 1.19 = 11.9% on Net Total
				commission_rate = flt(sales_partner_info.get("commission_rate"))
				tax_rate = _get_order_tax_rate(shopify_order)
				adjusted_commission_rate = commission_rate * (1 + tax_rate)
				so.commission_rate = adjusted_commission_rate

		if company:
			so.update({"company": company, "status": "Draft"})
		so.flags.ignore_mandatory = True
		so.flags.shopiy_order_json = json.dumps(shopify_order)
		so.save(ignore_permissions=True)
		so.submit()

		if shopify_order.get("note"):
			so.add_comment(text=f"Order Note: {shopify_order.get('note')}")

	else:
		so = frappe.get_doc("Sales Order", so)

	return so


def get_order_items(order_items, setting, delivery_date, taxes_inclusive):
	items = []
	all_product_exists = True
	product_not_exists = []

	for shopify_item in order_items:
		if not shopify_item.get("product_exists"):
			all_product_exists = False
			product_not_exists.append(
				{"title": shopify_item.get("title"), ORDER_ID_FIELD: shopify_item.get("id")}
			)
			continue

		if all_product_exists:
			item_code = get_item_code(shopify_item)
			
			# Get original price (tax-inclusive from Shopify)
			original_price = flt(shopify_item.get("price"))
			qty = cint(shopify_item.get("quantity"))
			
			# Get discount amount and calculate discount percentage
			discount_amount = _get_total_discount(shopify_item)
			discount_percentage = 0.0
			if original_price > 0 and discount_amount > 0:
				discount_percentage = (discount_amount / qty) / original_price * 100
			
			items.append(
				{
					"item_code": item_code,
					"item_name": shopify_item.get("name"),
					"price_list_rate": original_price,  # Original price (tax-inclusive)
					"discount_percentage": discount_percentage,  # Discount %
					"rate": original_price - (discount_amount / qty),  # Discounted price (tax-inclusive)
					"delivery_date": delivery_date,
					"qty": qty,
					"stock_uom": shopify_item.get("uom") or "Nos",
					"warehouse": setting.warehouse,
					"cost_center": setting.cost_center,
					ORDER_ITEM_DISCOUNT_FIELD: discount_amount / qty,
				}
			)
		else:
			items = []

	# If any line item's product is missing, do not create a partial order.
	# (The in-loop `else` above only clears items when a *later* existing item
	# follows the missing one, so a missing *last* item would otherwise slip
	# through as a partial order.) create_sales_order handles the empty list by
	# logging an error and rolling back.
	if not all_product_exists:
		return []

	return items


def _get_item_price(line_item, taxes_inclusive: bool) -> float:
	"""Get item price (tax-inclusive, after discount).
	
	Returns the discounted price including tax, as shown in Shopify.
	"""
	price = flt(line_item.get("price"))  # Original price (tax-inclusive)
	qty = cint(line_item.get("quantity"))
	discount_amount = _get_total_discount(line_item)
	
	# Return discounted price (tax-inclusive)
	return price - (discount_amount / qty)


def _get_total_discount(line_item) -> float:
	discount_allocations = line_item.get("discount_allocations") or []
	return sum(flt(discount.get("amount")) for discount in discount_allocations)


def _get_order_tax_rate(shopify_order: dict) -> float:
	"""Get the primary tax rate from the order.
	
	Returns the tax rate as decimal (e.g., 0.19 for 19%).
	"""
	line_items = shopify_order.get("line_items") or []
	
	for item in line_items:
		tax_lines = item.get("tax_lines") or []
		if tax_lines:
			return flt(tax_lines[0].get("rate", 0))
	
	return 0.0


def get_order_taxes(shopify_order, setting, items):
	taxes = []
	line_items = shopify_order.get("line_items")
	taxes_inclusive = shopify_order.get("taxes_included", False)

	for line_item in line_items:
		item_code = get_item_code(line_item)
		for tax in line_item.get("tax_lines"):
			tax_rate = flt(tax.get("rate")) * 100  # Convert to percentage (0.19 -> 19)
			
			if taxes_inclusive:
				# Use percentage-based tax for inclusive pricing
				taxes.append(
					{
						"charge_type": "On Net Total",
						"account_head": get_tax_account_head(tax, charge_type="sales_tax"),
						"description": (
							get_tax_account_description(tax)
							or f"{tax.get('title')} - {tax_rate:.2f}%"
						),
						"rate": tax_rate,
						"included_in_print_rate": 1,
						"cost_center": setting.cost_center,
						"dont_recompute_tax": 0,
					}
				)
			else:
				# Use actual amount for non-inclusive pricing
				taxes.append(
					{
						"charge_type": "Actual",
						"account_head": get_tax_account_head(tax, charge_type="sales_tax"),
						"description": (
							get_tax_account_description(tax)
							or f"{tax.get('title')} - {tax_rate:.2f}%"
						),
						"tax_amount": tax.get("price"),
						"included_in_print_rate": 0,
						"cost_center": setting.cost_center,
						"item_wise_tax_detail": {item_code: [tax_rate, flt(tax.get("price"))]},
						"dont_recompute_tax": 1,
					}
				)

	update_taxes_with_shipping_lines(
		taxes,
		shopify_order.get("shipping_lines"),
		setting,
		items,
		taxes_inclusive=taxes_inclusive,
	)

	if cint(setting.consolidate_taxes):
		taxes = consolidate_order_taxes(taxes, taxes_inclusive)

	for row in taxes:
		tax_detail = row.get("item_wise_tax_detail")
		if isinstance(tax_detail, dict):
			row["item_wise_tax_detail"] = json.dumps(tax_detail)

	return taxes


def consolidate_order_taxes(taxes, taxes_inclusive=False):
	tax_account_wise_data = {}
	
	for tax in taxes:
		account_head = tax["account_head"]
		
		if account_head not in tax_account_wise_data:
			if taxes_inclusive:
				# Use percentage-based for inclusive
				tax_account_wise_data[account_head] = {
					"charge_type": "On Net Total",
					"account_head": account_head,
					"description": tax.get("description"),
					"cost_center": tax.get("cost_center"),
					"included_in_print_rate": 1,
					"dont_recompute_tax": 0,
					"rate": tax.get("rate", 0),
				}
			else:
				# Use actual amount for non-inclusive
				tax_account_wise_data[account_head] = {
					"charge_type": "Actual",
					"account_head": account_head,
					"description": tax.get("description"),
					"cost_center": tax.get("cost_center"),
					"included_in_print_rate": 0,
					"dont_recompute_tax": 1,
					"tax_amount": 0,
					"item_wise_tax_detail": {},
				}
		
		if not taxes_inclusive:
			tax_account_wise_data[account_head]["tax_amount"] += flt(tax.get("tax_amount"))
			if tax.get("item_wise_tax_detail"):
				tax_account_wise_data[account_head]["item_wise_tax_detail"].update(tax["item_wise_tax_detail"])

	return tax_account_wise_data.values()


def get_tax_account_head(tax, charge_type: Literal["shipping", "sales_tax"] | None = None):
	tax_title = str(tax.get("title"))

	tax_account = frappe.db.get_value(
		"Shopify Tax Account",
		{"parent": SETTING_DOCTYPE, "shopify_tax": tax_title},
		"tax_account",
	)

	if not tax_account and charge_type:
		tax_account = frappe.db.get_single_value(SETTING_DOCTYPE, DEFAULT_TAX_FIELDS[charge_type])

	if not tax_account:
		frappe.throw(_("Tax Account not specified for Shopify Tax {0}").format(tax.get("title")))

	return tax_account


def get_tax_account_description(tax):
	tax_title = tax.get("title")

	tax_description = frappe.db.get_value(
		"Shopify Tax Account",
		{"parent": SETTING_DOCTYPE, "shopify_tax": tax_title},
		"tax_description",
	)

	return tax_description


def update_taxes_with_shipping_lines(taxes, shipping_lines, setting, items, taxes_inclusive=False):
	"""Shipping lines represents the shipping details,
	each such shipping detail consists of a list of tax_lines"""
	shipping_as_item = cint(setting.add_shipping_as_item) and setting.shipping_item
	for shipping_charge in shipping_lines:
		if shipping_charge.get("price"):
			shipping_discounts = shipping_charge.get("discount_allocations") or []
			total_discount = sum(flt(discount.get("amount")) for discount in shipping_discounts)

			shipping_taxes = shipping_charge.get("tax_lines") or []
			total_tax = sum(flt(discount.get("price")) for discount in shipping_taxes)

			shipping_charge_amount = flt(shipping_charge["price"]) - flt(total_discount)
			if bool(taxes_inclusive):
				shipping_charge_amount -= total_tax

			if shipping_as_item:
				items.append(
					{
						"item_code": setting.shipping_item,
						"rate": shipping_charge_amount,
						"delivery_date": items[-1]["delivery_date"] if items else nowdate(),
						"qty": 1,
						"stock_uom": "Nos",
						"warehouse": setting.warehouse,
					}
				)
			else:
				taxes.append(
					{
						"charge_type": "Actual",
						"account_head": get_tax_account_head(shipping_charge, charge_type="shipping"),
						"description": get_tax_account_description(shipping_charge)
						or shipping_charge["title"],
						"tax_amount": shipping_charge_amount,
						"cost_center": setting.cost_center,
					}
				)

		for tax in shipping_charge.get("tax_lines"):
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": get_tax_account_head(tax, charge_type="sales_tax"),
					"description": (
						get_tax_account_description(tax)
						or f"{tax.get('title')} - {tax.get('rate') * 100.0:.2f}%"
					),
					"tax_amount": tax["price"],
					"cost_center": setting.cost_center,
					"item_wise_tax_detail": {
						setting.shipping_item: [flt(tax.get("rate")) * 100, flt(tax.get("price"))]
					}
					if shipping_as_item
					else {},
					"dont_recompute_tax": 1,
				}
			)


def get_sales_order(order_id):
	"""Get ERPNext sales order using shopify order id."""
	sales_order = frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: order_id})
	if sales_order:
		return frappe.get_doc("Sales Order", sales_order)


def cancel_order(payload, request_id=None):
	"""Called by order/cancelled event.

	When shopify order is cancelled there could be many different someone handles it.

	Updates document with custom field showing order status.

	IF sales invoice / delivery notes are not generated against an order, then cancel it.
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	order = payload

	try:
		order_id = order["id"]
		order_status = order["financial_status"]

		sales_order = get_sales_order(order_id)

		if not sales_order:
			create_shopify_log(status="Invalid", message="Sales Order does not exist")
			return

		sales_invoice = frappe.db.get_value("Sales Invoice", filters={ORDER_ID_FIELD: order_id})
		delivery_notes = frappe.db.get_list("Delivery Note", filters={ORDER_ID_FIELD: order_id})

		if sales_invoice:
			frappe.db.set_value("Sales Invoice", sales_invoice, ORDER_STATUS_FIELD, order_status)

		for dn in delivery_notes:
			frappe.db.set_value("Delivery Note", dn.name, ORDER_STATUS_FIELD, order_status)

		if not sales_invoice and not delivery_notes and sales_order.docstatus == 1:
			sales_order.cancel()
		else:
			frappe.db.set_value("Sales Order", sales_order.name, ORDER_STATUS_FIELD, order_status)

	except Exception as e:
		create_shopify_log(status="Error", exception=e)
	else:
		create_shopify_log(status="Success")


@temp_shopify_session
def sync_old_orders():
	shopify_setting = frappe.get_cached_doc(SETTING_DOCTYPE)
	if not cint(shopify_setting.sync_old_orders):
		return

	orders = _fetch_old_orders(shopify_setting.old_orders_from, shopify_setting.old_orders_to)

	for order in orders:
		log = create_shopify_log(
			method=EVENT_MAPPER["orders/create"], request_data=json.dumps(order), make_new=True
		)
		sync_sales_order(order, request_id=log.name)

	shopify_setting = frappe.get_doc(SETTING_DOCTYPE)
	shopify_setting.sync_old_orders = 0
	shopify_setting.save()


def _fetch_old_orders(from_time, to_time):
	"""Fetch all shopify orders in specified range and return an iterator on fetched orders."""

	from_time = get_datetime(from_time).astimezone().isoformat()
	to_time = get_datetime(to_time).astimezone().isoformat()
	orders_iterator = PaginatedIterator(
		Order.find(created_at_min=from_time, created_at_max=to_time, limit=250)
	)

	for orders in orders_iterator:
		for order in orders:
			# Using generator instead of fetching all at once is better for
			# avoiding rate limits and reducing resource usage.
			yield order.to_dict()


def get_discount_info(shopify_order: dict) -> dict:
	"""Extract discount codes and percentage/amount from Shopify order.
	
	Hybrid approach:
	- If coupon code exists in ERPNext, use native coupon_code field
	- Otherwise, fallback to discount_amount (actual amount from Shopify)
	
	Returns:
		dict: {
			"codes": "CODE1, CODE2" (comma-separated string for custom field),
			"amount": 44.90 (float, actual discount amount from Shopify),
			"coupon_code": "CODE1" or None (ERPNext native coupon if exists),
			"use_native_coupon": True/False
		}
	"""
	discount_codes = shopify_order.get("discount_codes") or []
	
	# Extract all discount codes (comma-separated)
	code_list = [dc.get("code", "") for dc in discount_codes if dc.get("code")]
	codes = ", ".join(code_list)
	
	# Get the actual discount amount from Shopify (this is the real amount applied)
	# This comes from discount_codes[].amount which is the actual EUR/USD amount
	total_discount_amount = 0.0
	for dc in discount_codes:
		total_discount_amount += flt(dc.get("amount", 0))
	
	# Fallback: Use total_discounts from order level
	if not total_discount_amount:
		total_discount_amount = flt(shopify_order.get("total_discounts", 0))
	
	# Check if any coupon code exists in ERPNext
	erpnext_coupon = None
	for code in code_list:
		if frappe.db.exists("Coupon Code", {"coupon_code": code}):
			erpnext_coupon = code
			break
	
	return {
		"codes": codes,
		"amount": total_discount_amount,
		"coupon_code": erpnext_coupon,
		"use_native_coupon": bool(erpnext_coupon),
	}


def get_sales_partner_from_mapping(shopify_order: dict, setting) -> dict | None:
	"""Find a Sales Partner for the order using the "Shopify Sales Partner Rule" list.

	Rules are evaluated in ``priority`` order (lowest first); the first matching
	rule wins. A rule matches on one attribution signal:

	- Discount Code:     an order discount code equals the value (case-insensitive)
	- UTM Source/Campaign/Medium: the parsed UTM value equals the value
	- Marketing Channel: the derived channel equals the value (e.g. "Meta Ads")
	- Referral URL:      the value appears anywhere in landing/referring URL

	Returns: {"sales_partner": ..., "commission_rate": ...} or None.
	"""
	if not cint(setting.enable_sales_partner_mapping):
		return None

	rules = frappe.get_all(
		"Shopify Sales Partner Rule",
		filters={"enabled": 1},
		fields=["mapping_type", "mapping_value", "sales_partner", "commission_rate"],
		order_by="priority asc, creation asc",
	)
	if not rules:
		return None

	attribution = get_marketing_attribution(shopify_order)
	discount_codes = {
		(dc.get("code") or "").lower() for dc in (shopify_order.get("discount_codes") or [])
	}
	landing = (shopify_order.get("landing_site") or "").lower()
	referring = (shopify_order.get("referring_site") or "").lower()

	# Attribution values are keyed by the Shopify custom fieldnames.
	utm_source = (attribution.get(UTM_SOURCE_FIELD) or "").lower()
	utm_campaign = (attribution.get(UTM_CAMPAIGN_FIELD) or "").lower()
	utm_medium = (attribution.get(UTM_MEDIUM_FIELD) or "").lower()
	channel = (attribution.get(MARKETING_CHANNEL_FIELD) or "").lower()

	for rule in rules:
		value = (rule.mapping_value or "").strip().lower()
		if not value:
			continue

		mtype = rule.mapping_type
		matched = (
			(mtype == "Discount Code" and value in discount_codes)
			or (mtype == "UTM Source" and value == utm_source)
			or (mtype == "UTM Campaign" and value == utm_campaign)
			or (mtype == "UTM Medium" and value == utm_medium)
			or (mtype == "Marketing Channel" and value == channel)
			or (mtype == "Referral URL" and (value in landing or value in referring))
		)
		if matched:
			return {"sales_partner": rule.sales_partner, "commission_rate": rule.commission_rate}

	return None


# --- Marketing attribution ------------------------------------------------

_SEARCH_ENGINE_HOSTS = ("google.", "bing.", "yahoo.", "duckduckgo.", "ecosia.", "yandex.")


def get_marketing_attribution(shopify_order: dict) -> dict:
	"""Extract marketing attribution from a Shopify order for storing on the Sales Order.

	Shopify puts the first-touch URL in ``landing_site`` (a path + query string) and
	the external referrer in ``referring_site``. UTM parameters live in the
	landing_site query string; paid traffic also leaves click ids (``gclid`` /
	``gad_source`` for Google Ads, ``fbclid`` for Meta) that plain UTM parsing
	misses — so we look at those too and derive a single ``marketing_channel``.

	Returns a dict keyed by the Shopify custom fieldnames, ready to splat into the
	Sales Order document.
	"""
	landing = shopify_order.get("landing_site") or ""
	referring = shopify_order.get("referring_site") or ""
	params = parse_qs(urlparse(landing).query)

	def first(key: str) -> str:
		value = params.get(key)
		return value[0].strip() if value and value[0] else ""

	utm_source = first("utm_source")
	utm_medium = first("utm_medium")

	has_google_click = any(k in params for k in ("gclid", "gad_source", "gad_campaignid", "gbraid", "wbraid"))
	has_meta_click = "fbclid" in params

	channel = _classify_marketing_channel(
		utm_source=utm_source,
		utm_medium=utm_medium,
		has_google_click=has_google_click,
		has_meta_click=has_meta_click,
		referrer=referring.lower(),
		has_landing=bool(landing),
	)

	return {
		MARKETING_CHANNEL_FIELD: channel,
		UTM_SOURCE_FIELD: utm_source,
		UTM_MEDIUM_FIELD: utm_medium,
		UTM_CAMPAIGN_FIELD: first("utm_campaign"),
		UTM_CONTENT_FIELD: first("utm_content"),
		UTM_TERM_FIELD: first("utm_term"),
		# Keep the raw URLs (truncated) as an audit trail for anything we didn't parse.
		LANDING_SITE_FIELD: landing[:500],
		REFERRING_SITE_FIELD: referring[:500],
	}


def _classify_marketing_channel(
	utm_source: str,
	utm_medium: str,
	has_google_click: bool,
	has_meta_click: bool,
	referrer: str,
	has_landing: bool,
) -> str:
	"""Collapse the various signals into one human-readable channel label."""
	source = (utm_source or "").lower()

	# Meta (Facebook / Instagram) — utm_source is tagged on ads, or an fbclid is present.
	if has_meta_click or source in ("fb", "facebook", "ig", "instagram", "meta"):
		return "Meta Ads"

	# Google Ads — paid click ids.
	if has_google_click:
		return "Google Ads"

	# Google organic / Shopping free listings / product sync.
	if source == "google" or "google." in referrer:
		return "Google Organic"

	# Other search engines with no click id.
	if any(engine in referrer for engine in _SEARCH_ENGINE_HOSTS):
		return "Organic Search"

	# An explicit UTM source we didn't special-case (e.g. newsletter, an affiliate).
	if source:
		return f"{utm_source} / {utm_medium}" if utm_medium else utm_source

	# A real external referrer but no UTM.
	if referrer and "myshopify" not in referrer:
		return "Referral"

	return "Direct / Unknown"