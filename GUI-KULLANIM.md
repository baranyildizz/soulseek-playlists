# Soulseek Playlists — Kullanım

`Start Playlist.vbs` dosyasına çift tıklayın. slskd kapalıysa arka planda başlatılır ve
kayıtlı bilgilerle bağlanır; bağlantı durumu üst satırda görünür. Uygulama `slskd.yml`
dosyasını değiştirmez, parolaları hiçbir yere yazmaz.

## Playlist eklemek

- **+ Ekle** ile TXT veya CSV seçin, dosyayı pencereye sürükleyin ya da kopyaladığınız
  şarkı listesini pencerede **Ctrl+V** ile yapıştırın.
- TXT'de her satır `Sanatçı - Şarkı` veya `Şarkı - Sanatçı`; sıra otomatik algılanır,
  önizlemeden kontrol edip değiştirebilirsiniz.
- **CSV** (ör. Spotify için Exportify çıktısı): şarkı, sanatçı, albüm ve **süre** sütunları
  başlıktan bulunur. Süre bilinince önizleme, kesik dosya ve radio edit gibi kısa
  sürümler otomatik indirilmez; daha uzun (extended) sürümler kabul edilir.
- **Ekran görüntüsü:** tracklist ekran görüntüsünü pencerede **Ctrl+V** ile yapıştırın veya resim
  dosyasını bırakın. Windows’un yerleşik metin tanıması satırları okur; zaman damgaları atılır,
  “Şarkı by Sanatçı” biçimi çevrilir. Açılan pencerede listeyi düzeltip playlisti oluşturun —
  metin tanıma “I/1”, “rn/m” gibi harfleri karıştırabilir, aramadan önce bir göz atın.
- Playlist adını içe aktarırken değiştirebilirsiniz. **Önce yalnızca ara** seçilirse
  hiçbir dosya inmez; ne bulunacağını görmek için kullanın.

## Ana pencere

- **Başlat / Duraklat** tek düğmedir. Duraklatınca yeni arama ve istek yapılmaz,
  başlamış transferler izlenmeye devam eder.
- **Daha iyisini bul** (ayrıntı panelinde veya ⋯ menüsünde şüpheli dosyalar için toplu): daha iyi
  kaynak arar; doğrulanmış daha iyi dosya inene kadar mevcut dosya yerinde kalır, eskisi
  `Quarantine` klasörüne taşınır. Daha iyisi bulunamazsa parça eski hâline döner.
- **Tekrar ara** yalnızca bulunamayan parça varsa görünür; hepsini bütün sorgularla
  hemen yeniden arar ve daha önce denenen peer'ları yeniden dener.
- **⋯** menüsü (veya playlistte sağ tık): klasörü aç, rekordbox XML'ini göster,
  raporu aç, yeniden adlandır, sütun sırasını değiştir, işi iptal et, listeden kaldır
  (dosyalar silinmez).
- Filtre düğmeleri (**Tümü, Tamamlandı, İniyor, Aranıyor, Seçim bekliyor, Atlandı**)
  sayıları gösterir ve tıklanınca tabloyu süzer.
- Durum sütunu düz dille yazar: *İniyor · %45*, *Peer kuyruğunda*, *Bulunamadı · 1 sa 40 dk sonra*.
- Kalite sütununda spektrumu şüpheli dosyalar turuncu “şüpheli (15 kHz)” ile işaretlenir.
- Tamamlanan satıra çift tıklamak veya **Boşluk** tuşu dosyayı varsayılan oynatıcıda açar.

## Ayrıntı paneli (sağda)

Bir parça seçin:

- Durum, listedeki süre ve son arama sonucu.
- **Elle ara:** sorguyu düzenleyip **Ara**'ya basın; sonuçlar adaylara eklenir.
- **Adı düzelt…:** TXT'deki ad yanlışsa (ör. sanatçı farklı yazılmış) düzeltin;
  parça yeni adla yeniden aranır.
- **Adaylar:** tam dosya adı; istenenden farklı sürüm (ör. **(Dub)**) kırmızıdır.
  Altında kalite, boyut, süre, peer, kuyruk ve puan. En iyi güvenli aday
  “★ Önerilen” işaretlidir. **Bunu indir** (veya çift tık) seçtiğiniz dosyayı indirir;
  elle seçimde kalite eşiği uygulanmaz.
- **Atla / Sonraya bırak / Geri al:** kararlar kalıcıdır, diğer parçalar çalışmaya devam eder.
- **Denemeler ve geçmiş:** hangi peer'ların neden başarısız olduğu ve yapılan aramalar.

Yalnızca benzer sürüm bulunan parçalar için tepside bildirim çıkar; bildirime
tıklamak **Seçim bekliyor** filtresini açar. Bir playlist bitince de bildirim gelir.

## Arama ve eşleştirme

- Her parça için sırayla birkaç sade sorgu denenir: ana sanatçı + şarkı adı, remix
  adıyla şarkı, diğer sanatçılarla şarkı, ayırt ediciyse yalnızca şarkı adı, aksansız
  yazım. “Original Mix” gibi sözcükler sorguya eklenmez; Soulseek sorgudaki her
  sözcüğü dosya yolunda arar. Kesme işaretli sözcükler bölünür (“Don't” → “Don”).
- En fazla 3 arama aynı anda, 8 sn aralıkla başlar.
- Eşleşme: sanatçı (dosya adı veya üst klasörler), şarkı adı, sürüm. “Original Mix”,
  “Extended Mix”, “Remastered” ve sürüm yazmayan dosya aynı sürüm sayılır. Club Mix,
  Dub, Edit, Live ve başka remixçiler ayrıdır; otomatik inmez, seçim bekler.
- TXT'de “…” ile kesilmiş adlar ön ek olarak eşleştirilir.
- Bulunamayanlar 30 dk, 2 sa, 6 sa, sonra 12 saatte bir yeniden aranır.
- **Kütüphane klasörleri** ayarlıysa, bilgisayarınızda zaten olan eşleşen dosya
  Soulseek'te aranmadan kopyalanır.

## İndirme ve kalite

- Hızlı modda parça başına iki peer; ikisi de 20 dakikadan uzun kuyrukta bekliyorsa
  bir peer daha denenir, ilk biten kazanır.
- Peer sırası: geçmişte sık başarısız olanlar sona, sonra boş slot, kalite
  (FLAC > AIFF/WAV > MP3 320 > bitrate'i bilinmeyen MP3), kısa kuyruk ve hız.
- Çevrimdışı peer hemen geçilir; parça başına günde en fazla 10 peer denenir.
  10 dakika ilerlemeyen indirme iptal edilip başka kaynak denenir.
- İnen dosya boyut ve ses başlığı açısından doğrulanır; otomatik seçilen MP3'lerin
  gerçek bitrate'i 320 olmalıdır.
- **Spektrum kontrolü** (ffmpeg gerekir): MP3 kodlayıcıları belli bir frekansın üstünü keser
  (128 kbps ≈ 16 kHz, 192 ≈ 19 kHz) ve spektrumda keskin bir “duvar” bırakır. 19 kHz altında
  böyle bir duvar varsa dosya düşük bitrate MP3’ten dönüştürülmüştür: `Quarantine` klasörüne
  alınır ve başka kaynak denenir; iki denemeden sonra en iyi dosya “şüpheli” işaretiyle
  tutulur. Tizleri doğal olarak zayıf (karanlık) kayıtlar duvar göstermediği için işaretlenmez.
  Önceden inmiş dosyalar da arka planda ölçülür. 320 kbps’tan dönüştürülmüş dosyalar bu
  yöntemle ayırt edilemez.
- Aynı şarkı başka playlistte zaten indiyse yeniden indirilmez, kopyalanır.
- Biten transferler slskd listesinden temizlenir; dosyalar korunur.
- İndirme sürerken Windows uyku moduna geçmez.

## Playlist klasörü

Her playlist kendi klasöründedir: ses dosyaları, `.m3u8` playlist, `rapor.csv` ve
`.rekordbox.xml`. rekordbox'ta *Tercihler › Gelişmiş › rekordbox xml* ile XML'i seçip
playlisti içe aktarabilirsiniz. Dosyanın `tracknumber` etiketi TXT sırasıyla yazılır;
diğer etiketler korunur.

## Ayarlar

Üstteki **Ayarlar** penceresinde slskd yolları ve klasörlerin yanında:

- **Dosya formatı:** orijinal veya FLAC/WAV'ı **AIFF'e çevir** (etiketler korunur).
- **Dosya adı:** peer'deki ad veya listedeki adla “Sanatçı - Şarkı”.
- Spektrum kontrolü, rekordbox XML, **kütüphane klasörleri**,
  **Windows açılışında tepside başlat**.

Ayarlar bir sonraki açılışta uygulanır. İnce ayarların varsayılanları `engine.py`
içindeki `DEFAULTS` bölümündedir; config.json'a aynı adla yazılan değer onları geçersiz kılar.

**Paylaşım uyarısı:** slskd'de hiç paylaşılan klasör yoksa üstte sarı bir uyarı çıkar.
Birçok kullanıcı paylaşımı olmayanlara dosya vermez (“Rejected”); slskd ayarlarından
bir müzik klasörü paylaşmanız önerilir.

## Arka plan

Pencereyi kapatmak tepsiye küçültür; tamamen çıkmak için tepsi menüsünden
**Tamamen çık**. slskd'ye verilmiş transferler devam eder; yeniden açınca eşitlenir.
**Günlük** düğmesi tüm işlemleri zamanlarıyla gösterir.

## Geliştirme

| Dosya | Görev |
|---|---|
| `matching.py` | TXT/CSV ayrıştırma, sütun algılama, sorgu planı, puanlama, kalite |
| `slskd_api.py` | slskd REST istemcisi |
| `audio.py` | Ses doğrulama, etiketler, spektrum, AIFF dönüşümü |
| `store.py` | SQLite şeması ve kayıtlar |
| `engine.py` | Arama, peer seçimi, transfer izleme, teslim, kütüphane |
| `playlist_app.py`, `app_setup.py` | Qt arayüzü ve ayarlar |

`python -m unittest -v test_playlist` motoru sahte API ile test eder.
`python gui_smoke.py` arayüzü geçici verilerle çizer.
`python live_search_check.py "Sanatçı - Şarkı" ...` çalışan slskd'de indirmesiz arama yapar.
