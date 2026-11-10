"""The Withdrawal Form app's page in the Shopify admin. No site needed:

    python -m unittest ecommerce_integrations.shopify.test_withdrawal_app

The signature example is Shopify's own ("Verify a request", secret "hush").
"""

import datetime
import unittest
from unittest import mock

from ecommerce_integrations.shopify import withdrawal_app as wa

_d = wa.frappe._dict

SHOP = "some-shop.myshopify.com"
SECRET = "hush"
CLIENT_ID = "client-123"
STAMP = 1337178173
# Shopify's documented example: this message signed with "hush".
DOC_PARAMS = {
	"code": "0907a61c0c8d55e99db179b68161bc00",
	"shop": SHOP,
	"state": "0.6784241404160823",
	"timestamp": str(STAMP),
	"hmac": "700e2dadb827fcc8609e9d5ce208b2e9cdaab9df07390d2cbca10d7c328fc4bf",
}


MISSING = object()


def signed(**params):
	"""An admin opening as Shopify sends it, signed with SECRET."""
	values = {
		"embedded": "1",
		"host": "YWRtaW4uc2hvcGlmeS5jb20vc3RvcmUvc29tZS1zaG9w",
		"locale": "en",
		"shop": SHOP,
		"timestamp": str(STAMP),
	}
	values.update(params)
	values["hmac"] = wa.signature(values, SECRET)
	return values


def declaration(**values):
	row = _d(
		name="WD-2026-00001",
		status="Case Opened",
		received_at=datetime.datetime(2026, 11, 10, 14, 5),
		sales_order="SO-0001",
		return_case="RC-0001",
		full_name="Anna de Vries",
		order_reference="#1421",
		email="anna@example.com",
		scope="Whole Order",
		withdrawn_items="",
		shopify_order_id="5550001",
	)
	row.update(values)
	return row


class SignatureTests(unittest.TestCase):
	def test_shopify_documented_example(self):
		self.assertEqual(wa.signature(DOC_PARAMS, SECRET), DOC_PARAMS["hmac"])
		self.assertIsNone(wa.refusal(DOC_PARAMS, SECRET, SHOP, STAMP))

	def test_order_of_parameters_does_not_matter(self):
		backwards = dict(reversed(list(DOC_PARAMS.items())))
		self.assertEqual(wa.signature(backwards, SECRET), DOC_PARAMS["hmac"])

	def test_changed_value_is_refused(self):
		params = dict(DOC_PARAMS, state="0.1")
		self.assertEqual(wa.refusal(params, SECRET, SHOP, STAMP), "signature")

	def test_added_parameter_is_refused(self):
		params = dict(DOC_PARAMS, admin="1")
		self.assertEqual(wa.refusal(params, SECRET, SHOP, STAMP), "signature")

	def test_one_value_cannot_pose_as_two_parameters(self):
		self.assertNotEqual(wa.signature({"a": "1&b=2"}, SECRET), wa.signature({"a": "1", "b": "2"}, SECRET))
		self.assertNotEqual(wa.signature({"a=b": "c"}, SECRET), wa.signature({"a": "b=c"}, SECRET))

	def test_wrong_secret_is_refused(self):
		self.assertEqual(wa.refusal(DOC_PARAMS, "other", SHOP, STAMP), "signature")

	def test_unsigned_or_no_secret_is_refused(self):
		params = dict(DOC_PARAMS)
		del params["hmac"]
		self.assertEqual(wa.refusal(params, SECRET, SHOP, STAMP), "unsigned")
		self.assertEqual(wa.refusal(DOC_PARAMS, "", SHOP, STAMP), "unsigned")

	def test_capital_letters_in_the_signature_are_fine(self):
		params = dict(DOC_PARAMS, hmac=DOC_PARAMS["hmac"].upper())
		self.assertIsNone(wa.refusal(params, SECRET, SHOP, STAMP))

	def test_odd_characters_in_the_signature_do_not_crash(self):
		params = dict(DOC_PARAMS, hmac="ünï")
		self.assertEqual(wa.refusal(params, SECRET, SHOP, STAMP), "signature")

	def test_old_or_future_signature_is_refused(self):
		self.assertIsNone(wa.refusal(DOC_PARAMS, SECRET, SHOP, STAMP + wa.MAX_AGE))
		self.assertEqual(wa.refusal(DOC_PARAMS, SECRET, SHOP, STAMP + wa.MAX_AGE + 1), "stale")
		self.assertEqual(wa.refusal(DOC_PARAMS, SECRET, SHOP, STAMP - wa.MAX_AGE - 1), "stale")

	def test_signed_timestamp_that_is_not_a_number(self):
		self.assertEqual(wa.refusal(signed(timestamp="soon"), SECRET, SHOP, STAMP), "timestamp")
		params = signed()
		del params["timestamp"]
		params["hmac"] = wa.signature(params, SECRET)
		self.assertEqual(wa.refusal(params, SECRET, SHOP, STAMP), "timestamp")

	def test_other_shop_is_refused(self):
		self.assertEqual(wa.refusal(signed(shop="other.myshopify.com"), SECRET, SHOP, STAMP), "shop")
		self.assertEqual(wa.refusal(signed(), SECRET, "", STAMP), "shop")

	def test_shop_address_written_loosely(self):
		self.assertEqual(wa.clean_shop("https://Some-Shop.myshopify.com/"), SHOP)
		self.assertEqual(wa.clean_shop(" some-shop.myshopify.com "), SHOP)
		self.assertEqual(wa.clean_shop(None), "")
		self.assertIsNone(wa.refusal(signed(shop="SOME-SHOP.myshopify.com"), SECRET, SHOP, STAMP))


class PageTests(unittest.TestCase):
	def open(self, params, setup=(SHOP, CLIENT_ID, SECRET), rows=MISSING):
		rows = [declaration()] if rows is MISSING else rows
		page = getattr(wa.page, "__wrapped__", wa.page)
		with (
			mock.patch.object(wa, "request_args", return_value=params),
			mock.patch.object(wa, "settings", return_value=setup),
			mock.patch.object(wa, "declarations", return_value=rows) as declarations,
			mock.patch.object(wa.time, "time", return_value=STAMP + 5),
			mock.patch.object(wa.frappe.utils, "get_url", return_value="https://erp.example.test"),
		):
			response = page()
		return response, response.get_data(as_text=True), declarations

	def test_good_opening_shows_the_declarations(self):
		response, body, _ = self.open(signed())
		self.assertEqual(response.status_code, 200)
		self.assertIn("Anna de Vries", body)
		self.assertIn("anna@example.com", body)
		self.assertIn('href="shopify://admin/orders/5550001" target="_top"', body)
		self.assertIn("https://erp.example.test/app/withdrawal-declaration/WD-2026-00001", body)
		self.assertIn("https://erp.example.test/app/return-case/RC-0001", body)
		self.assertIn(f'<meta name="shopify-api-key" content="{CLIENT_ID}">', body)
		self.assertIn(wa.APP_BRIDGE, body)
		self.assertLess(body.index(wa.APP_BRIDGE), body.index("<style>"), "App Bridge comes first")

	def test_only_the_shopify_admin_may_frame_it(self):
		for params in (signed(), signed(shop="other.myshopify.com")):
			response, _, _ = self.open(params)
			policy = response.headers["Content-Security-Policy"]
			self.assertEqual(policy, f"frame-ancestors https://admin.shopify.com https://{SHOP};")
			self.assertEqual(response.headers["Cache-Control"], "no-store")

	def test_refused_opening_shows_no_data(self):
		response, body, declarations = self.open(signed(shop="other.myshopify.com"))
		self.assertEqual(response.status_code, 403)
		declarations.assert_not_called()
		self.assertNotIn("Anna", body)
		self.assertIn("opens only from the Shopify admin", body)

	def test_old_link_shows_no_data(self):
		params = signed(timestamp=str(STAMP - wa.MAX_AGE - 60))
		response, _, declarations = self.open(params)
		self.assertEqual(response.status_code, 403)
		declarations.assert_not_called()

	def test_without_settings_nothing_is_checked_or_shown(self):
		for setup in (("", CLIENT_ID, SECRET), (SHOP, "", SECRET), (SHOP, CLIENT_ID, "")):
			response, body, declarations = self.open(signed(), setup=setup)
			self.assertEqual(response.status_code, 503)
			declarations.assert_not_called()
			self.assertIn("not set up in ERPNext", body)

	def test_names_cannot_inject_markup(self):
		row = declaration(
			full_name='<script>alert("x")</script>',
			withdrawn_items='"><img src=x>',
			scope="Some Items",
			order_reference="<b>#1</b>",
		)
		_, body, _ = self.open(signed(), rows=[row])
		self.assertNotIn("<script>alert", body)
		self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", body)
		self.assertNotIn('"><img', body)
		self.assertNotIn("<b>#1</b>", body)

	def test_language_follows_the_admin_user(self):
		self.open(signed(locale="tr-TR"))
		self.assertEqual(wa.frappe.local.lang, "tr")
		self.open(signed(locale="de"))
		self.assertEqual(wa.frappe.local.lang, "en", "only Turkish is translated")

	def test_site_without_withdrawal_declarations(self):
		response, body, _ = self.open(signed(), rows=None)
		self.assertEqual(response.status_code, 200)
		self.assertIn("multichannel_core", body)
		self.assertNotIn("<table>", body)


class RenderTests(unittest.TestCase):
	def render(self, rows):
		return wa.render(rows, CLIENT_ID, "https://erp.example.test")

	def test_starts_on_needs_attention_when_something_waits(self):
		body = self.render([declaration(), declaration(name="WD-2026-00002", status="Not Found")])
		self.assertIn('data-start="attention"', body)
		self.assertIn('Needs attention <span class="count">1</span>', body)
		self.assertIn('All <span class="count">2</span>', body)
		self.assertEqual(body.count('data-attention="1"'), 1)

	def test_starts_on_all_when_nothing_waits(self):
		body = self.render([declaration()])
		self.assertIn('data-start="all"', body)

	def test_attention_means_needs_check_and_not_found(self):
		for status, flag in (("Needs Check", "1"), ("Not Found", "1"), ("Closed", "0"), ("Case Opened", "0")):
			self.assertIn(f'data-attention="{flag}"', wa.row_html(declaration(status=status), "https://e"))

	def test_order_without_shopify_id_is_plain_text(self):
		row = wa.row_html(declaration(shopify_order_id=None, sales_order=None), "https://e")
		self.assertNotIn("shopify://", row)
		self.assertIn("#1421", row)

	def test_some_items_shows_what(self):
		row = wa.row_html(declaration(scope="Some Items", withdrawn_items="2x Pillow"), "https://e")
		self.assertIn("Some items", row)
		self.assertIn("2x Pillow", row)
		self.assertIn("Whole order", wa.row_html(declaration(), "https://e"))

	def test_empty_cell_is_marked_for_phones(self):
		row = wa.row_html(declaration(return_case=None), "https://e")
		self.assertIn('<td class="none"></td>', row)
		self.assertNotIn("return-case/", row)

	def test_search_text_covers_reference_order_name_email(self):
		row = wa.row_html(declaration(), "https://e")
		self.assertIn('data-search="wd-2026-00001 #1421 anna de vries anna@example.com rc-0001"', row)

	def test_received_time(self):
		self.assertEqual(wa.received(datetime.datetime(2026, 11, 10, 9, 5)), "10.11.2026 09:05")
		self.assertEqual(wa.received("2026-11-10 09:05:00"), "10.11.2026 09:05")
		self.assertEqual(wa.received(None), "")


class DeclarationTests(unittest.TestCase):
	def test_newest_and_every_waiting_one_once_with_shopify_ids(self):
		newest = [
			declaration(name="WD-3", received_at=datetime.datetime(2026, 11, 10, 12), sales_order="SO-3"),
			declaration(
				name="WD-2",
				status="Needs Check",
				received_at=datetime.datetime(2026, 11, 9),
				sales_order=None,
			),
		]
		waiting = [
			declaration(
				name="WD-2",
				status="Needs Check",
				received_at=datetime.datetime(2026, 11, 9),
				sales_order=None,
			),
			declaration(
				name="WD-1", status="Not Found", received_at=datetime.datetime(2026, 1, 1), sales_order=None
			),
		]
		calls = []

		def get_all(doctype, filters=None, fields=None, **kwargs):
			calls.append((doctype, filters, kwargs))
			if doctype == wa.DECLARATION:
				return [_d(r) for r in (waiting if filters else newest)]
			return [_d(name="SO-3", shopify_order_id="777")]

		with (
			mock.patch.object(wa.frappe, "get_all", side_effect=get_all),
			mock.patch.object(wa.frappe.db, "exists", return_value=True),
		):
			rows = wa.declarations()
		self.assertEqual([r.name for r in rows], ["WD-3", "WD-2", "WD-1"])
		self.assertEqual(rows[0].shopify_order_id, "777")
		self.assertIsNone(rows[1].shopify_order_id)
		self.assertEqual(calls[0][2]["limit_page_length"], wa.LIMIT)
		self.assertEqual(calls[1][1], {"status": ["in", ["Needs Check", "Not Found"]]})
		self.assertEqual(calls[2][1], {"name": ["in", ["SO-3"]]})

	def test_site_without_the_doctype(self):
		with mock.patch.object(wa.frappe.db, "exists", return_value=False):
			self.assertIsNone(wa.declarations())


if __name__ == "__main__":
	unittest.main()
