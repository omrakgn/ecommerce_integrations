# Withdrawal Form: cayma düğmesi Shopify uygulaması olarak

Yasal "Vertrag widerrufen" düğmesi ve formu (§ 356a BGB). Uygulama temaya blok ekliyor ve Shopify
yönetiminde beyanları listeleyen bir sayfası var. Mağaza verisine hiçbir yetkisi yok: formun
gönderdiği beyanı ERPNext işliyor (`multichannel_core.returns.withdrawal.submit`), yönetimdeki
sayfayı da ERPNext sunuyor (bkz. hub'da `docs/cayma-butonu.md`).

- **Tür:** kendi sunucusu olmayan uygulama (tema uzantısı + ERPNext'in sunduğu sayfa); App Store'da
  yok.
- **Sahibi:** mağazanın kendi organizasyonu, yani mağazanın Dev Dashboard'u (2026-11-13'ten beri).
  Mağazaya oradaki "Installs" kartından kurulur; dağıtım adımı ve IP allowlist yok. Client ID
  `shopify.app.toml`'da.
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

- İlk seferde tarayıcıda Shopify girişi açılır; mağaza sahibi hesabıyla (ya da mağazada **App
  developer** rolü olan bir hesapla) girin. Organizasyon sorulursa mağazanın organizasyonu.
- CLI başka bir ayar dosyasını ya da eski uygulamayı hatırlıyorsa: `shopify app config use
  shopify.app.toml`. Başka bir hesapla girilmişse önce `shopify auth logout`.
- "Release a new version?" sorusuna **Yes**.
- Yayın bitince depoda değişen dosya olursa (CLI uzantıya kimlik yazabilir) commit edin.

Denetim (yayından önce, isteğe bağlı):
`shopify theme check --path extensions/withdrawal-form --config theme-check:theme-app-extension`

## Kurulum (bir kez)

Önce `shopify app deploy` (kurulacak bir sürüm olmalı), sonra:

1. Mağazanın Shopify yönetiminde (bilgisayardan) **Settings > Apps > Develop apps > Build apps in
   Dev Dashboard**. dev.shopify.com mağazanın organizasyonunda açılır; sol üstteki ad bunu söyler.
2. **Apps > Withdrawal Form > Installs** kartında **Install app**, mağazayı seçin, onaylayın.

**Neden mağazanın kendi organizasyonunda (2026-11-13).** Uygulama önce başka bir organizasyonda
açılmış ve mağazaya özel dağıtım bağlantısıyla kurulmuştu (Client ID `f31e2cb3…`). Shopify,
kendi organizasyonu dışındaki bir mağazaya kurulan özel uygulamadan IP allowlist istiyor: yeni
kurulumlarda 20 Kasım 2026'dan, kurulu olanlarda 1 Ocak 2027'den itibaren. Mağazanın kendi
organizasyonundaki uygulamada bu yok ve "Distribution" kartı hiç çıkmıyor; kurulum "Installs"
kartından. Yeni uygulama aynı klasörden `shopify app config link` ile açıldı (yeni Client ID);
eski uygulama mağazadan kaldırılır, eski organizasyondan silinir.

**Uygulama uzantı yayınlanmadan kurulduysa yeniden kurun.** 2026-11-10'da ilk uygulama,
oluşturulurken açılan uzantısız 1. sürümle kuruldu. `withdrawal-form-2` "New version released to
users" ile etkin olduğu halde tema düzenleyicide App embeds'te ve Add block'ta görünmedi.
Kaldırıp (Settings > Apps > Withdrawal Form > Uninstall) yeniden kurunca geldi. Bu yüzden sıra
hep önce yayın, sonra kurulum.

**Uygulama değişince tema ayarları taşınmaz.** Bloğun ve gömmenin ayarları (düğme yazısı, renkler,
yedek e-posta) uygulamaya bağlı. Yeni uygulamada tema düzenleyicide yeniden açılır ve girilir.

## Simge (2026-11-13)

Shopify yönetiminde uygulama listesinde ve sol menüde görünen simge: belge ve köşesinde turuncu
"geri" rozeti (sözleşmeden cayma). Markadan bağımsız; firma logosu yok.

- `app-icon/icon-1200.png`: Shopify'a yüklenen dosya. 1200 x 1200 PNG, köşeleri düz; çizim 697 x 877
  px, kenar boşluğu en az 161 px (Shopify: en az 75 px, çizim 750 ile 900 px arası).
- `app-icon/icon.svg`: kaynak. Değişirse PNG yeniden üretilir (tarayıcıda 1200 x 1200 açıp ekran
  görüntüsü). Renkler: zemin `#1E3A5F`, rozet `#F2A541`, belge çizgileri `#B9C9DE`.
- **Yükleme CLI ile değil, elle:** mağazanın Dev Dashboard'u > Withdrawal Form > **Settings** > App
  icon. Shopify sol menüde simgeyi küçültüp kendisi çiziyor (20 px).

## Shopify yönetimindeki sayfa: cayma beyanları (2026-11-10)

Uygulamayı Shopify yönetiminde (Apps > Withdrawal Form) açınca cayma beyanlarının listesi
görünür. Sayfayı ERPNext sunar (`application_url`,
`ecommerce_integrations/shopify/withdrawal_app.py`); Shopify onu yönetimin içinde açar.

- **Gösterilen:** son 200 beyan ve bakılması gereken her beyan (Needs Check, Not Found). Alındı
  zamanı, WD- numarası (ERPNext'teki kayda bağlantı, yeni sekmede), sipariş numarası (Shopify'daki
  siparişi açar), ad ve e-posta, tamamı mı bazı ürünler mi, durum, iade vakası. "Needs attention" ve
  "All" sekmeleri, arama kutusu. Yalnız okunur: kararlar ve düzeltmeler ERPNext'te.
- **Kim görür:** Shopify'da bu uygulamaya erişimi olan her personel. Sayfada ad ve e-posta var;
  erişim Shopify'ın personel izinlerinden (Settings > Users > personel > Apps) sınırlanır.
- **Güvenlik:** Shopify her açılışı uygulamanın Client secret'ıyla imzalıyor. ERPNext imzayı,
  zamanı (en çok 10 dakika) ve mağazayı kontrol ediyor; biri tutmazsa liste gösterilmiyor.
  Sayfayı yalnız Shopify yönetimi çerçeve içinde açabiliyor.
- **Ayar (bir kez):** ERPNext > Shopify Setting > **Withdrawal Form App**: Client ID (bu dosyadaki
  `client_id`) ve **Client Secret** (mağazanın Dev Dashboard'u > Withdrawal Form > Settings). Uygulama
  değişince ikisi de değişir. Secret yalnız
  ERPNext'e girilir; kimseyle paylaşılmaz, depoya yazılmaz. Eksikse sayfa "not set up" der.
- **Yayın:** `application_url` değişti; bir kez `shopify app deploy`.
- **Dil:** Shopify yönetimi Türkçeyse sayfa Türkçe, değilse İngilizce.

## Tema düzenleyicide (her tema için)

Uygulamanın tema tarafındaki her şeyi tema düzenleyicide.

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
