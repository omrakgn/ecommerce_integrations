// Yeniden gönderimde Shopify'daki takip numarasını güncelle.
//
// Paket dönüp yenisi çıktığında müşteri hâlâ eski numaraya bakıyor — ve o
// numaranın son hareketi "gönderene iade edildi". Shopify o satırları karşılanmış
// saydığı için yeni bir fulfillment açılamıyor; tek yol duran fulfillment'ın
// takip bilgisini yeniden yazmak.
//
// Neden düğme, neden otomatik değil: "gönderilecek satır kalmamış" koşulu tek
// başına, YERİNE GEÇEN koli ile EK koliyi ayırt edemiyor. SendCloud'un zaten
// karşıladığı bir sipariş de aynı görünüyor, ve orada yeniden yazmak müşterinin
// hâlâ beklediği bir kolinin takibini elinden almak olurdu. Hangisi olduğunu
// yalnız yeniden gönderimi yapan kişi biliyor.
//
// Belgesi: docs/plans/basarisiz-teslimat.md §7.1

frappe.ui.form.on("Shipment", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.awb_number) {
			return;
		}

		frm.add_custom_button(
			__("Update Shopify Tracking"),
			() => confirm_retrack(frm),
			__("Reshipment")
		);
	},
});

function confirm_retrack(frm) {
	frappe.confirm(
		__("This replaces the tracking on the fulfillment Shopify already holds, so the customer follows this parcel instead of the one that came back.") +
			"<br><br>" +
			__("Only do this when this parcel replaces one already reported. If it is an additional parcel for the same order, it would take the tracking away from a parcel the customer is still waiting for."),
		() => {
			frappe.call({
				method: "ecommerce_integrations.shopify.tracking.retrack_shipment",
				args: { shipment: frm.doc.name },
				freeze: true,
				freeze_message: __("Telling Shopify…"),
				callback(response) {
					const result = response.message || {};
					if (result.skipped) {
						frappe.msgprint(__("Nothing sent: {0}.", [result.skipped]));
						return;
					}

					// Boş sonuç sessiz kalmamalı: "yaptım" ile "yapacak bir şey
					// bulamadım" ekranda aynı görünürse, ikincisi fark edilmez.
					const notes = [];
					let count = 0;
					for (const row of result.retracked || []) {
						count += (row.retracked || []).length;
						for (const note of row.unsent || []) {
							notes.push(`${row.delivery_note}: ${note}`);
						}
					}

					if (!count) {
						frappe.msgprint({
							title: __("Nothing rewritten"),
							indicator: "orange",
							message:
								__("Shopify already carries this parcel's tracking, or the numbers could not be matched to the fulfillments.") +
								(notes.length ? `<br><br>${frappe.utils.escape_html(notes.join("; "))}` : ""),
						});
						return;
					}

					frappe.msgprint({
						title: __("Shopify updated"),
						indicator: "green",
						message:
							__("Tracking rewritten on {0} fulfillment(s).", [count]) +
							(notes.length ? `<br><br>${frappe.utils.escape_html(notes.join("; "))}` : ""),
					});
				},
			});
		}
	);
}
