# Soulseek Playlists

TXT şarkı listelerini slskd üzerinden arayıp DJ kullanımı için ayrı playlist klasörlerine indirmeye yardımcı olan Windows masaüstü uygulaması. Yalnızca indirme hakkınız olan içerikler için kullanın.

## Kullanım

Windows paketini GitHub Releases bölümünden indirin. ZIP'i açın; `Soulseek Playlists.exe` ve yanındaki `_internal` klasörünü birlikte tutun. İlk çalıştırmada slskd programını, slskd yapılandırma dosyasını, tamamlanan indirme klasörünü ve playlistlerin kaydedileceği klasörü seçin. Ayrıntılar için [PAYLASIM.md](PAYLASIM.md) dosyasına bakın.

Bu depoda kişisel ayarlar, hesap bilgileri, müzik dosyaları, playlistler ve indirme geçmişi bulunmaz. slskd ayrıca kurulur ve kendi Soulseek hesabınızla yapılandırılır.

## Kaynaktan çalıştırma

Python 3.10+ ortamında `requirements-gui.txt` bağımlılıklarını kurun ve `python playlist_app.py` çalıştırın. Testler: `python -m unittest test_playlist test_slskd_runtime test_distribution`. Windows paketini üretmek için `requirements-build.txt` ve `python build_release.py` kullanın.

Mac sürümü henüz hazırlanmadı; platforma özgü slskd başlatma, yollar ve paketleme ayrıca uyarlanmalıdır.

Bu özel test deposuna henüz kaynak kod lisansı eklenmedi. Üçüncü taraf bağımlılıkların lisansları dağıtım paketinde bulunur.
