<div align="center">
    <img src="https://frappecloud.com/files/ERPNext%20-%20Ecommerce%20Integrations.png" height="128">
    <h2>Ecommerce Integrations for ERPNext</h2>

[![CI](https://github.com/frappe/ecommerce_integrations/actions/workflows/ci.yml/badge.svg)](https://github.com/frappe/ecommerce_integrations/actions/workflows/ci.yml)
  
</div>

### Currently supported integrations:

- Shopify - [User documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/shopify_integration)
- Unicommerce - [User Documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/unicommerce_integration)
- Zenoti - [User documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/zenoti_integration)
- Amazon - [User documentation](https://docs.erpnext.com/docs/v13/user/manual/en/erpnext_integration/amazon_integration)

---

## Shopify Integration - Custom Features

This fork includes additional features for Shopify integration:

### 1. Discount Code & Coupon Support

Shopify discount codes are automatically synced to ERPNext Sales Orders.

**How it works:**

| Shopify | ERPNext |
|---------|---------|
| Discount Code (e.g., EXTRA5) | `shopify_discount_codes` field |
| Discount Percentage (e.g., 5%) | `additional_discount_percentage` |
| Discount visible on invoice | ✅ Yes |

**Hybrid Coupon Code Approach:**
- If the discount code exists in ERPNext as a Coupon Code → Uses native `coupon_code` field (Pricing Rule applies)
- If not → Falls back to `additional_discount_percentage`

### 2. Sales Partner Mapping (Optional)

Automatically assign Sales Partners based on discount codes or referral links.

**Setup:** Shopify Setting → Sales Partner Mapping

| Mapping Type | Mapping Value | Sales Partner | Commission Rate |
|--------------|---------------|---------------|-----------------|
| Discount Code | PARTNER10 | Partner A | 10% |
| Referral/UTM | ref=influencer1 | Influencer X | 15% |
| Referral/UTM | utm_source=facebook | Facebook Ads | 5% |

**Priority:**
1. First checks `discount_codes`
2. Then checks `landing_site` (URL parameters)
3. Then checks `referring_site`

> **Note:** Mapping is optional. General campaigns (without partner tracking) work automatically without any mapping configuration.

### 3. Payment Gateway Integration

Shopify payment methods are mapped to ERPNext's native Mode of Payment system.

**Setup:** Shopify Setting → Payment Gateway Mapping

| Shopify Payment Gateway | Mode of Payment |
|-------------------------|-----------------|
| Klarna | Klarna |
| shopify_payments | Credit Card |
| paypal | PayPal |

**Flow:**
```
Shopify Order (paid)
    │
    ▼
Sales Invoice (created)
    │
    ▼
Payment Entry (created with Mode of Payment)
```

> **Note:** If no mapping is found, the system tries to find a Mode of Payment with a similar name. Create Mode of Payment records in ERPNext first (Accounts → Mode of Payment).

### 4. Additional Order Fields

| Shopify Field | ERPNext Field | Description |
|---------------|---------------|-------------|
| Order Number (#1127) | `po_no` | Customer's Purchase Order |
| Order Date | `po_date` | Customer's Purchase Order Date |
| Payment Gateway | `shopify_payment_method` | Info field (also in Payment Entry) |
| - | `cost_center` | From Shopify Settings |

### 5. Cost Center Support

Cost Center is now properly applied to:
- Sales Order (header level)
- Sales Order Items (line level)
- Tax lines

---

### Installation

- Frappe Cloud Users can install [from Marketplace](https://frappecloud.com/marketplace/apps/ecommerce_integrations).
- Self Hosted users can install using Bench:

```bash
# Production installation
$ bench get-app ecommerce_integrations --branch main

# OR development install
$ bench get-app ecommerce_integrations  --branch develop

# install on site
$ bench --site sitename install-app ecommerce_integrations
```

**For this fork:**
```bash
$ bench get-app https://github.com/omrakgn/ecommerce_integrations.git --branch develop
$ bench --site sitename install-app ecommerce_integrations
```

After installation follow user documentation for each integration to set it up.

### Contributing

- Follow general [ERPNext contribution guideline](https://github.com/frappe/erpnext/wiki/Contribution-Guidelines)
- Send PRs to `develop` branch only.

### Development setup

- Enable developer mode.
- If you want to use a tunnel for local development. Set `localtunnel_url` parameter in your site_config file with ngrok / localtunnel URL. This will be used in most places to register webhooks. Likewise, use this parameter wherever you're sending current site URL to integrations in development mode.


#### License

GNU GPL v3.0
