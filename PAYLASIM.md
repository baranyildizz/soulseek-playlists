# Soulseek Playlists · Windows x64

ZIP'i bir klasöre çıkartın ve **Soulseek Playlists.exe** dosyasına çift tıklayın.
Python kurmanız gerekmez. EXE ile yanındaki `_internal` klasörü birlikte kalmalıdır.
Bu paket Windows 11 x64 üzerinde doğrulanmıştır.

İlk açılışta slskd.exe, slskd.yml, slskd'nin tamamlanan indirmeler klasörü ve
playlistlerin kaydedileceği klasörü seçin. Ayarlar hatırlanır; her açılışta sorulmaz.
Sonradan üstteki **Ayarlar** düğmesiyle değiştirilebilir; değişiklik yeniden açınca uygulanır.

slskd ayrı kurulur: https://github.com/slskd/slskd/releases
Kendi Soulseek hesabınızı slskd'nin kendi ayarlarında tanımlayın.
Uygulama mevcut slskd.yml dosyasını değiştirmez; kayıtlı web erişim ayarlarını okur.
Gerekirse SLSKD_API_KEY veya SLSKD_WEB_USERNAME / SLSKD_WEB_PASSWORD ortam değişkenleri kullanılabilir.
API erişimi için slskd web kimlik bilgileri kullanılır; Soulseek parolası bu uygulamaya girilmez.
İlk kurulumda **Bağlantıyı kontrol et** ile durumu görün. slskd yeni başlatılıyorsa birkaç saniye sonra tekrar deneyin.
API hazır ama Soulseek bağlı değilse slskd'nin kendi hesap/bağlantı ayarlarını kontrol edin.
slskd dinleme portu Windows tarafından ayrılmışsa Ayarlar'daki isteğe bağlı
Soulseek giriş portunu boş bir portla değiştirin. Bu ayar slskd.yml dosyasını değiştirmez;
yalnızca uygulamanın slskd'yi başlatırken verdiği portu değiştirir.

**+ TXT** ile liste ekleyin veya sürükleyin. Önce önizleme modunda küçük bir liste deneyin.
Her playlist, TXT adındaki ayrı klasöre kaydedilir. Dosya adına sıra numarası eklenmez;
sıra, ses dosyasının tracknumber etiketine yazılır. Uygulamayı kapatıp açınca işler korunur.
Pencereyi kapatmak tepsiye küçültür. Tamamen durdurmak için tepsi menüsünden **Tamamen çık** seçin.

Ayarlar ve iş geçmişi `%LOCALAPPDATA%\SoulseekPlaylists` altında tutulur.
Program klasörünü güncellerken bu veri klasörünü silmeyin. Eski C:\SoulseekAuto kurulumu
kendi verileriyle çalışmaya devam eder; paylaşım EXE'si onun işlerini otomatik kopyalamaz.
Aynı listeyi iki uygulamada başlatmayın.

Bu paket kişisel hesap, playlist, indirme veya geçmiş içermez. slskd pakete dahil değildir.
Yalnızca indirme hakkınız olan içerikleri kullanın.

## Geliştirici / Mac'e aktarım

Windows paketini üretmek için Python 3.10+ ortamında requirements-build.txt kurun ve
`python build_release.py` çalıştırın. Paket yalnızca açıkça listelenen kaynak dosyalarından üretilir.
`python -m unittest test_playlist test_slskd_runtime test_distribution` iş mantığını sınar.
EXE içinde `--package-check sonuc.json` ayar, arayüz ve yeniden açma testlerini geçici verilerle yapar;
ağa bağlanmaz veya indirme başlatmaz.
Paketleme ayrıca uygulamayı iki ayrı süreçte açar: ilk kurulumda ayarları kaydeder,
ikinci açılışta kurulum sorulmadığını, temanın ve playlistin korunduğunu doğrular.
Kullanılan bağımlılık sürümleri versions.json dosyasında listelenir.
Derleme kendi temiz PATH ortamını kullanır; başka uygulamalardaki aynı adlı DLL'leri toplamaz.

Mac sürümü henüz üretilmedi. Aynı Python/Qt kaynakları başlangıç noktasıdır.
Mac üzerinde slskd başlatma ve veri yolları uyarlanmalı; paket o sistemde üretilip
tray, açılış, klasör izinleri ve yeniden başlatma testleri yapılmalıdır.

Paket üçüncü taraf bileşenlerin lisanslarını LICENSES klasöründe taşır.
Kaynak kod Soulseek-Playlists-Source.zip ile birlikte paylaşılabilir.
Bu imzasız bir arkadaşlar-arası test sürümüdür. Başka bir fiziksel Windows bilgisayarda
ilk kullanımda 1–3 parçalık bir listeyle bağlantı ve gerçek indirme kontrolü yapın.
Bu bilgisayarda Windows Sandbox/sanallaştırma etkin olmadığı için o ortam doğrulanmadı.
