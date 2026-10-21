"""Turkish / English user interface text.

The Turkish text in the code is the key; `tr()` returns the English version when
English is active. Strings with placeholders are templates: `tr('{n} parça').format(n=3)`.
The language is chosen once at start-up ('auto' follows the Windows display language).
Messages the engine writes to the database are stored in the language active at the time.
Internal markers kept in data (e.g. quality notes such as "16 kHz · şüpheli") are only
translated for display with `note_text()`.
"""
from __future__ import annotations

LANGUAGES = {'auto': 'Sistem dili / System language', 'tr': 'Türkçe', 'en': 'English'}
_active = 'tr'


def set_language(code: str | None) -> str:
    """Activate 'tr', 'en' or 'auto' (Turkish Windows → Turkish, everything else → English)."""
    global _active
    if code not in ('tr', 'en'):
        code = system_language()
    _active = code
    return code


def language() -> str:
    return _active


def system_language() -> str:
    try:
        from PySide6.QtCore import QLocale
        return 'tr' if QLocale.system().language() == QLocale.Language.Turkish else 'en'
    except Exception:
        import locale
        return 'tr' if (locale.getlocale()[0] or '').lower().startswith('tr') else 'en'


def tr(text: str) -> str:
    if _active == 'en':
        return EN.get(text, text)
    return text


def note_text(note: str | None) -> str:
    """Display form of a stored spectrum note ("16 kHz · şüpheli", "kesme yok")."""
    note = note or ''
    if _active != 'en':
        return note
    return (note.replace('kesme yok', 'no cutoff').replace('(daha iyisi bulunamadı)', '(nothing better found)')
            .replace('şüpheli', 'suspect'))


EN = {
    # --- filters and statuses
    'Tümü': 'All',
    'Tamamlandı': 'Done',
    'İniyor': 'Downloading',
    'Aranıyor': 'Searching',
    'Seçim bekliyor': 'Needs a pick',
    'Şüpheli kalite': 'Suspect quality',
    'Atlandı': 'Skipped',
    '{h} sa {m} dk': '{h} h {m} min',
    '{m} dk': '{m} min',
    '{s} sn': '{s} s',
    'Aranıyor…': 'Searching…',
    'Daha iyisi aranıyor': 'Looking for better',
    'İniyor · %{percent}': 'Downloading · {percent}%',
    'Peer kuyruğunda': 'In peer queue',
    'Kaynak bulundu': 'Source found',
    'Sonraya bırakıldı': 'Deferred',
    'Sırada': 'Up next',
    'Sırada · {wait}': 'Up next · {wait}',
    'Bulunamadı': 'Not found',
    'Yeni kaynak aranacak': 'New source pending',
    ' · {wait} sonra': ' · in {wait}',
    # --- worker
    'İşlem yapılamadı; dosya erişimini kontrol edin.': 'Could not complete the action; check file access.',
    'İşlem hatası: {name} · işler korunuyor': 'Processing error: {name} · jobs are kept',
    # --- import dialogs
    'Playlist ekle': 'Add playlist',
    'Playlist adı': 'Playlist name',
    'CSV sütunları (başlıktan okunur)': 'CSV columns (read from the header)',
    'Sanatçı - Şarkı': 'Artist - Title',
    'Şarkı - Sanatçı': 'Title - Artist',
    '  (otomatik algılandı)': '  (detected)',
    'Sütun sırası': 'Column order',
    'Sanatçı': 'Artist',
    'Şarkı': 'Title',
    'Süre': 'Length',
    'Önce yalnızca ara — hiçbir dosya indirme (sonuçları görmek için)': 'Search only first — download nothing (to see what would be found)',
    'Playlist oluştur': 'Create playlist',
    'Vazgeç': 'Cancel',
    ' · {timed} parçada süre var; kısa sürümler (radio edit, önizleme) otomatik elenir.':
        ' · {timed} tracks have a length; shorter versions (radio edits, previews) are skipped automatically.',
    '{count} parça{extra}\nOluşturduktan sonra Başlat’a basın.': '{count} tracks{extra}\nPress Start after creating it.',
    'Dosya UTF-8 metin olarak okunamadı.': 'The file could not be read as UTF-8 text.',
    'Ekran görüntüsü okunamadı.': 'The screenshot could not be read.',
    'Ekran görüntüsünden playlist': 'Playlist from screenshot',
    'Ekran görüntüsü %Y-%m-%d %H.%M': 'Screenshot %Y-%m-%d %H.%M',
    'Her satır bir parça: <b>Sanatçı - Şarkı</b>. Metin tanıma “I/1”, “rn/m” gibi harfleri karıştırabilir; aramadan önce adları göz ucuyla kontrol edin.':
        'One track per line: <b>Artist - Title</b>. Text recognition can mix up letters such as “I/1” or “rn/m”; give the names a quick look before searching.',
    'Okunuyor…': 'Reading…',
    'Windows metin tanıma çalışıyor…': 'Windows text recognition is running…',
    'Önce yalnızca ara — hiçbir dosya indirme': 'Search only first — download nothing',
    ' Listeyi elle yazabilir veya yapıştırabilirsiniz.': ' You can type or paste the list yourself.',
    'Parça bulunamadı.': 'No tracks found.',
    '{count} satır · ayırıcısı olmayan satır: {lines}': '{count} lines · lines without a separator: {lines}',
    ' (“Sanatçı - Şarkı” biçiminde düzeltin veya silin)': ' (fix them as “Artist - Title” or delete them)',
    '{count} parça hazır.': '{count} tracks ready.',
    # --- detail panel
    'Bir parça seçin': 'Select a track',
    'Dinle': 'Play',
    'Klasörde göster': 'Show in folder',
    'Adı düzelt…': 'Fix name…',
    'Daha iyisini bul': 'Find better',
    'Daha iyi bir kaynak arar; doğrulanmış daha iyi dosya inene kadar mevcut dosya yerinde kalır':
        'Looks for a better source; the current file stays until a verified better one has downloaded',
    'ELLE ARA': 'MANUAL SEARCH',
    'Soulseek’te aranacak sözcükler': 'Words to search for on Soulseek',
    'Ara': 'Search',
    'ADAYLAR': 'CANDIDATES',
    'Bunu indir': 'Download this',
    'Atla': 'Skip',
    'Sonraya bırak': 'Later',
    'Geri al': 'Undo',
    'DENEMELER VE GEÇMİŞ': 'ATTEMPTS AND HISTORY',
    'Durumunu, adaylarını ve geçmişini burada görürsünüz. Bulunamayan parçayı elle arayabilir veya adını düzeltebilirsiniz.':
        'Its status, candidates and history appear here. You can search for a missing track yourself or fix its name.',
    ' · spektrum {note}': ' · spectrum {note}',
    'boş slot': 'free slot',
    'kuyruk {n}': 'queue {n}',
    'puan {score}': 'score {score}',
    '★ Önerilen': '★ Recommended',
    'tamamlandı': 'done',
    'başarısız': 'failed',
    'iptal': 'cancelled',
    'kuyrukta': 'queued',
    'iniyor': 'downloading',
    'gönderiliyor': 'sending',
    'doğrulanıyor': 'verifying',
    'Henüz deneme yok.': 'No attempts yet.',
    'Henüz aday yok. Elle arayabilir veya adı düzeltebilirsiniz.': 'No candidates yet. You can search yourself or fix the name.',
    ' · Aktif indirme sürüyor.': ' · A download is in progress.',
    'Adı düzelt': 'Fix name',
    'Sanatçı ve şarkıyı boşluk-tire-boşluk ile ayırın.': 'Separate artist and title with space-dash-space.',
    # --- main window
    'Bağlanıyor…': 'Connecting…',
    'Ayarlar': 'Settings',
    'Küçült': 'Minimise',
    'Büyüt': 'Maximise',
    'Tepsiye küçült': 'Minimise to tray',
    'slskd’de paylaşılan klasör yok. Birçok Soulseek kullanıcısı hiç paylaşımı olmayanlara dosya vermiyor (“Rejected”). slskd ayarlarından bir müzik klasörü paylaşmanız önerilir.':
        'slskd shares no folders. Many Soulseek users refuse uploads to people who share nothing (“Rejected”). Sharing a music folder in slskd’s settings is recommended.',
    'slskd’yi aç': 'Open slskd',
    'Kapat': 'Close',
    'PLAYLISTLER': 'PLAYLISTS',
    '+ Ekle': '+ Add',
    'TXT, CSV veya ekran görüntüsü ekleyin, pencereye sürükleyin ya da Ctrl+V ile yapıştırın':
        'Add a TXT, CSV or screenshot, drag it onto the window or paste it with Ctrl+V',
    'TXT, CSV veya ekran görüntüsü bırakın; ya da Ctrl+V ile yapıştırın.': 'Drop a TXT, CSV or screenshot, or paste with Ctrl+V.',
    'Bir playlist ekleyin': 'Add a playlist',
    'Başlat': 'Start',
    'Duraklat': 'Pause',
    'Tekrar ara': 'Search again',
    'Bulunamayanları bütün sorgularla hemen yeniden arar': 'Searches missing tracks again right away with every query',
    'Diğer işlemler': 'More actions',
    'Sanatçı veya şarkı ara…': 'Filter by artist or title…',
    'Durum': 'Status',
    'Kalite': 'Quality',
    'Canlı günlük · tüm playlistler': 'Live log · all playlists',
    'Henüz işlem yok': 'Nothing yet',
    'Günlük': 'Log',
    'Uygulamayı göster': 'Show app',
    'Tamamen çık': 'Quit completely',
    'İşlem bilgisi': 'Note',
    'Açık moda geç': 'Switch to light mode',
    'Koyu moda geç': 'Switch to dark mode',
    'Ayar dosyası okunamadı; mevcut dosya korunuyor.': 'The settings file could not be read; the existing file is kept.',
    'Ayarlar kaydedildi': 'Settings saved',
    'Ayarlar bir sonraki açılışta uygulanacak.\nTepsi menüsünden Tamamen çık seçip uygulamayı yeniden açın.':
        'Settings take effect the next time the app starts.\nChoose Quit completely in the tray menu and open the app again.',
    'Playlist seç': 'Choose playlist',
    'Şarkı listesi veya ekran görüntüsü (*.txt *.csv *.png *.jpg *.jpeg *.bmp *.webp)':
        'Track list or screenshot (*.txt *.csv *.png *.jpg *.jpeg *.bmp *.webp)',
    'Yapıştır': 'Paste',
    'Panoda "Sanatçı - Şarkı" satırları veya ekran görüntüsü bulunamadı.': 'The clipboard holds no "Artist - Title" lines or screenshot.',
    'Pano {date}': 'Clipboard {date}',
    'Klasörü aç': 'Open folder',
    'rekordbox XML’ini göster': 'Show rekordbox XML',
    'Raporu aç (rapor.csv)': 'Open report (rapor.csv)',
    'Şüpheli dosyalar için daha iyisini bul ({count})': 'Find better for suspect files ({count})',
    'Yeniden adlandır…': 'Rename…',
    'Yeniden adlandır': 'Rename',
    'Sütun sırasını değiştir ({label})': 'Switch column order ({label})',
    'İşi iptal et': 'Cancel job',
    'Listeden kaldır…': 'Remove from list…',
    'Listeden kaldır': 'Remove from list',
    '“{name}” listeden kaldırılsın mı?\nİndirilmiş dosyalar ve klasör silinmez; aktif istekler iptal edilir.':
        'Remove “{name}” from the list?\nDownloaded files and the folder are not deleted; active requests are cancelled.',
    'Bu playlist’in indirme istekleri iptal edilsin mi? Tamamlanan dosyalar korunacak.':
        'Cancel this playlist’s download requests? Finished files are kept.',
    'çalışıyor': 'running',
    'duraklatıldı': 'paused',
    'arama testi bitti': 'search test done',
    ' · {n} seçim': ' · {n} to pick',
    'Playlist tamamlandı': 'Playlist finished',
    '{name} · {done} parça hazır.': '{name} · {done} tracks ready.',
    'Seçim bekleyen şarkılar': 'Tracks waiting for a pick',
    '{count} parça için yalnızca benzer sürümler bulundu. Seçmek için tıklayın.':
        'Only similar versions were found for {count} tracks. Click to choose.',
    'TXT / CSV ekleyin veya Ctrl+V ile şarkı listesini yapıştırın.': 'Add a TXT / CSV or paste a track list with Ctrl+V.',
    'Yeni arama ve indirmeleri durdurur; başlamış transferler izlenmeye devam eder':
        'Stops new searches and downloads; transfers already started are still followed',
    'Arama ve indirmeyi başlatır': 'Starts searching and downloading',
    '{done} tamamlandı · {skipped} atlandı · {total} parça': '{done} done · {skipped} skipped · {total} tracks',
    ' · şüpheli ({khz} kHz)': ' · suspect ({khz} kHz)',
    'Spektrum {note}: MP3’ten dönüştürülmüş olabilir.': 'Spectrum {note}: may have been converted from an MP3.',
    'Listedeki süre': 'Length from the list',
    '{n} arama': '{n} searches',
    '{n} transfer': '{n} transfers',
    'kütüphane {n} dosya': 'library {n} files',
    'Son motor güncellemesi: {time}': 'Last engine update: {time}',
    'bekleniyor': 'waiting',
    'Arka planda çalışıyor. Tepsi simgesinden tekrar açabilirsiniz.': 'Running in the background. Reopen it from the tray icon.',
    'İşler kaydediliyor…': 'Saving jobs…',
    'Ayarlar okunamadı': 'Settings could not be read',
    'config.json okunamadı. Dosya korunuyor; ayar dosyasını kontrol edin.': 'config.json could not be read. The file is kept; check the settings file.',
    # --- settings
    'Geçersiz yapılandırma': 'Invalid configuration',
    ' · Biraz sonra yeniden kontrol edin.': ' · Check again in a moment.',
    'API hazır · Soulseek bağlı': 'API ready · Soulseek connected',
    'API hazır · Soulseek bağlantısı henüz kurulmadı': 'API ready · Soulseek not connected yet',
    'Bağlantı kurulamadı. Adresi ve slskd web erişim ayarlarını kontrol edin.': 'Could not connect. Check the address and slskd’s web access settings.',
    'Soulseek Playlists · Ayarlar': 'Soulseek Playlists · Settings',
    'Bir kez ayarlayın, sonraki açılışlarda hatırlansın.\nslskd kendi Soulseek hesabınızla kurulmuş olmalı. Mevcut ayar dosyası değiştirilmez.':
        'Set this up once; it is remembered next time.\nslskd must be set up with your own Soulseek account. Its settings file is not changed.',
    '<a href="https://github.com/slskd/slskd/releases">slskd indirme sayfası</a>': '<a href="https://github.com/slskd/slskd/releases">slskd download page</a>',
    'slskd adresi': 'slskd address',
    'slskd.exe (otomatik başlatmak için)': 'slskd.exe (to start it automatically)',
    'slskd.yml (kayıtlı web erişim ayarları)': 'slskd.yml (saved web access settings)',
    'Soulseek giriş portu (isteğe bağlı; boş: slskd ayarı)': 'Soulseek listen port (optional; empty: slskd setting)',
    'slskd tamamlanan indirmeler klasörü': 'slskd finished downloads folder',
    'Playlistlerin kaydedileceği klasör': 'Folder for playlists',
    'Seç…': 'Browse…',
    'Parola bu uygulamada saklanmaz. Web erişimi seçtiğiniz slskd.yml dosyasından veya SLSKD_API_KEY / SLSKD_WEB_USERNAME / SLSKD_WEB_PASSWORD ortam değişkenlerinden okunur.\nKlasör değişikliği yalnızca yeni playlistler için geçerlidir.':
        'No password is stored by this app. Web access is read from the chosen slskd.yml or the SLSKD_API_KEY / SLSKD_WEB_USERNAME / SLSKD_WEB_PASSWORD environment variables.\nA folder change only applies to new playlists.',
    'Bağlantıyı kontrol et': 'Check connection',
    'Kaydet': 'Save',
    'Orijinal format (FLAC / MP3 / AIFF / WAV)': 'Original format (FLAC / MP3 / AIFF / WAV)',
    'FLAC ve WAV’ı AIFF’e çevir (rekordbox / CDJ)': 'Convert FLAC and WAV to AIFF (rekordbox / CDJ)',
    'Dosya formatı': 'File format',
    'Peer’deki dosya adı': 'File name from the peer',
    'Sanatçı - Şarkı (listedeki adla)': 'Artist - Title (as in the list)',
    'Dosya adı': 'File name',
    'İnen FLAC/MP3’ün spektrumunu kontrol et (sahte kaliteyi ele)': 'Check the spectrum of downloaded FLAC/MP3 (reject fake quality)',
    'Her playlist için rekordbox XML dosyası yaz': 'Write a rekordbox XML file for every playlist',
    'Örn. D:\\Müzik; E:\\DJ — eşleşen dosya indirilmez, kopyalanır': 'e.g. D:\\Music; E:\\DJ — matching files are copied, not downloaded',
    'Ekle…': 'Add…',
    'Kütüphane klasörleri': 'Library folders',
    'Windows açılışında tepside başlat': 'Start in the tray when Windows starts',
    'Dil': 'Language',
    'ffmpeg bulunamadı: AIFF dönüşümü ve spektrum kontrolü kapalı. (winget install Gyan.FFmpeg)':
        'ffmpeg not found: AIFF conversion and the spectrum check are off. (winget install Gyan.FFmpeg)',
    'Müzik klasörü seç': 'Choose music folder',
    'Klasör seç': 'Choose folder',
    'Dosya seç': 'Choose file',
    'Geçerli bir port girin.': 'Enter a valid port.',
    'Geçerli bir HTTP(S) adresi girin; adrese parola veya erişim anahtarı eklemeyin.': 'Enter a valid HTTP(S) address; do not put a password or access key in it.',
    'Seçilen slskd dosyası bulunamadı. Dosya yolunu kontrol edin.': 'The chosen slskd file was not found. Check the path.',
    'İndirme ve playlist klasörleri için tam bir klasör yolu seçin.': 'Choose a full folder path for downloads and playlists.',
    'Soulseek giriş portu 1–65535 arasında olmalı.': 'The Soulseek listen port must be between 1 and 65535.',
    'Kütüphane klasörlerinden biri bulunamadı.': 'One of the library folders was not found.',
    'Ayarlar kaydedilemedi. Dosya yollarını, adresi ve klasör yazma iznini kontrol edin.': 'Settings could not be saved. Check the paths, the address and folder write access.',
    'Bağlantı kontrol ediliyor…': 'Checking connection…',
    # --- engine (stored in the database in the active language)
    'slskd bağlantısı yenileniyor · işler korunuyor': 'Reconnecting to slskd · jobs are kept',
    'slskd hata verdi (HTTP {status}) · tekrar denenecek': 'slskd returned an error (HTTP {status}) · will retry',
    'slskd hazır · oturum açılıyor': 'slskd ready · signing in',
    'slskd hazır · Soulseek hesabının bağlanması bekleniyor': 'slskd ready · waiting for the Soulseek account to connect',
    'slskd oturumu açılamadı · web giriş bilgilerini kontrol edin': 'Could not sign in to slskd · check the web login',
    'slskd bağlantı kontrolü başarısız · 20 sn sonra yeniden denenecek': 'slskd connection check failed · retrying in 20 s',
    'slskd · Soulseek bağlı': 'slskd · Soulseek connected',
    'Aranıyor ({n}/{total}): {query}': 'Searching ({n}/{total}): {query}',
    'Aranıyor · {artist} — {title} · "{query}"': 'Searching · {artist} — {title} · "{query}"',
    'Arama başlatılamadı (HTTP {status}); kısa süre sonra tekrar denenecek': 'Search could not start (HTTP {status}); retrying shortly',
    'Önceki sürümden kalan arama': 'Search left from the previous version',
    '{label}: {files} ses dosyası · {safe} güvenli eşleşme': '{label}: {files} audio files · {safe} safe matches',
    ' · sıradaki sorgu denenecek': ' · trying the next query',
    " · TXT'de ad kesilmiş; kesik kısım ön ek olarak eşleştirildi": ' · the name was cut off in the list; the cut part was matched as a prefix',
    'Daha iyi kaynak bulunamadı; mevcut dosya korundu · ': 'No better source found; the current file is kept · ',
    'Elle arama "{query}": {files} ses dosyası · {safe} güvenli eşleşme': 'Manual search "{query}": {files} audio files · {safe} safe matches',
    '{tried} kaynak denendi; yeni kaynaklar için yeniden aranacak': '{tried} sources tried; will search again for new ones',
    'Peer isteği · {title} · {peer} · {ext} · %{score}': 'Peer request · {title} · {peer} · {ext} · {score}%',
    'Peer yanıtı / indirme bekleniyor': 'Waiting for the peer / download',
    'Peer çevrimdışı': 'Peer offline',
    'slskd isteği reddetti (HTTP {status})': 'slskd rejected the request (HTTP {status})',
    'Peer isteği reddetti': 'The peer rejected the request',
    'Peer isteği kabul etmedi (boş yanıt)': 'The peer did not accept the request (empty reply)',
    'Transfer slskd kayıtlarında yok': 'Transfer missing from slskd',
    'Peer tamamlayamadı; diğer kaynaklar deneniyor': 'The peer could not finish; trying other sources',
    'Peer sonlandı · {title} · {peer} · {state}': 'Peer ended · {title} · {peer} · {state}',
    'İndirme {minutes} dakikadır ilerlemiyor': 'No download progress for {minutes} minutes',
    'İndirme durdu; başka kaynak deneniyor': 'Download stalled; trying another source',
    'Tamamlandı, fakat dosya bulunamadı': 'Finished, but the file was not found',
    'Tamamlandı; dosya aranıyor': 'Finished; locating the file',
    'Dosya doğrulanamadı': 'The file could not be verified',
    'Doğrulama başarısız: {message}': 'Verification failed: {message}',
    'Doğrulama başarısız · {title} · {message} · dosya silinmedi': 'Verification failed · {title} · {message} · file not deleted',
    'İnen dosya doğrulanamadı; başka kaynak deneniyor': 'The downloaded file could not be verified; trying another source',
    '{reason} · başka kaynak deneniyor': '{reason} · trying another source',
    '{title} · {reason} · dosya Quarantine klasöründe': '{title} · {reason} · file is in the Quarantine folder',
    'İnen dosya beklenenden kısa: {short}': 'The downloaded file is shorter than expected: {short}',
    "Daha iyi değil: yeni dosya da {khz} kHz'de kesiliyor": 'Not better: the new file is also cut off at {khz} kHz',
    "Şüpheli kalite: ses {khz} kHz'de kesiliyor (düşük bitrate MP3'ten dönüştürülmüş olabilir)":
        'Suspicious quality: the audio is cut off at {khz} kHz (may have been converted from a low-bitrate MP3)',
    'Dosya kopyası doğrulanamadı': 'The file copy could not be verified',
    'Tamamlandı · sıra etiketi: {position}': 'Done · track number tag: {position}',
    'Tamamlandı · sıra etiketi yazılamadı': 'Done · track number tag could not be written',
    ' · eski dosya Quarantine klasörüne taşındı': ' · the old file was moved to the Quarantine folder',
    'Tamamlandı · {artist} — {title}': 'Done · {artist} — {title}',
    'İndirmesiz arama testi tamamlandı.': 'Search-only test finished.',
    'Playlist tamamlandı.': 'Playlist finished.',
    'sıra': 'position',
    'sanatçı': 'artist',
    'şarkı': 'title',
    'durum': 'status',
    'dosya': 'file',
    'açıklama': 'details',
    'Kütüphanede bulundu · {artist} — {title} · {file}': 'Found in library · {artist} — {title} · {file}',
    'İş sürdürülüyor.': 'Job resumed.',
    'Yeni aramalar duraklatıldı. Mevcut transferler izleniyor.': 'New searches paused. Current transfers are still followed.',
    'İş iptal edildi; aktif istekler iptal edilecek. Dosyalar korunur.': 'Job cancelled; active requests will be cancelled. Files are kept.',
    "Tamamlanmayan parçalar yeniden aranacak; daha önce denenen peer'lar tekrar denenebilir.":
        'Unfinished tracks will be searched again; peers tried before may be tried again.',
    'Aranacak metni yazın.': 'Type something to search for.',
    'Bu parça için bir arama zaten sürüyor; birkaç saniye bekleyin.': 'A search for this track is already running; wait a few seconds.',
    'Elle aranıyor: {query}': 'Manual search: {query}',
    'Elle arama · {artist} — {title} · "{query}"': 'Manual search · {artist} — {title} · "{query}"',
    'Elle arama başlatılamadı; slskd bağlantısını kontrol edin': 'Manual search could not start; check the slskd connection',
    'Arama başlatılamadı; slskd bağlantısını kontrol edin.': 'Search could not start; check the slskd connection.',
    'Sanatçı ve şarkı adı boş olamaz.': 'Artist and title cannot be empty.',
    'Tamamlanan veya inen parçanın adı değiştirilemez.': 'A finished or downloading track cannot be renamed.',
    'Bu playlistte aynı parça zaten var.': 'This playlist already has that track.',
    'Ad düzeltildi; yeniden aranacak': 'Name fixed; will search again',
    'Ad düzeltildi · {old} → {new}': 'Name fixed · {old} → {new}',
    'Playlist adı boş olamaz.': 'The playlist name cannot be empty.',
    'Playlist yeniden adlandırıldı: {name}': 'Playlist renamed: {name}',
    'Aktif indirmesi olan playlistin sütun sırası değiştirilemez; önce duraklatıp bekleyin.':
        'The column order cannot change while the playlist is downloading; pause it and wait first.',
    'Liste yeniden okunamadı.': 'The list could not be read again.',
    'Sütun sırası değiştirildi; tamamlanmayan parçalar yeniden aranacak.': 'Column order changed; unfinished tracks will be searched again.',
    'Daha iyi kaynak aranıyor; mevcut dosya yerinde kalıyor': 'Looking for a better source; the current file stays in place',
    'Daha iyisi aranıyor · {artist} — {title}': 'Looking for better · {artist} — {title}',
    'Daha iyi kaynak aranacak tamamlanmış parça yok (aktif indirmesi olanlar atlanır).':
        'No finished track to look for a better source for (tracks being downloaded are skipped).',
    'Tamamlanan parça bu işlemle değiştirilemez.': 'A finished track cannot be changed this way.',
    'Bu parçanın aktif transferi var; transfer bittikten sonra seçebilirsiniz.': 'This track has an active transfer; choose after it ends.',
    'Tekrar işleme alındı': 'Taken up again',
    'Kullanıcı kararı; adaylar korundu': 'Your decision; candidates kept',
    'Atlandı (Pass)': 'Skipped (Pass)',
    'Seçim sonraya bırakıldı': 'Pick deferred',
    'Aday artık seçilemiyor; listeyi yenileyin.': 'That candidate can no longer be chosen; refresh the list.',
    'Bu parçanın aktif transferi var; seçim için tamamlanmasını bekleyin.': 'This track has an active transfer; wait for it to finish before choosing.',
    'Arama testi / iptal edilen iş için indirme seçilemez.': 'Downloads cannot be chosen for a search test or a cancelled job.',
    'Seçilen dosya bir ses dosyası değil.': 'The chosen file is not an audio file.',
    'Seçtiğiniz aday sıraya alındı; iş duraklatılmışsa sürdürün.': 'Your pick is queued; resume the job if it is paused.',
    'Kullanıcı seçimi · {title} · {peer}': 'Your pick · {title} · {peer}',
    'Sütun sırası otomatik düzeltildi ({changed} parça yeniden okunacak).': 'Column order fixed automatically ({changed} tracks will be read again).',
    'Yeni eşleştirme ile yeniden aranacak': 'Will be searched again with the new matching',
    'Yeniden doğrulandı': 'Verified again',
    # --- matching (stored with candidates)
    'Satır {number}: sanatçı ve şarkıyı boşluk-tire-boşluk ile ayırın.': 'Line {number}: separate artist and title with space-dash-space.',
    'TXT dosyasında parça bulunamadı.': 'No tracks found in the TXT file.',
    'CSV dosyasında parça bulunamadı.': 'No tracks found in the CSV file.',
    'CSV başlığında şarkı ve sanatçı sütunları bulunamadı (ör. "Track Name", "Artist Name(s)").':
        'The CSV header has no title and artist columns (e.g. "Track Name", "Artist Name(s)").',
    '{ext} boyutu şüpheli ({mb} MB)': 'Suspicious {ext} size ({mb} MB)',
    'MP3 bitrate bilinmiyor · indirince doğrulanır': 'MP3 bitrate unknown · checked after download',
    'MP3 {kbps} kbps (düşük)': 'MP3 {kbps} kbps (low)',
    '{ext} · yalnızca elle seçim': '{ext} · manual pick only',
    'Sürüm farklı': 'Different version',
    'Sürüm farklı: {version}': 'Different version: {version}',
    'Dosyada istenen sürüm yok: {version}': 'The file lacks the requested version: {version}',
    'Sanatçı dosya yolunda yok': 'Artist not in the file path',
    'Başlık benzerliği %{similarity}': 'Title similarity {similarity}%',
    'Eşleşme doğrulandı · {quality}': 'Match verified · {quality}',
    'Süre kısa ({actual} < {expected})': 'Too short ({actual} < {expected})',
    # --- store
    'CSV · süre bilgisiyle': 'CSV · with lengths',
    '{count} parça içe aktarıldı ({label}). Başlat düğmesiyle başlayın.': '{count} tracks imported ({label}). Press Start to begin.',
    'Sütun sırası düzeltildi; yeniden aranacak': 'Column order fixed; will search again',
    # --- OCR and audio
    'Windows metin tanıma çalıştırılamadı.': 'Windows text recognition could not run.',
    'Windows’ta metin tanıma dili yüklü değil (Ayarlar › Zaman ve dil › Dil).': 'No text recognition language is installed in Windows (Settings › Time & language › Language).',
    'Görüntü geçici dosyaya yazılamadı.': 'The image could not be written to a temporary file.',
    'Görüntü okunamadı.': 'The image could not be read.',
    'Dosya boyutu beklenen boyutla eşleşmiyor.': 'The file size does not match the expected size.',
    'Ses başlığı okunamadı.': 'The audio header could not be read.',
    'FLAC başlığı geçersiz.': 'Invalid FLAC header.',
    'Ses süresi okunamadı.': 'The audio length could not be read.',
    'MP3 gerçek bitrate {kbps} kbps; {minimum} beklendi.': 'Actual MP3 bitrate {kbps} kbps; {minimum} expected.',
    'AIFF dönüşümü başarısız.': 'AIFF conversion failed.',
    # --- slskd start-up
    'slskd başlatılıyor…': 'Starting slskd…',
    'slskd erişilemiyor · 5 sn sonra yeniden denenecek': 'slskd unreachable · retrying in 5 s',
    'slskd bulunamadı · config.json içindeki slskd_executable yolunu kontrol edin': 'slskd not found · check slskd_executable in config.json',
    'slskd çalışıyor · web servisinin hazır olması bekleniyor': 'slskd running · waiting for its web service',
    'Soulseek giriş portu geçersiz · Ayarlar bölümünü kontrol edin': 'Invalid Soulseek listen port · check Settings',
    'slskd ayar dosyası bulunamadı · slskd_config_path yolunu kontrol edin': 'slskd settings file not found · check slskd_config_path',
    'slskd başlatılıyor · kayıtlı hesapla bağlantı kuruluyor': 'Starting slskd · connecting with the saved account',
    'slskd başlatılamadı · 60 sn sonra yeniden denenecek': 'slskd could not start · retrying in 60 s',
    'slskd yanıt vermedi': 'slskd did not respond',
    'slskd oturum anahtarı alınamadı': 'Could not get an slskd session token',
    'slskd oturumu yenilenmeli': 'The slskd session must be renewed',
}
