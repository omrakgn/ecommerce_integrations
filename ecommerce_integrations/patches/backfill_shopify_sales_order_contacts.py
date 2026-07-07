import frappe


def execute():
	"""Fix contacts/addresses so Sales Orders get contact_person, and backfill existing ones.

	The customer sync used to set only the Customer's customer_primary_contact /
	customer_primary_address fields, not the Contact/Address `is_primary_*` flags
	that ERPNext's server-side fetch relies on. As a result Sales Orders had an
	empty Address & Contact tab (contact side). This:
	  1) flags existing primary Contacts/Addresses, and
	  2) backfills contact_person + display fields on existing Shopify Sales Orders.
	"""
	# 1) Flag primary Contacts.
	contacts = frappe.get_all(
		"Customer", filters={"customer_primary_contact": ["is", "set"]}, pluck="customer_primary_contact"
	)
	for contact in set(filter(None, contacts)):
		if frappe.db.exists("Contact", contact) and not frappe.db.get_value(
			"Contact", contact, "is_primary_contact"
		):
			frappe.db.set_value("Contact", contact, "is_primary_contact", 1, update_modified=False)

	# 1b) Flag primary Addresses.
	addresses = frappe.get_all(
		"Customer", filters={"customer_primary_address": ["is", "set"]}, pluck="customer_primary_address"
	)
	for address in set(filter(None, addresses)):
		if frappe.db.exists("Address", address) and not frappe.db.get_value(
			"Address", address, "is_primary_address"
		):
			frappe.db.set_value("Address", address, "is_primary_address", 1, update_modified=False)

	# 2) Backfill contact_person on existing Shopify Sales Orders missing it.
	sales_orders = frappe.get_all(
		"Sales Order",
		filters={"shopify_order_id": ["is", "set"], "contact_person": ["is", "not set"]},
		fields=["name", "customer"],
	)
	for so in sales_orders:
		primary = frappe.db.get_value("Customer", so.customer, "customer_primary_contact")
		if not primary or not frappe.db.exists("Contact", primary):
			continue
		contact = frappe.get_doc("Contact", primary)
		frappe.db.set_value(
			"Sales Order",
			so.name,
			{
				"contact_person": primary,
				"contact_display": " ".join(filter(None, [contact.first_name, contact.last_name])),
				"contact_email": contact.email_id,
				"contact_mobile": contact.mobile_no or contact.phone,
			},
			update_modified=False,
		)

	frappe.db.commit()
