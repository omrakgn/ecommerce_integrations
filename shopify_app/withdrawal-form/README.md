# Withdrawal Form: cayma düğmesi Shopify uygulaması olarak

Yasal "Vertrag widerrufen" düğmesi ve formu (§ 356a BGB). Uygulama yalnız temaya blok ekliyor:
mağaza verisine hiçbir yetkisi yok, formun gönderdiği beyanı ERPNext işliyor
(`multichannel_core.returns.withdrawal.submit`, bkz. hub'da `docs/cayma-butonu.md`).

- **Tür:** yalnız uzantıdan oluşan uygulama, tek mağazaya özel dağıtımla kurulur; App Store'da yok.
- **Sahibi:** dev.shopify.com'daki hesap. Client ID `shopify.app.toml`'da.
- **Tema kodu sürümü** (`../../shopify_theme/withdrawal/`) yedek olarak duruyor; ikisinden yalnız biri
  kullanılır.

## Parçalar (`extensions/withdrawal-form/`)

| Parça | Tema düzenleyicide | Ne |
|---|---|---|
| `blocks/withdrawal-form.liquid` | Bir bölüme "Withdrawal form" bloğu | Form (ikinci adım, "Widerruf bestätigen") |
| `blocks/withdrawal-button.liquid` | Altbilgiye "Withdrawal button" bloğu | Düğme (birinci adım), altbilgi uygulama bloğu kabul ediyorsa |
| `blocks/withdrawal-button-embed.liquid` | Theme settings > App embeds > "Withdrawal button (every page)" | Düğme her sayfanın en altında, altbilgi blok kabul etmiyorsa |
| `snippets/withdrawal-link.liquid` | - | İki düğme biçiminin ortak bağlantısı |
| `locales/*.json` | - | Metinler: DE, NL, FR, ES, EN (`en.default.json` varsayılan) |
| `assets/withdrawal.js`, `.css` | - | Formu kuran betik (Shopify sınırı 10 KB; şu an 7 KB) ve stil |

Düğme ile gömme seçeneğinden **yalnız biri** kullanılır; ikisi birden açılırsa düğme iki kez görünür.

## Yayın (bir kez ve her değişiklikte)

Gerekli: Node.js 20+, Shopify CLI (`npm install -g @shopify/cli@latest`).
Etkileşimli bir terminalde (VS Code > Terminal > New Terminal ya da PowerShell):

```bash
cd "C:\Users\omera\Desktop\ErpNext_Bulk_App\Erpnext Ecommerce Integration\ecommerce_integrations\shopify_app\withdrawal-form"
shopify app deploy
```

- İlk seferde tarayıcıda dev.shopify.com girişi açılır; uygulamanın sahibi olan hesapla girin.
- "Release a new version?" sorusuna **Yes**.
- Yayın bitince depoda değişen dosya olursa (CLI uzantıya kimlik yazabilir) commit edin.

Denetim (yayından önce, isteğe bağlı):
`shopify theme check --path extensions/withdrawal-form --config theme-check:theme-app-extension`

## Kurulum (bir kez)

1. dev.shopify.com > uygulama > **Distribution** > **Custom distribution**.
2. Mağazanın `...myshopify.com` adresini girin, **Generate link**.
3. Bağlantıyı mağaza sahibi hesabıyla Shopify'a girmiş tarayıcıda açın, **Install**.

## Tema düzenleyicide (her tema için)

1. Önce temanın kopyasında çalışın: Online Store > Themes > ... > Duplicate.
2. Sayfa: Online Store > Pages > Add page, başlık `Vertrag widerrufen`, adres (handle)
   `vertrag-widerrufen`. Başlığı öteki dillere çevirin (Translate & Adapt).
3. Kopya temada **Customize** > sayfa seçiciden *Vertrag widerrufen* > **Add block** (ya da Add
   section > Apps) > **Withdrawal form**. Bloğun ayarında "Email for withdrawals when the form cannot
   be sent" doldurulur.
4. Düğme:
   - Altbilgide **Add block** listesinde "Withdrawal button" varsa onu ekleyin; yoksa
   - **Theme settings > App embeds > Withdrawal button (every page)**'i açın.
5. Önizlemede deneyin (Yapılacaklar 33'teki liste), sonra kopyayı yayınlayın.
6. Händlerbund düğmesini kapatın; sitede tek düğme kalmalı.

**Tema değişince:** yeni temada 3. ve 4. adım tekrarlanır (tıklama, kod yok). Yeni tema yayınlanmadan
önce düğmeye ve formdan bir deneme beyanına bakılır.

## Değiştirmeyin

- Form alan adları (`full_name`, `order_reference`, `email`, `scope`, `items`, `reason`, `website`):
  ERPNext bunları bekliyor. `website` görünmez tuzak alan.
- CAPTCHA, giriş zorunluluğu, zorunlu "sebep" eklemeyin: yasaya aykırı.
- Almanca düğme metinleri yasada yazılı: "Vertrag widerrufen", "Widerruf bestätigen".
- NL, FR, ES ifadeleri `locales/`'da; Händlerbund'un ifadeleriyle karşılaştırılabilir. Değişirse
  ERPNext'teki alındı onayı metinleri de (`multichannel_core/returns/withdrawal_texts.py`) gözden
  geçirilmeli.
