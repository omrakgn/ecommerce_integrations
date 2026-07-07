import frappe
from frappe import _
from frappe.utils.nestedset import get_root_of


class EcommerceCustomer:
	def __init__(self, customer_id: str, customer_id_field: str, integration: str):
		self.customer_id = customer_id
		self.customer_id_field = customer_id_field
		self.integration = integration

	def is_synced(self) -> bool:
		"""Check if customer on Ecommerce site is synced with ERPNext"""

		return bool(frappe.db.exists("Customer", {self.customer_id_field: self.customer_id}))

	def get_customer_doc(self):
		"""Get ERPNext customer document."""
		if self.is_synced():
			return frappe.get_last_doc("Customer", {self.customer_id_field: self.customer_id})
		else:
			raise frappe.DoesNotExistError()

	def sync_customer(self, customer_name: str, customer_group: str) -> None:
		"""Create customer in ERPNext if one does not exist already."""
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"name": self.customer_id,
				self.customer_id_field: self.customer_id,
				"customer_name": customer_name,
				"customer_group": customer_group,
				"territory": get_root_of("Territory"),
				"customer_type": _("Individual"),
			}
		)

		customer.flags.ignore_mandatory = True
		customer.insert(ignore_permissions=True)

	def get_customer_address_doc(self, address_type: str):
		try:
			customer = self.get_customer_doc().name
			addresses = frappe.get_all("Address", {"link_name": customer, "address_type": address_type})
			if addresses:
				address = frappe.get_last_doc("Address", {"name": addresses[0].name})
				return address
		except frappe.DoesNotExistError:
			return None

	def create_customer_address(self, address: dict[str, str]) -> None:
		"""Create address from dictionary containing fields used in Address doctype of ERPNext."""

		customer_doc = self.get_customer_doc()
		customer_doc.reload()  # Reload to get latest values

		new_address = frappe.get_doc(
			{
				"doctype": "Address",
				**address,
				"links": [{"link_doctype": "Customer", "link_name": customer_doc.name}],
			}
		)
		new_address.insert(ignore_mandatory=True)
		
		# Set as Primary Address on Customer if it's Billing type and no primary exists.
		# Also flag the Address itself (is_primary_address) — ERPNext's server-side
		# address/contact fetch relies on this flag, not just the Customer field.
		address_type = address.get("address_type", "Billing")
		if address_type == "Billing" and not customer_doc.customer_primary_address:
			new_address.db_set("is_primary_address", 1)
			frappe.db.set_value("Customer", customer_doc.name, "customer_primary_address", new_address.name)

	def create_customer_contact(self, contact: dict[str, str]) -> None:
		"""Create contact from dictionary containing fields used in Address doctype of ERPNext."""

		customer_doc = self.get_customer_doc()
		customer_doc.reload()  # Reload to get latest values (in case address was just set)

		new_contact = frappe.get_doc(
			{
				"doctype": "Contact",
				**contact,
				"links": [{"link_doctype": "Customer", "link_name": customer_doc.name}],
			}
		)
		new_contact.insert(ignore_mandatory=True)
		
		# Set as Primary Contact on Customer if no primary exists. The Contact's
		# is_primary_contact flag is what ERPNext's get_default_contact() looks at
		# when auto-filling contact_person on Sales Orders/Invoices, so set it too —
		# setting only the Customer field leaves the Sales Order contact empty.
		if not customer_doc.customer_primary_contact:
			new_contact.db_set("is_primary_contact", 1)
			frappe.db.set_value("Customer", customer_doc.name, "customer_primary_contact", new_contact.name)
		
		# Set mobile_no and email_id on Customer for template compatibility
		update_fields = {}
		
		# Get email from contact
		if contact.get("email_ids"):
			for email in contact.get("email_ids"):
				if email.get("is_primary"):
					update_fields["email_id"] = email.get("email_id")
					break
		
		# Get phone from contact
		if contact.get("phone_nos"):
			for phone in contact.get("phone_nos"):
				if phone.get("is_primary_phone") or phone.get("is_primary_mobile_no"):
					update_fields["mobile_no"] = phone.get("phone")
					break
		
		if update_fields:
			frappe.db.set_value("Customer", customer_doc.name, update_fields)