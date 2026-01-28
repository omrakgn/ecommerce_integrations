from collections import Counter

import frappe
from frappe.utils import cint, create_batch, flt, now
from pyactiveresource.connection import ResourceNotFound
from shopify.resources import InventoryLevel, Variant

from ecommerce_integrations.controllers.inventory import (
	get_inventory_levels,
	update_inventory_sync_status,
)
from ecommerce_integrations.controllers.scheduling import need_to_run
from ecommerce_integrations.shopify.connection import temp_shopify_session
from ecommerce_integrations.shopify.constants import MODULE_NAME, SETTING_DOCTYPE
from ecommerce_integrations.shopify.utils import create_shopify_log


def update_inventory_on_shopify() -> None:
	"""Upload stock levels from ERPNext to Shopify.

	Called by scheduler on configured interval.
	"""
	setting = frappe.get_doc(SETTING_DOCTYPE)

	if not setting.is_enabled() or not setting.update_erpnext_stock_levels_to_shopify:
		return

	if not need_to_run(SETTING_DOCTYPE, "inventory_sync_frequency", "last_inventory_sync"):
		return

	warehous_map = setting.get_erpnext_to_integration_wh_mapping()
	inventory_levels = get_inventory_levels(tuple(warehous_map.keys()), MODULE_NAME)
	
	# Add Product Bundle inventory levels
	bundle_inventory = get_product_bundle_inventory_levels(tuple(warehous_map.keys()), MODULE_NAME)
	if bundle_inventory:
		inventory_levels.extend(bundle_inventory)

	if inventory_levels:
		upload_inventory_data_to_shopify(inventory_levels, warehous_map)


def get_product_bundle_inventory_levels(warehouses: tuple, integration: str) -> list:
	"""Get inventory levels for Product Bundle items.
	
	Product Bundles have 'Maintain Stock = No', so they don't have Bin entries.
	We calculate their available quantity based on the minimum available
	quantity of their component items.
	
	Bundle Stock = min(component_qty / bundle_qty for each component)
	
	Example:
	- Bundle contains: 1x Mattress, 2x Pillow
	- Mattress stock: 10
	- Pillow stock: 25
	- Bundle stock: min(10/1, 25/2) = min(10, 12.5) = 10
	"""
	# Get all Ecommerce Items that are Product Bundles
	bundle_items = frappe.db.sql("""
		SELECT 
			ei.name as ecom_item,
			ei.erpnext_item_code as item_code,
			ei.integration_item_code,
			ei.variant_id,
			ei.inventory_synced_on
		FROM `tabEcommerce Item` ei
		INNER JOIN `tabItem` i ON ei.erpnext_item_code = i.name
		WHERE ei.integration = %s
		AND i.is_stock_item = 0
		AND EXISTS (
			SELECT 1 FROM `tabProduct Bundle` pb 
			WHERE pb.new_item_code = ei.erpnext_item_code
		)
	""", (integration,), as_dict=True)
	
	if not bundle_items:
		return []
	
	result = []
	
	for bundle in bundle_items:
		# Get Product Bundle components
		components = frappe.db.get_all(
			"Product Bundle Item",
			filters={"parent": bundle.item_code},
			fields=["item_code", "qty"]
		)
		
		if not components:
			continue
		
		# Calculate minimum available quantity across all warehouses
		min_bundle_qty = float('inf')
		bundle_modified = None
		primary_warehouse = None
		
		for warehouse in warehouses:
			warehouse_bundle_qty = float('inf')
			warehouse_modified = None
			
			for comp in components:
				# Get component stock in this warehouse
				bin_data = frappe.db.get_value(
					"Bin",
					{"item_code": comp.item_code, "warehouse": warehouse},
					["actual_qty", "reserved_qty", "modified"],
					as_dict=True
				)
				
				if bin_data:
					available_qty = flt(bin_data.actual_qty) - flt(bin_data.reserved_qty)
					# How many bundles can we make with this component?
					component_bundle_qty = available_qty / flt(comp.qty) if comp.qty else 0
					warehouse_bundle_qty = min(warehouse_bundle_qty, component_bundle_qty)
					
					# Track the latest modification
					if bin_data.modified:
						if not warehouse_modified or bin_data.modified > warehouse_modified:
							warehouse_modified = bin_data.modified
				else:
					# Component not in this warehouse
					warehouse_bundle_qty = 0
					break
			
			if warehouse_bundle_qty != float('inf') and warehouse_bundle_qty >= 0:
				if warehouse_bundle_qty < min_bundle_qty or (warehouse_bundle_qty > 0 and min_bundle_qty == float('inf')):
					min_bundle_qty = warehouse_bundle_qty
					bundle_modified = warehouse_modified
					primary_warehouse = warehouse
		
		# Skip if no valid quantity calculated
		if min_bundle_qty == float('inf'):
			min_bundle_qty = 0
		
		# Check if inventory was modified since last sync
		if bundle_modified and bundle.inventory_synced_on:
			if bundle_modified <= bundle.inventory_synced_on:
				continue  # No changes since last sync
		
		if primary_warehouse:
			result.append(frappe._dict({
				"ecom_item": bundle.ecom_item,
				"item_code": bundle.item_code,
				"integration_item_code": bundle.integration_item_code,
				"variant_id": bundle.variant_id,
				"actual_qty": int(min_bundle_qty),
				"reserved_qty": 0,
				"warehouse": primary_warehouse,
				"is_bundle": True
			}))
	
	return result


@temp_shopify_session
def upload_inventory_data_to_shopify(inventory_levels, warehous_map) -> None:
	synced_on = now()

	for inventory_sync_batch in create_batch(inventory_levels, 50):
		for d in inventory_sync_batch:
			d.shopify_location_id = warehous_map.get(d.warehouse)
			
			if not d.shopify_location_id:
				d.status = "Failed"
				d.failure_reason = f"No Shopify location mapped for warehouse: {d.warehouse}"
				continue

			try:
				variant = Variant.find(d.variant_id)
				inventory_id = variant.inventory_item_id

				InventoryLevel.set(
					location_id=d.shopify_location_id,
					inventory_item_id=inventory_id,
					# shopify doesn't support fractional quantity
					available=cint(d.actual_qty) - cint(d.reserved_qty),
				)
				update_inventory_sync_status(d.ecom_item, time=synced_on)
				d.status = "Success"
			except ResourceNotFound:
				# Variant or location is deleted, mark as last synced and ignore.
				update_inventory_sync_status(d.ecom_item, time=synced_on)
				d.status = "Not Found"
			except Exception as e:
				d.status = "Failed"
				d.failure_reason = str(e)

			frappe.db.commit()

		_log_inventory_update_status(inventory_sync_batch)


def _log_inventory_update_status(inventory_levels) -> None:
	"""Create log of inventory update."""
	log_message = "variant_id,location_id,status,failure_reason,is_bundle\n"

	log_message += "\n".join(
		f"{d.variant_id},{d.shopify_location_id},{d.status},{d.failure_reason or ''},{d.get('is_bundle', False)}"
		for d in inventory_levels
	)

	stats = Counter([d.status for d in inventory_levels])

	percent_successful = stats["Success"] / len(inventory_levels) if inventory_levels else 0

	if percent_successful == 0:
		status = "Failed"
	elif percent_successful < 1:
		status = "Partial Success"
	else:
		status = "Success"

	log_message = f"Updated {percent_successful * 100}% items\n\n" + log_message

	create_shopify_log(method="update_inventory_on_shopify", status=status, message=log_message)