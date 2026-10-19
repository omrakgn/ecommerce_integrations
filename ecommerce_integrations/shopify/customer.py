from typing import Any

import frappe
from frappe import _
from frappe.utils import cstr, validate_phone_number

from ecommerce_integrations.controllers.customer import EcommerceCustomer
from ecommerce_integrations.shopify.constants import (
	ADDRESS_ID_FIELD,
	CUSTOMER_ID_FIELD,
	MODULE_NAME,
	SETTING_DOCTYPE,
)
from ecommerce_integrations.utils.phone import international


class ShopifyCustomer(EcommerceCustomer):
	def __init__(self, customer_id: str):
		self.setting = frappe.get_doc(SETTING_DOCTYPE)
		super().__init__(customer_id, CUSTOMER_ID_FIELD, MODULE_NAME)

	def sync_customer(self, customer: dict[str, Any]) -> None:
		"""Create Customer in ERPNext using shopify's Customer dict."""

		customer_name = cstr(customer.get("first_name")) + " " + cstr(customer.get("last_name"))
		if len(customer_name.strip()) == 0:
			customer_name = customer.get("email")

		customer_group = self.setting.customer_group
		super().sync_customer(customer_name, customer_group)

		billing_address = customer.get("billing_address", {}) or customer.get("default_address")
		shipping_address = customer.get("shipping_address", {})

		if billing_address:
			self.create_customer_address(
				customer_name, billing_address, address_type="Billing", email=customer.get("email")
			)
		if shipping_address:
			self.create_customer_address(
				customer_name, shipping_address, address_type="Shipping", email=customer.get("email")
			)

		self.create_customer_contact(customer)

	def create_customer_address(
		self,
		customer_name,
		shopify_address: dict[str, Any],
		address_type: str = "Billing",
		email: str | None = None,
	) -> None:
		"""Create customer address(es) using Customer dict provided by shopify."""
		address_fields = _map_address_fields(shopify_address, customer_name, address_type, email)
		super().create_customer_address(address_fields)

	def update_existing_addresses(self, customer):
		billing_address = customer.get("billing_address", {}) or customer.get("default_address")
		shipping_address = customer.get("shipping_address", {})

		customer_name = cstr(customer.get("first_name")) + " " + cstr(customer.get("last_name"))
		email = customer.get("email")

		if billing_address:
			self._update_existing_address(customer_name, billing_address, "Billing", email)
		if shipping_address:
			self._update_existing_address(customer_name, shipping_address, "Shipping", email)

	def _update_existing_address(
		self,
		customer_name,
		shopify_address: dict[str, Any],
		address_type: str = "Billing",
		email: str | None = None,
	) -> None:
		old_address = self.get_customer_address_doc(address_type)

		if not old_address:
			self.create_customer_address(customer_name, shopify_address, address_type, email)
		else:
			exclude_in_update = ["address_title", "address_type"]
			new_values = _map_address_fields(shopify_address, customer_name, address_type, email)

			old_address.update({k: v for k, v in new_values.items() if k not in exclude_in_update})
			old_address.flags.ignore_mandatory = True
			old_address.save()

	def create_customer_contact(self, shopify_customer: dict[str, Any]) -> None:
		contact_fields = _map_contact_fields(shopify_customer)
		if contact_fields:
			super().create_customer_contact(contact_fields)

	def ensure_contact(self, shopify_customer: dict[str, Any]) -> None:
		"""
		Give a returning customer the contact it never got.

		The contact used to be made only with the customer, and only when
		Shopify sent an e-mail; a customer who first ordered by phone stayed
		without one for every later order (#1421, 2026-10-16). Only a customer
		with no linked contact at all gets one, so nothing is duplicated.

		A contact that cannot be saved must not cost the order: it is logged
		and the order goes on.
		"""
		if self.has_contact():
			return
		frappe.db.savepoint("shopify_contact")
		try:
			self.create_customer_contact(shopify_customer)
		except Exception:
			frappe.db.rollback(save_point="shopify_contact")
			frappe.log_error(title="Shopify: could not create contact", message=frappe.get_traceback())


def _map_contact_fields(shopify_customer: dict[str, Any]) -> dict[str, Any] | None:
	"""
	Contact fields for a Shopify customer, or None when there is no one to reach.

	A name and one way to reach the customer are enough: e-mail or phone.
	E-mail used to be required, so a customer who checked out with a phone
	number only got no contact (#1421). The order's own e-mail stands in for
	the customer's, and the address's name for a customer record without one;
	ERPNext requires a first name, so a lone last name takes its place.
	"""
	adres = (
		shopify_customer.get("default_address")
		or shopify_customer.get("billing_address")
		or shopify_customer.get("shipping_address")
		or {}
	)
	first = cstr(shopify_customer.get("first_name")).strip()
	last = cstr(shopify_customer.get("last_name")).strip()
	if not (first or last):
		first = cstr(adres.get("first_name")).strip()
		last = cstr(adres.get("last_name")).strip()
	if not first:
		first, last = last, ""
	if not first:
		first = cstr(adres.get("name")).strip()
	if not first:
		return None

	email = cstr(shopify_customer.get("email") or shopify_customer.get("order_email")).strip()
	# Adreste yazıldığı gibi gelen numara ("01627473889") gönderide
	# reddediliyordu; ülkesine göre "+49..." biçimine çevriliyor.
	phone_no = international(
		shopify_customer.get("phone") or shopify_customer.get("order_phone") or adres.get("phone"),
		adres.get("country_code"),
	)
	has_phone = validate_phone_number(phone_no, throw=False)
	if not (email or has_phone):
		return None

	contact_fields = {
		"status": "Passive",
		"first_name": first,
		"last_name": last or None,
		"unsubscribed": not shopify_customer.get("accepts_marketing"),
	}
	if email:
		contact_fields["email_ids"] = [{"email_id": email, "is_primary": True}]
	if has_phone:
		contact_fields["phone_nos"] = [{"phone": phone_no, "is_primary_phone": True}]
	return contact_fields


def _map_address_fields(shopify_address, customer_name, address_type, email):
	"""returns dict with shopify address fields mapped to equivalent ERPNext fields"""
	address_fields = {
		"address_title": customer_name,
		"address_type": address_type,
		ADDRESS_ID_FIELD: shopify_address.get("id"),
		"address_line1": shopify_address.get("address1") or "Address 1",
		"address_line2": shopify_address.get("address2"),
		"city": shopify_address.get("city"),
		"state": shopify_address.get("province"),
		"pincode": shopify_address.get("zip"),
		"country": shopify_address.get("country"),
		"email_id": email,
	}

	phone = international(shopify_address.get("phone"), shopify_address.get("country_code"))
	if validate_phone_number(phone, throw=False):
		address_fields["phone"] = phone

	return address_fields
