"""The Withdrawal Form app's page inside the Shopify admin.

The withdrawal button is a Shopify app (`shopify_app/withdrawal-form`) without a server of its
own. Its app URL points here: the Shopify admin opens this page in a frame and signs every
opening with the app's client secret. The page lists the withdrawal declarations kept by
multichannel_core (`Withdrawal Declaration`), read only; decisions and corrections stay in
ERPNext.

The page shows names and email addresses, so nothing is listed unless
* the signature (HMAC-SHA256 over the query string) was made with our client secret,
* the signature is fresh (at most 10 minutes away),
* the shop is the one in Shopify Setting.

Settings: Shopify Setting > Withdrawal Form App (Client ID, Client Secret; from the app's
settings in the Shopify developer dashboard). More in the hub: docs/cayma-butonu.md.
"""

import hashlib
import hmac
import json
import time
from html import escape

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from werkzeug.wrappers import Response

from ecommerce_integrations.shopify.constants import ORDER_ID_FIELD, SETTING_DOCTYPE

DECLARATION = "Withdrawal Declaration"
MAX_AGE = 600  # seconds between Shopify's signature and our check
LIMIT = 200
ATTENTION = ("Needs Check", "Not Found")
ADMIN_ORIGIN = "https://admin.shopify.com"
APP_BRIDGE = "https://cdn.shopify.com/shopifycloud/app-bridge.js"
FIELDS = [
	"name",
	"status",
	"received_at",
	"sales_order",
	"return_case",
	"full_name",
	"order_reference",
	"email",
	"scope",
	"withdrawn_items",
]
TONE = {
	"Needs Check": "warning",
	"Not Found": "critical",
	"Matched": "info",
	"Case Opened": "success",
	"Linked to Case": "success",
	"Closed": "neutral",
}


# Checking the opening ------------------------------------------------------------------------------


def signature(params, secret):
	"""Shopify's HMAC over the query string: every parameter but `hmac`, as key=value, sorted,
	joined with "&". Delimiters inside keys and values are escaped as Shopify's own library
	does, so one value cannot pose as two parameters."""
	pairs = []
	for key, value in params.items():
		if key == "hmac":
			continue
		key = str(key).replace("%", "%25").replace("=", "%3D")
		value = str(value).replace("%", "%25")
		pairs.append(f"{key}={value}".replace("&", "%26"))
	message = "&".join(sorted(pairs))
	return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def clean_shop(value):
	"""'https://Shop.myshopify.com/' -> 'shop.myshopify.com'."""
	value = str(value or "").strip().lower()
	for prefix in ("https://", "http://"):
		if value.startswith(prefix):
			value = value[len(prefix) :]
	return value.split("/")[0]


def refusal(params, secret, shop, now):
	"""Why the opening is refused, or None when Shopify signed it for our shop just now."""
	given = str(params.get("hmac") or "").strip().lower()
	if not secret or not given:
		return "unsigned"
	if not hmac.compare_digest(signature(params, secret).encode(), given.encode()):
		return "signature"
	try:
		stamp = int(str(params.get("timestamp")))
	except ValueError:
		return "timestamp"
	if abs(now - stamp) > MAX_AGE:
		return "stale"
	if not shop or clean_shop(params.get("shop")) != shop:
		return "shop"
	return None


# Reading the site ----------------------------------------------------------------------


def settings():
	"""(shop, client_id, secret) from Shopify Setting; empty strings when not set."""
	doc = frappe.get_cached_doc(SETTING_DOCTYPE)
	secret = doc.get_password("withdrawal_app_client_secret", raise_exception=False) or ""
	return (
		clean_shop(doc.get("shopify_url")),
		(doc.get("withdrawal_app_client_id") or "").strip(),
		secret.strip(),
	)


def request_args():
	"""The query string as Shopify sent it. Not frappe.form_dict: that also carries `cmd`."""
	if not getattr(frappe, "request", None):
		return {}
	return frappe.request.args.to_dict(flat=True)


def declarations():
	"""Newest first: the last LIMIT declarations, plus every one still needing attention.
	None when the site has no Withdrawal Declaration (multichannel_core missing)."""
	if not frappe.db.exists("DocType", DECLARATION):
		return None
	found = {}
	for filters in ({}, {"status": ["in", list(ATTENTION)]}):
		for row in frappe.get_all(
			DECLARATION, filters=filters, fields=FIELDS, order_by="received_at desc", limit_page_length=LIMIT
		):
			found[row.name] = row
	rows = sorted(found.values(), key=lambda row: str(row.received_at or ""), reverse=True)

	orders = sorted({row.sales_order for row in rows if row.sales_order})
	ids = {}
	if orders:
		for order in frappe.get_all(
			"Sales Order", filters={"name": ["in", orders]}, fields=["name", ORDER_ID_FIELD]
		):
			ids[order.name] = order.get(ORDER_ID_FIELD)
	for row in rows:
		row.shopify_order_id = ids.get(row.sales_order)
	return rows


def set_language(locale):
	"""The admin user's language from Shopify's `locale`; the page is translated to Turkish only."""
	code = str(locale or "").split("-")[0].lower()
	frappe.local.lang = "tr" if code == "tr" else "en"


# Endpoint -------------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=120, seconds=60 * 60)
def page(**kwargs):
	"""The app URL of the Withdrawal Form app. Shopify opens it inside the admin."""
	params = request_args()
	set_language(params.get("locale"))
	shop, client_id, secret = settings()
	if not (shop and client_id and secret):
		body = notice(
			_("The Withdrawal Form app is not set up in ERPNext yet."),
			_("Shopify Setting > Withdrawal Form App: Client ID and Client Secret."),
			client_id,
		)
		return respond(body, 503, shop)
	reason = refusal(params, secret, shop, int(time.time()))
	if reason:
		frappe.logger("withdrawal_app").info(f"Withdrawal Form app page refused: {reason}")
		body = notice(
			_("This page opens only from the Shopify admin of the connected store."),
			_("Open it from Apps in the Shopify admin."),
			client_id,
		)
		return respond(body, 403, shop)
	return respond(render(declarations(), client_id, frappe.utils.get_url()), 200, shop)


def respond(body, status, shop):
	"""HTML that only the Shopify admin (and the shop's own admin address) may frame."""
	ancestors = ADMIN_ORIGIN + (f" https://{shop}" if shop else "")
	response = Response(body, status=status, content_type="text/html; charset=utf-8")
	response.headers["Content-Security-Policy"] = f"frame-ancestors {ancestors};"
	response.headers["Cache-Control"] = "no-store"
	response.headers["X-Robots-Tag"] = "noindex"
	return response


# Page ----------------------------------------------------------------------------------


def head(client_id, title):
	bridge = ""
	if client_id:
		# App Bridge must be the first script, loaded neither async nor deferred.
		bridge = (
			f'<meta name="shopify-api-key" content="{escape(client_id)}"><script src="{APP_BRIDGE}"></script>'
		)
	return (
		f'<!doctype html><html lang="{escape(frappe.local.lang or "en")}"><head><meta charset="utf-8">'
		'<meta name="viewport" content="width=device-width, initial-scale=1">'
		f"{bridge}<title>{escape(title)}</title><style>{STYLE}</style></head>"
	)


def notice(message, hint, client_id):
	return (
		head(client_id, _("Withdrawals"))
		+ f'<body><main class="page"><div class="card notice"><p><strong>{escape(message)}</strong></p>'
		+ f'<p class="muted">{escape(hint)}</p></div></main></body></html>'
	)


def received(value):
	if not value:
		return ""
	if isinstance(value, str):
		value = frappe.utils.get_datetime(value)
	return value.strftime("%d.%m.%Y %H:%M")


def row_html(row, site):
	desk = f"{site}/app/withdrawal-declaration/{row.name}"
	reference = f'<a href="{escape(desk)}" target="_blank" rel="noopener">{escape(row.name)}</a>'

	order = escape(row.order_reference or "")
	if row.get("shopify_order_id"):
		order = (
			f'<a href="shopify://admin/orders/{escape(str(row.shopify_order_id))}" target="_top">{order}</a>'
		)

	customer = f'{escape(row.full_name or "")}<br><span class="muted">{escape(row.email or "")}</span>'

	if row.scope == "Some Items":
		items = escape(row.withdrawn_items or "")
		scope = f'{escape(_("Some items"))}<br><span class="muted clip" title="{items}">{items}</span>'
	else:
		scope = escape(_("Whole order"))

	status = row.status or ""
	badge = f'<span class="badge {TONE.get(status, "neutral")}">{escape(_(status))}</span>'

	case = ""
	if row.return_case:
		link = f"{site}/app/return-case/{row.return_case}"
		case = f'<a href="{escape(link)}" target="_blank" rel="noopener">{escape(row.return_case)}</a>'

	search = " ".join(
		str(part or "") for part in (row.name, row.order_reference, row.full_name, row.email, row.return_case)
	).lower()
	attention = "1" if status in ATTENTION else "0"
	cells = (
		(_("Received"), escape(received(row.received_at))),
		(_("Reference"), reference),
		(_("Order"), order),
		(_("Customer"), customer),
		(_("Withdrawn"), scope),
		(_("Status"), badge),
		(_("Return Case"), case),
	)
	# One box per cell, so that on a phone the label and the value are two grid items.
	tds = "".join(
		f'<td data-label="{escape(label)}"><div>{value}</div></td>' if value else '<td class="none"></td>'
		for label, value in cells
	)
	return f'<tr data-attention="{attention}" data-search="{escape(search)}">{tds}</tr>'


def render(rows, client_id, site):
	title = _("Withdrawals")
	if rows is None:
		return notice(
			_("Withdrawal declarations are not available on this site."),
			_("The app that keeps them (multichannel_core) is not installed."),
			client_id,
		)
	waiting = sum(1 for row in rows if row.status in ATTENTION)
	start = "attention" if waiting else "all"
	heads = "".join(
		f"<th>{escape(label)}</th>"
		for label in (
			_("Received"),
			_("Reference"),
			_("Order"),
			_("Customer"),
			_("Withdrawn"),
			_("Status"),
			_("Return Case"),
		)
	)
	empty = {
		"attention": _("Nothing needs attention."),
		"all": _("No withdrawal declarations yet."),
		"search": _("No declaration matches the search."),
	}
	body = [
		head(client_id, title),
		f'<body data-start="{start}"><main class="page">',
		f"<header><h1>{escape(title)}</h1>",
		'<p class="muted">'
		+ escape(
			_(
				"Declarations sent with the withdrawal button. Read only: decisions and corrections are made in ERPNext."
			)
		)
		+ "</p></header>",
		'<div class="bar"><div class="tabs" role="group">',
		f'<button type="button" data-filter="attention">{escape(_("Needs attention"))} <span class="count">{waiting}</span></button>',
		f'<button type="button" data-filter="all">{escape(_("All"))} <span class="count">{len(rows)}</span></button>',
		"</div>",
		f'<input type="search" placeholder="{escape(_("Search reference, order, name or email"))}" '
		f'aria-label="{escape(_("Search"))}">',
		"</div>",
		f'<div class="card"><table><thead><tr>{heads}</tr></thead><tbody>',
		"".join(row_html(row, site) for row in rows),
		"</tbody></table>",
		f'<p class="empty" hidden data-texts="{escape(json.dumps(empty))}"></p></div>',
		'<p class="muted foot">'
		+ escape(_("Shows the last {0} declarations and every one that needs attention.").format(LIMIT))
		+ "</p>",
		f"</main><script>{SCRIPT}</script></body></html>",
	]
	return "".join(body)


# Tabs and search work in the page; the server sends every row once.
SCRIPT = """(function () {
  var tabs = [].slice.call(document.querySelectorAll('[data-filter]'));
  var rows = [].slice.call(document.querySelectorAll('tbody tr'));
  var search = document.querySelector('input[type=search]');
  var empty = document.querySelector('.empty');
  var texts = JSON.parse(empty.getAttribute('data-texts'));
  var filter = document.body.getAttribute('data-start');
  function apply() {
    var q = search.value.trim().toLowerCase();
    var shown = 0;
    rows.forEach(function (row) {
      var ok = (filter === 'all' || row.getAttribute('data-attention') === '1') &&
        (!q || row.getAttribute('data-search').indexOf(q) > -1);
      row.hidden = !ok;
      if (ok) shown += 1;
    });
    empty.hidden = shown > 0;
    empty.textContent = q ? texts.search : texts[filter];
    tabs.forEach(function (tab) {
      tab.setAttribute('aria-pressed', String(tab.getAttribute('data-filter') === filter));
    });
  }
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () { filter = tab.getAttribute('data-filter'); apply(); });
  });
  search.addEventListener('input', apply);
  apply();
})();"""

# Close to the Shopify admin's own look, so the page does not feel foreign there.
STYLE = """
:root { color-scheme: light; --bg: #f1f1f1; --card: #fff; --text: #303030; --muted: #616161;
  --line: #e3e3e3; --link: #005bd3; --focus: #005bd3; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 13px/1.45 -apple-system, BlinkMacSystemFont, "San Francisco", "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }
[hidden] { display: none !important; }
.page { max-width: 1200px; margin: 0 auto; padding: 20px 16px 40px; }
h1 { margin: 0 0 4px; font-size: 20px; font-weight: 650; }
header { margin-bottom: 16px; }
.muted { color: var(--muted); }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, button:focus-visible, input:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.bar { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.tabs { display: flex; gap: 4px; }
.tabs button { font: inherit; font-weight: 550; padding: 6px 12px; border: 0; border-radius: 8px;
  background: transparent; color: var(--text); cursor: pointer; }
.tabs button[aria-pressed="true"] { background: #e3e3e3; }
.count { display: inline-block; min-width: 20px; padding: 0 6px; border-radius: 10px; background: #fff;
  font-size: 12px; text-align: center; font-variant-numeric: tabular-nums; }
input[type=search] { flex: 1 1 260px; max-width: 360px; min-height: 32px; padding: 6px 10px; font: inherit;
  border: 1px solid #8a8a8a; border-radius: 8px; background: #fff; color: var(--text); }
.card { background: var(--card); border-radius: 12px; box-shadow: 0 1px 0 rgba(0,0,0,.07), 0 0 0 1px rgba(0,0,0,.06); overflow: hidden; }
.notice { padding: 16px 20px; max-width: 640px; }
.notice p { margin: 0 0 6px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 10px 12px; text-align: left; vertical-align: top; border-bottom: 1px solid var(--line); }
th { font-weight: 550; color: var(--muted); background: #f7f7f7; white-space: nowrap; }
tbody tr:last-child td { border-bottom: 0; }
td:first-child { white-space: nowrap; font-variant-numeric: tabular-nums; }
td > div { min-width: 0; overflow-wrap: anywhere; }
.clip { display: inline-block; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 8px; font-weight: 550; white-space: nowrap; background: #e3e3e3; }
.badge.warning { background: #ffd6a4; color: #5e4200; }
.badge.critical { background: #fedad9; color: #8e1f0b; }
.badge.success { background: #cdfee1; color: #0c5132; }
.badge.info { background: #e0f0ff; color: #00527c; }
.empty { margin: 0; padding: 24px 16px; text-align: center; color: var(--muted); }
.foot { margin-top: 12px; }
@media (max-width: 720px) {
  thead { display: none; }
  table, tbody, tr, td { display: block; width: 100%; }
  tr { padding: 10px 12px; border-bottom: 1px solid var(--line); }
  td { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 8px; padding: 3px 0; border: 0; white-space: normal; }
  td::before { content: attr(data-label); color: var(--muted); font-weight: 550; }
  td.none { display: none; }
  .clip { max-width: 100%; white-space: normal; }
}
"""
