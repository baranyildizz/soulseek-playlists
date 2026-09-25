# Soulseek Playlists


<p align="center">
<img width="100%" alt="Soulseek Playlists uygulama akışı" src="https://github.com/user-attachments/assets/f6e1bb98-cfb2-4aa5-9dd1-f3dcba48e50b" />
.txt   ->   app   ->   flac, mp3
</p>



TXT şarkı listelerini slskd üzerinden arayıp DJ kullanımı için ayrı playlist klasörlerine indirmeye yardımcı olan Windows masaüstü uygulaması. Yalnızca indirme hakkınız olan içerikler için kullanın.

## Kullanım


Windows paketini GitHub Releases bölümünden indirin. ZIP'i açın; `Soulseek Playlists.exe` ve yanındaki `_internal` klasörünü birlikte tutun. İlk çalıştırmada slskd programını, slskd yapılandırma dosyasını, tamamlanan indirme klasörünü ve playlistlerin kaydedileceği klasörü seçin. Ayrıntılar için [PAYLASIM.md](PAYLASIM.md) dosyasına bakın.

[Windows için indir — v0.1.0-beta.1 (ZIP)](https://github.com/baranyildizz/soulseek-playlists/releases/download/v0.1.0-beta.1/Soulseek-Playlists-Windows.zip)

Not: Bu ZIP, 25 Eylül'deki eksik şarkı arama düzeltmelerinden önce üretilmiştir. Güncel düzeltmeler kaynak kodundadır; yeni Windows paketi henüz yayımlanmadı.

Bu beta sürüm kod imzası taşımaz; Windows güvenlik uyarısı gösterebilir. İlk denemeyi 1–3 parçalık, indirme hakkınız olan bir listeyle yapın.

Bu repoda kişisel ayarlar, hesap bilgileri, müzik dosyaları, playlistler ve indirme geçmişi bulunmaz. 


[slskd](https://github.com/slskd/slskd) ayrıca kurulmalıdır ve kendi [Soulseek](https://www.slsknet.org/news/node/680) hesabınızla yapılandırılmalıdır.


## Kaynaktan çalıştırma


Python 3.10+ ortamında `requirements-gui.txt` bağımlılıklarını kurun ve `python playlist_app.py` çalıştırın. Testler: `python -m unittest test_playlist test_slskd_runtime test_distribution`. Windows paketini üretmek için `requirements-build.txt` ve `python build_release.py` kullanın.


Mac sürümü henüz hazırlanmadı; platforma özgü slskd başlatma, yollar ve paketleme ayrıca uyarlanmalıdır.


Kaynak kod [MIT lisansı](LICENSE) altındadır. Üçüncü taraf bağımlılıkların lisansları dağıtım paketinde bulunur.

