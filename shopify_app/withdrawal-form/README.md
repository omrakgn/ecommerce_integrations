# Withdrawal Form: cayma düğmesi Shopify uygulaması olarak

Yasal "Vertrag widerrufen" düğmesi ve formu (§ 356a BGB). Uygulama yalnız temaya blok ekliyor:
mağaza verisine hiçbir yetkisi yok, formun gönderdiği beyanı ERPNext işliyor
(`multichannel_core.returns.withdrawal.submit`, bkz. hub'da `docs/cayma-butonu.md`).

- **Tür:** yalnız uzantıdan oluşan uygulama, tek mağazaya özel dağıtımla kurulur; App Store'da yok.
- **Sahibi:** dev.shopify.com'daki hesap. Client ID `shopify.app.toml`'da.
- **Tema kodu sürümü** (`../../shopify_theme/withdrawal/`) yedek olarak duruyor; ikisinden yalnız biri
  kullanılır. Yedek eski düzende (düğme bir sayfaya gider, form o sayfada); bilerek güncellenmedi.

## Nasıl çalışıyor (2026-11-10'dan beri)

1. Müşteri hangi sayfadaysa **"Vertrag widerrufen"**'e basar. Sayfa değişmez, form üstte bir
   pencerede açılır (telefonda tam ekran).
2. Bilgilerini girer, **"Widerruf bestätigen"** (yasanın istediği ikinci adım).
3. Sonuç aynı pencerede: alındı saati, WD- numarası, sipariş bulunamadıysa uyarı ve yazılacak adres,
   **Schließen** düğmesi. Alındı onayı e-postası ERPNext'ten gider. Ayrı bir sayfa gerekmez.

**Doğrudan bağlantı:** sayfadaki herhangi bir `#vertrag-widerrufen` bağlantısı da pencereyi açar
(örneğin `https://scarnatti.com/#vertrag-widerrufen` cayma bilgilendirmesinde ya da e-postada; açılan
sayfada pencere kendiliğinden gelir). Bunun için düğmenin o temada açık olması yeterli.

Pencere: Esc, × ve karartılmış alana tıklama kapatır; kapanınca odak düğmeye döner, sayfa kaymaz.
Yazılanlar kapatınca kalır, gönderilmiş bir beyandan sonra form boşalır.

## Parçalar (`extensions/withdrawal-form/`)

| Parça | Tema düzenleyicide | Ne |
|---|---|---|
| `blocks/withdrawal-button.liquid` | Altbilgiye "Withdrawal button" bloğu | Düğme ve pencere, altbilgi uygulama bloğu kabul ediyorsa |
| `blocks/withdrawal-button-embed.liquid` | App embeds > "Button on every page" | Düğme ve pencere her sayfanın en altında |
| `snippets/withdrawal-dialog.liquid` | - | Düğme, pencere ve form; iki bloğun ortak parçası |
| `locales/*.json` | - | Metinler: DE, NL, FR, ES, EN (`en.default.json` varsayılan) |
| `assets/withdrawal.js`, `.css` | - | Pencereyi açan ve formu gönderen betik (Shopify sınırı 10 KB; şu an 7,8 KB) ve stil |

Blok ile gömme seçeneğinden **yalnız biri** kullanılır; ikisi birden açılırsa düğme iki kez görünür.
Sayfaya konan eski "Withdrawal form" bloğu 2026-11-10'da kaldırıldı (gereği kalmadı).

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

**Uygulama uzantı yayınlanmadan kurulduysa yeniden kurun.** 2026-11-10'da uygulama, dev.shopify.com'da
oluşturulurken açılan uzantısız 1. sürümle kuruldu. `withdrawal-form-2` "New version released to
users" ile etkin olduğu halde tema düzenleyicide App embeds'te ve Add block'ta görünmedi.
Kaldırıp (Settings > Apps and sales channels > Withdrawal Form > Uninstall) aynı bağlantıyla
yeniden kurunca geldi. Yeni kurulum yalnız bugünkü sürümün izinleriyle yapılır (hiç izin yok);
ilk kurulumun müşteri verisi izinleri de böylece düştü.

**IP allowlist kurulmadı (bilerek).** Shopify yönetiminde "Enforced IP restrictions" satırı
görülebilir: uygulamanın Admin API erişimini belirli sunuculara sınırlayan ayar. Yalnız App
Store'daki uygulamalara zorunlu; bu uygulama Shopify API'sine hiç bağlanmıyor, sınırlanacak bir
erişimi yok.

## Tema düzenleyicide (her tema için)

Uygulamayı Shopify yönetiminde açınca "Find this app in the pages where you work" yazar: normal.
Uygulamanın kendi ekranı yok, her şey tema düzenleyicide.

1. Önce temanın kopyasında çalışın: Online Store > Themes > ... > Duplicate.
2. Kopya temada **Edit theme** (eski adıyla Customize; **…** menüsündeki "Edit code" değil).
3. Düğme, ikisinden biri:
   - **App embeds > Withdrawal Form > Button on every page**'i açın (her sayfanın en altında). App
     embeds bu mağazanın düzenleyicisinde **Theme settings (dişli) içinde en üstte**; bazı
     düzenleyicilerde solda ayrı bir simge.
   - Ya da altbilgide **Add block** listesinde "Withdrawal button" varsa onu ekleyin.
4. Ayarlarda **"Email for withdrawals when the form cannot be sent"** doldurulur. ERPNext'e
   ulaşılamazsa ya da tarayıcıda JavaScript kapalıysa müşteriye bu adres gösterilir. Boş kalırsa
   müşteri yalnız "yeniden deneyin" uyarısı görür.
5. **Save**, önizlemede deneyin (Yapılacaklar 33'teki liste), sonra kopyayı yayınlayın.
6. Händlerbund düğmesini kapatın; sitede tek düğme kalmalı.

Eski düzenden kalan "Vertrag widerrufen" sayfası artık gerekmiyor; silinebilir ya da içine yalnız
`#vertrag-widerrufen` bağlantısı konabilir.

**Tema değişince:** yeni temada 3. ve 4. adım tekrarlanır (tıklama, kod yok). Yeni tema yayınlanmadan
önce düğmeye ve formdan bir deneme beyanına bakılır.

## Görünüm (tasarımcı için)

**Kodsuz, bloğun ayarlarından:**

| Bölüm | Ayar | Boşsa |
|---|---|---|
| Button text | Her dil için düğmenin yazısı (German, Dutch, French, Spanish, English) | `locales/`'daki standart metin |
| Button | Style: Outline ya da Filled; Color; Text color (Filled için); Corners; Alignment | Çerçeveli, çevredeki yazı renginde; köşe 4px; embed'de ortada, blokta solda |
| Form window | Confirm button ve yazısı; Background; Text; Corners | Temanın ana düğme rengi (`--btn-1-*`), beyaz zemin, koyu gri yazı, köşe 8px |

**Düğme yazısı yasal sınır:** Almanya'da düğmede "Vertrag widerrufen" ya da aynı derecede açık bir
ifade olmalı (§ 356a BGB). "Hier widerrufen" gibi belirsiz ya da reklam içeren yazı olmaz. Öteki
dillerde de anlam aynı kalmalı. Pencerenin başlığı ve onay düğmesi ("Widerruf bestätigen")
ayardan değişmez.

**Kodla, temanın kendi CSS'inden** (tema ayarlarındaki Custom CSS ya da temanın stil dosyası).
Ayarların yazdığı değişkenler aynı adla CSS'ten de verilebilir:

| Değişken | Ne |
|---|---|
| `--wd-button`, `--wd-button-text`, `--wd-button-radius`, `--wd-align` | Düğme |
| `--wd-surface`, `--wd-text`, `--wd-radius`, `--wd-font`, `--wd-heading-font` | Pencere |
| `--wd-accent`, `--wd-accent-text`, `--wd-control-radius` | Onay düğmesi, alanlar |

Sınıflar sabit: `.withdrawal-bar` / `.withdrawal-button-wrap` (düğmenin kabı), `.withdrawal-button`,
`.withdrawal-dialog`, `.withdrawal-title`, `.withdrawal-intro`, `.withdrawal-field`, `.withdrawal-hint`,
`.withdrawal-submit`, `.withdrawal-status`, `.withdrawal-done`, `.withdrawal-note`, `.withdrawal-close`.

```css
/* örnek: köşesiz pencere ve alanlar, daha büyük başlık */
.withdrawal-dialog { --wd-radius: 0; --wd-control-radius: 0; }
.withdrawal-title { font-size: 2rem; }
```

Ayarlar düğmenin kabına (`.withdrawal-bar` / `.withdrawal-button-wrap`) satır içi yazılıyor. CSS
değişkeni `.withdrawal-dialog` ya da `.withdrawal-button` üzerinde verilirse ayarı ezer; `:root` gibi
daha üstteki bir öğede verilirse ayar kazanır. Karışıklık olmasın diye CSS'le verilen değişkenin
ayarı boş bırakılır.

**Neden pencere temanın renk değişkenlerine bağlı değil:** temalar renkleri farklı biçimlerde yazıyor
(onaltılık, `18,18,18` gibi üçlü, `oklch`). Yanlış tahmin edilen bir biçim arka planı saydam
bırakıyor. 2026-11-10'da eski CSS'te bu oldu: `rgb(var(--color-background))` Ella temasında
(`--color-background: #ffffff`) geçersiz kaldı ve "Widerruf bestätigen" düğmesinin yazısı koyu
zeminde koyu göründü. Bu yüzden pencere varsayılan olarak beyaz; temanın düğme renkleri yalnız
`--btn-1-*` adlarıyla (Ella ve benzerleri, onaltılık) okunuyor.

## Değiştirmeyin

- Form alan adları (`full_name`, `order_reference`, `email`, `scope`, `items`, `reason`, `website`):
  ERPNext bunları bekliyor. `website` görünmez tuzak alan.
- CAPTCHA, giriş zorunluluğu, zorunlu "sebep" eklemeyin: yasaya aykırı.
- Almanca metinler yasada yazılı: "Vertrag widerrufen" (düğme, ayardan değişirse aynı açıklıkta),
  "Widerruf bestätigen" (onay).
- NL, FR, ES ifadeleri `locales/`'da; Händlerbund'un ifadeleriyle karşılaştırılabilir. Değişirse
  ERPNext'teki alındı onayı metinleri de (`multichannel_core/returns/withdrawal_texts.py`) gözden
  geçirilmeli.

## Denendi (2026-11-10)

Snippet Almanca metinlerle düz HTML'e çevrilip, sert tema kuralları (büyük harfli düğme ve etiket,
30px alan yüksekliği) ve sahte ERPNext cevabıyla Chromium'da denendi, 1280 ve 390 piksel genişlikte:
açılma ve odak, boş gönderim engeli, "Nur einzelne Artikel" alanı, gönderilen alanlar ve
`credentials: omit`, bulunan ve bulunamayan sipariş görünümü, hata ve yedek adres, × / Esc /
karartılmış alan, kapanınca odak ve kaydırma, yeniden açılınca boş form, `#vertrag-widerrufen`
bağlantısı, telefonda tam ekran ve yatay kayma olmaması. Konsolda hata yok. Gerçek mağazada önizleme
ayrıca yapılmalı.
