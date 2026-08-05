# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE


MODULE_NAME = "shopify"
SETTING_DOCTYPE = "Shopify Setting"
OLD_SETTINGS_DOCTYPE = "Shopify Settings"

API_VERSION = "2024-01"

WEBHOOK_EVENTS = [
	"orders/create",
	"orders/paid",
	"orders/fulfilled",
	"orders/cancelled",
	"orders/partially_fulfilled",
	"refunds/create",
]

EVENT_MAPPER = {
	"orders/create": "ecommerce_integrations.shopify.order.sync_sales_order",
	"orders/paid": "ecommerce_integrations.shopify.invoice.prepare_sales_invoice",
	"orders/fulfilled": "ecommerce_integrations.shopify.fulfillment.prepare_delivery_note",
	"orders/cancelled": "ecommerce_integrations.shopify.order.cancel_order",
	"refunds/create": "ecommerce_integrations.shopify.refund.prepare_credit_note",
	"orders/partially_fulfilled": "ecommerce_integrations.shopify.fulfillment.prepare_delivery_note",
}

SHOPIFY_VARIANTS_ATTR_LIST = ["option1", "option2", "option3"]

# custom fields

CUSTOMER_ID_FIELD = "shopify_customer_id"
ORDER_ID_FIELD = "shopify_order_id"
ORDER_NUMBER_FIELD = "shopify_order_number"
ORDER_STATUS_FIELD = "shopify_order_status"
REFUND_ID_FIELD = "shopify_refund_id"
FULLFILLMENT_ID_FIELD = "shopify_fulfillment_id"
SUPPLIER_ID_FIELD = "shopify_supplier_id"
ADDRESS_ID_FIELD = "shopify_address_id"
ORDER_ITEM_DISCOUNT_FIELD = "shopify_item_discount"
ITEM_SELLING_RATE_FIELD = "shopify_selling_rate"
DISCOUNT_CODES_FIELD = "shopify_discount_codes"
PAYMENT_METHOD_FIELD = "shopify_payment_method"

# Deprecated structural fields from earlier attempts — deleted by patch, never recreated.
SHOPIFY_TAB_FIELD = "shopify_tab"

# Generic "Marketplace" tab on Sales Order that holds attribution for every channel
# (Shopify now; Amazon / Bol can add their own sections under it later).
MARKETPLACE_TAB_FIELD = "marketplace_tab"
MARKETPLACE_SHOPIFY_SECTION_FIELD = "marketplace_shopify_section"

# marketing attribution (parsed from the Shopify order's landing_site / referring_site)
MARKETING_SECTION_FIELD = "shopify_marketing_section"
MARKETING_CHANNEL_FIELD = "shopify_marketing_channel"
UTM_SOURCE_FIELD = "shopify_utm_source"
UTM_MEDIUM_FIELD = "shopify_utm_medium"
UTM_CAMPAIGN_FIELD = "shopify_utm_campaign"
UTM_CONTENT_FIELD = "shopify_utm_content"
UTM_TERM_FIELD = "shopify_utm_term"
LANDING_SITE_FIELD = "shopify_landing_site"
REFERRING_SITE_FIELD = "shopify_referring_site"

# ERPNext already defines the default UOMs from Shopify but names are different
WEIGHT_TO_ERPNEXT_UOM_MAP = {"kg": "Kg", "g": "Gram", "oz": "Ounce", "lb": "Pound"}
