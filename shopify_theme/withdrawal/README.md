# Cayma düğmesi (Vertrag widerrufen): tema dosyaları

Bu klasör tema tasarımcısı için. Mağazadaki yasal cayma düğmesini (§ 356a BGB, AB'de
Directive 2011/83/EU Madde 11a) Händlerbund uygulamasından alıp kendi formumuza taşıyor.
Form, beyanı doğrudan ERPNext'e gönderiyor. ERPNext beyanı saklıyor, müşteriye hemen
alındı onayı e-postası atıyor ve siparişi bulursa iade vakasını açıyor.

## Dosyalar

| Dosya | Temada nereye | Ne |
|---|---|---|
| `sections/withdrawal-form.liquid` | `sections/` | Form, beş dil (DE, NL, FR, ES, EN), stil ve betik içinde |
| `snippets/withdrawal-button.liquid` | `snippets/` | Her sayfadaki düğme (birinci adım) |
| `templates/page.withdrawal.json` | `templates/` | Formu gösteren sayfa şablonu |

## Kurulum

1. **Dosyaları kopyalayın:** Shopify yönetimi > Online Store > Themes > ... > Edit code.
   Üç dosyayı aynı adla ilgili klasöre ekleyin.
2. **Sayfayı açın:** Online Store > Pages > Add page.
   - Başlık: `Vertrag widerrufen`.
   - Adres (handle): **`vertrag-widerrufen`**. Düğme sayfayı bu adla buluyor.
   - Şablon: `page.withdrawal`. İçerik boş kalabilir.
   - Başlığı öteki dillere Translate & Adapt ile çevirin: NL "Overeenkomst herroepen", FR "Se
     rétracter du contrat", ES "Desistir del contrato", EN "Withdraw from contract".
3. **Form ayarı:** tema düzenleyicide bu sayfayı açın, "Withdrawal form" bölümünde:
   - **ERPNext address:** dolu gelir, değiştirmeyin.
   - **Email for withdrawals when the form cannot be sent:** cayma beyanlarının gidebileceği
     bir e-posta adresi. Yalnız ERPNext'e ulaşılamazsa gösterilir.
4. **Düğme:** altbilgide (footer), her sayfada görünecek yere ekleyin:
   `{% render 'withdrawal-button' %}`
   - Düğme gibi görünmeli. Öteki yasal bağlantılarla aynı küçük metin bağlantısı **yetmez**;
     yasa "belirgin ve kolay erişilebilir" diyor.
   - Telefonda da görünmeli. Bir menünün içine ya da giriş yapılan bir yere saklanmamalı.
5. **Önizlemeyi deneyin.** Tema önizlemesi `...myshopify.com` adresinden açılıyorsa o adresi
   bize bildirin. ERPNext yalnız izin verilen mağaza adreslerinden gelen formu kabul ediyor;
   önizleme adresini sunucuda ekliyoruz.
6. **Canlıya aldıktan sonra Händlerbund düğmesini kaldırın** ya da uygulamada kapatın. Sitede
   tek bir cayma düğmesi olmalı.

## Değiştirmeyin

- **Alan adları** (`full_name`, `order_reference`, `email`, `scope`, `items`, `reason`,
  `website`): ERPNext bu adları bekliyor. `website` görünmez bir tuzak alan; robotlar dolduruyor,
  silmeyin.
- **CAPTCHA eklemeyin, giriş istemeyin, "Sebep" alanını zorunlu yapmayın.** Üçü de yasaya aykırı.
- **Almanca düğme metinleri yasada yazılı:** "Vertrag widerrufen" ve "Widerruf bestätigen".
- İki adım şart: önce altbilgideki düğme, sonra sayfadaki onay düğmesi.

## Dil metinleri

Bütün metinler `withdrawal-form.liquid` içindeki `TEXTS` sözlüğünde. Düğme metinleri ayrıca
`withdrawal-button.liquid`'de. NL, FR ve ES düğme ifadelerini Händlerbund uygulamasının bugün
gösterdiği ifadelerle karşılaştırın. Farklıysa Händlerbund'unkini kullanın: hukuk metni sağlayıcısı
onlar. Değiştirdiğiniz metni bize de bildirin; alındı onayı e-postası aynı dili kullanıyor.

## Stil

Form kendi küçük stilini taşıyor (`.withdrawal` ile başlayan sınıflar). Gönder düğmesi temanın
`button button--primary` sınıfını, altbilgi düğmesi `button button--secondary` sınıfını
kullanıyor. Temaya uymazsa bu sınıfları değiştirebilirsiniz; alan adlarına dokunmayın.

## Teknik

- Form `POST` ile `https://erp.scarnatti.com/api/method/multichannel_core.returns.withdrawal.submit`
  adresine gidiyor. Gövde form-urlencoded, çerez yok.
- Cevap: `{"message": {"ok": true, "reference": "WD-2026-00001", "received_at": "...",
  "found": true}}`. Sipariş bulunamazsa `found` false olur ve `contact_email` gelir; form
  müşteriye o adrese yazmasını söyler. Beyan her durumda alınmış ve onaylanmış olur.
- Aynı internet adresinden saatte en çok 10 beyan kabul ediliyor.
- Sunucuya ulaşılamazsa form yedek e-posta adresini gösteriyor; e-postayla cayma da geçerli.
