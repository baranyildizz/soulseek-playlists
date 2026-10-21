# Soulseek Playlists


<p align="center">
<img width="100%" alt="Soulseek Playlists uygulama akışı" src="https://github.com/user-attachments/assets/f6e1bb98-cfb2-4aa5-9dd1-f3dcba48e50b" />
.txt / .csv   ->   app   ->   flac, aiff, mp3
</p>



Şarkı listelerini (TXT, Spotify/Exportify CSV veya panodan yapıştırılan metin) [slskd](https://github.com/slskd/slskd) üzerinden Soulseek'te arayan, doğru sürümü bulup sırayla indiren ve her listeyi DJ kullanımına hazır ayrı bir klasörde toplayan Windows masaüstü uygulaması. Yalnızca indirme hakkınız olan içerikler için kullanın.

## Kullanım

Windows paketini [GitHub Releases](https://github.com/baranyildizz/soulseek-playlists/releases/latest) bölümünden indirin. ZIP'i açın; `Soulseek Playlists.exe` ve yanındaki `_internal` klasörünü birlikte tutun. İlk çalıştırmada slskd programını, slskd yapılandırma dosyasını, tamamlanan indirme klasörünü ve playlistlerin kaydedileceği klasörü seçin. Ayrıntılar: [PAYLASIM.md](PAYLASIM.md) ve [GUI-KULLANIM.md](GUI-KULLANIM.md).

Bu beta sürüm kod imzası taşımaz; Windows güvenlik uyarısı gösterebilir. İlk denemeyi 1–3 parçalık, indirme hakkınız olan bir listeyle yapın.

[slskd](https://github.com/slskd/slskd) ayrıca kurulmalı ve kendi [Soulseek](https://www.slsknet.org/news/node/680) hesabınızla yapılandırılmalıdır. slskd'de en az bir klasör paylaşmanız önerilir: birçok Soulseek kullanıcısı hiç paylaşımı olmayanlara dosya vermez.

## Neler yapar

- **Doğru sürümü bulur.** Sanatçı, şarkı adı ve sürüm ayrı ayrı doğrulanır. “Original Mix”, “Extended Mix” ve sürümsüz dosya aynı kabul edilir; Club Mix, Dub, Edit, Live veya başka bir remixçi otomatik inmez, seçiminize sunulur. Listede “…” ile kesilmiş adlar da eşleşir.
- **Soulseek'e uygun arar.** Sorgular sade tutulur (sürüm sözcükleri eklenmez, kesme işaretli sözcükler Soulseek gibi bölünür, aksanlı ve aksansız yazım denenir). Aynı anda 3 arama yapılır; bulunamayanlar artan aralıklarla yeniden aranır.
- **İyi kaynağı seçer.** Boş slotu olan, güvenilir peer'lar önce; sonra FLAC > AIFF/WAV > MP3 320. Çevrimdışı, reddeden veya takılan peer yerine sıradaki denenir.
- **Kaliteyi denetler.** İnen dosya doğrulanır; MP3'ün gerçek bitrate'i kontrol edilir. ffmpeg kuruluysa spektrumda MP3 kesme “duvarı” aranır ve düşük bitrate MP3'ten dönüştürülmüş FLAC'lar elenir. Tamamlanmış şüpheli dosyalar için **Daha iyisini bul** mevcut dosyayı koruyarak daha iyi kaynak arar.
- **CSV ile süre doğrulaması.** Spotify/Exportify CSV'sindeki süre sayesinde önizleme, kesik dosya ve radio edit otomatik inmez.
- **DJ klasörü hazırlar.** Her playlist kendi klasöründe: ses dosyaları, `.m3u8`, `rapor.csv` ve **rekordbox XML**. Sıra `tracknumber` etiketine yazılır. İsteğe bağlı olarak FLAC/WAV **AIFF'e** çevrilir ve dosyalar “Sanatçı - Şarkı” diye adlandırılır.
- **Kütüphanenizi kullanır.** Bilgisayarınızda zaten olan şarkılar indirilmez, kopyalanır.
- **Sade arayüz.** Filtre düğmeleri, sağda ayrıntı paneli (adaylar, farklı sürüm kırmızıyla vurgulu, elle arama, ad düzeltme), tepside çalışma, bildirimler, koyu tema.

## Kaynaktan çalıştırma

Python 3.10+ ortamında `pip install -r requirements-gui.txt` ile bağımlılıkları kurun ve `python playlist_app.py` çalıştırın. Spektrum kontrolü ve AIFF dönüşümü için [ffmpeg](https://ffmpeg.org/) (ör. `winget install Gyan.FFmpeg`) önerilir; yoksa bu iki özellik kapalı kalır.

| Dosya | Görev |
|---|---|
| `matching.py` | Liste ayrıştırma, sütun algılama, sorgu planı, puanlama, kalite |
| `slskd_api.py` | slskd REST istemcisi |
| `audio.py` | Ses doğrulama, etiketler, spektrum, AIFF dönüşümü |
| `store.py` | SQLite şeması ve kayıtlar |
| `engine.py` | Arama, peer seçimi, transfer izleme, teslim |
| `playlist_app.py`, `app_setup.py` | Qt arayüzü ve ayarlar |

Testler: `python -m unittest test_playlist test_slskd_runtime test_distribution` ve `python gui_smoke.py`. Çalışan slskd'de indirmesiz arama denemesi: `python live_search_check.py "Sanatçı - Şarkı"`. Windows paketini üretmek için `requirements-build.txt` ve `python build_release.py` kullanın.

Mac sürümü henüz hazırlanmadı; platforma özgü slskd başlatma, yollar ve paketleme ayrıca uyarlanmalıdır.

Bu repoda kişisel ayarlar, hesap bilgileri, müzik dosyaları, playlistler ve indirme geçmişi bulunmaz. Kaynak kod [MIT lisansı](LICENSE) altındadır. Üçüncü taraf bağımlılıkların lisansları dağıtım paketinde bulunur.
