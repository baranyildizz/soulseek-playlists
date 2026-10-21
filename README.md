# Soulseek Playlists

<p align="center">
<img width="100%" alt="Soulseek Playlists workflow" src="https://github.com/user-attachments/assets/f6e1bb98-cfb2-4aa5-9dd1-f3dcba48e50b" />
.txt / .csv / screenshot   ->   app   ->   flac, aiff, mp3
</p>

A Windows desktop app that takes a track list — a TXT file, a Spotify/Exportify CSV, pasted text or a **screenshot of a tracklist** — searches Soulseek for it through [slskd](https://github.com/slskd/slskd), picks the right version, downloads the tracks in order and collects every list in its own DJ-ready folder. Only use it for content you have the right to download.

The interface is available in **English and Turkish** (follows the Windows display language; changeable in Settings). [Türkçe açıklama aşağıda.](#türkçe)

## Getting started

Download the Windows package from [GitHub Releases](https://github.com/baranyildizz/soulseek-playlists/releases/latest). Unzip it and keep `Soulseek Playlists.exe` together with the `_internal` folder next to it. On first start, choose slskd, its settings file, the folder where slskd puts finished downloads, and the folder for your playlists. More detail: [PAYLASIM.md](PAYLASIM.md) (Turkish).

This beta is not code-signed, so Windows may show a security warning. Try it first with a 1–3 track list you are allowed to download.

[slskd](https://github.com/slskd/slskd) is installed separately and set up with your own [Soulseek](https://www.slsknet.org/news/node/680) account. Share at least one folder in slskd: many Soulseek users refuse uploads to people who share nothing.

## What it does

- **Finds the right version.** Artist, title and version are checked separately. “Original Mix”, “Extended Mix” and a file without a version tag count as the same; a Club Mix, Dub, Edit, Live or another remixer is never downloaded automatically but offered for you to pick. Names cut off with “…” in the list still match.
- **Searches the way Soulseek works.** Queries stay plain (no version words, apostrophe words split like Soulseek does, accented and unaccented spellings). Three searches run at once; missing tracks are searched again at growing intervals.
- **Chooses a good source.** Reliable peers with a free upload slot first, then FLAC > AIFF/WAV > MP3 320. Offline, refusing or stalled peers are replaced by the next one.
- **Checks quality.** Downloads are verified and the real MP3 bitrate is checked. With ffmpeg installed, the spectrum is scanned for the lowpass “wall” of an MP3 encoder, so FLACs converted from low-bitrate MP3s are rejected. **Find better** looks for a better source for a finished suspect file while keeping the current one.
- **Screenshot to playlist.** Paste a tracklist screenshot with Ctrl+V; Windows’ built-in text recognition reads it, drops timestamps, turns “Title by Artist” around and opens an editable preview before anything is searched.
- **Length check from CSV.** With the track lengths in a Spotify/Exportify CSV, previews, cut files and radio edits are not downloaded automatically.
- **Builds a DJ folder.** Each playlist gets its own folder with the audio files, an `.m3u8`, `rapor.csv` and a **rekordbox XML**. The list order is written to the `tracknumber` tag. Optionally FLAC/WAV are converted to **AIFF** and files are named “Artist - Title”.
- **Uses your library.** Tracks already on your computer are copied instead of downloaded.
- **Clean interface.** Filter buttons, a details panel (candidates with differing versions highlighted, manual search, name fixing), tray mode, notifications, dark theme.

## Running from source

Install the dependencies with `pip install -r requirements-gui.txt` on Python 3.10+ and run `python playlist_app.py`. [ffmpeg](https://ffmpeg.org/) (e.g. `winget install Gyan.FFmpeg`) is recommended for the spectrum check and AIFF conversion; without it these two features are off.

| File | Purpose |
|---|---|
| `matching.py` | List parsing, column detection, query plan, scoring, quality |
| `slskd_api.py` | slskd REST client |
| `audio.py` | Audio checks, tags, spectrum, AIFF conversion |
| `ocr.py` | Text from screenshots (Windows OCR) |
| `store.py` | SQLite schema and records |
| `engine.py` | Searching, peer choice, transfer tracking, delivery |
| `i18n.py` | Turkish / English interface text |
| `playlist_app.py`, `app_setup.py` | Qt interface and settings |

Tests: `python -m unittest test_playlist test_slskd_runtime test_distribution test_i18n` and `python gui_smoke.py`. A download-free search against a running slskd: `python live_search_check.py "Artist - Title"`. To build the Windows package, use `requirements-build.txt` and `python build_release.py`.

A macOS version is not available yet; starting slskd, paths and packaging need platform-specific work.

This repository contains no personal settings, account data, music, playlists or download history. The source code is under the [MIT license](LICENSE). Licenses of third-party dependencies ship with the release package.

---

## Türkçe

Şarkı listelerini (TXT, Spotify/Exportify CSV, panodan yapıştırılan metin veya **tracklist ekran görüntüsü**) [slskd](https://github.com/slskd/slskd) üzerinden Soulseek'te arayan, doğru sürümü bulup sırayla indiren ve her listeyi DJ kullanımına hazır ayrı bir klasörde toplayan Windows masaüstü uygulaması. Yalnızca indirme hakkınız olan içerikler için kullanın. Arayüz Türkçe ve İngilizcedir; Windows dilini izler, Ayarlar'dan değiştirilebilir.

**Kurulum:** [Releases](https://github.com/baranyildizz/soulseek-playlists/releases/latest) bölümünden Windows paketini indirin, ZIP'i açın ve `Soulseek Playlists.exe` ile yanındaki `_internal` klasörünü birlikte tutun. İlk açılışta slskd programını, ayar dosyasını, tamamlanan indirmeler klasörünü ve playlist klasörünü seçin. Ayrıntılar: [PAYLASIM.md](PAYLASIM.md) ve [GUI-KULLANIM.md](GUI-KULLANIM.md). slskd ayrıca kurulur; birçok kullanıcı paylaşımı olmayanlara dosya vermediği için slskd'de bir klasör paylaşmanız önerilir.

**Öne çıkanlar:**

- Sanatçı, şarkı adı ve sürüm ayrı ayrı doğrulanır; Original/Extended Mix aynı sayılır, farklı remix/dub/edit/live seçiminize bırakılır.
- Soulseek'e uygun sade sorgular, aynı anda 3 arama, bulunamayanlar için otomatik yeniden arama.
- Güvenilir ve boş slotu olan peer önce; FLAC > AIFF/WAV > MP3 320.
- Sahte FLAC tespiti (ffmpeg ile) ve **Daha iyisini bul**.
- Ekran görüntüsünden liste (Ctrl+V), CSV ile süre doğrulaması.
- Her playlist için klasör, `.m3u8`, `rapor.csv`, rekordbox XML; isteğe bağlı AIFF dönüşümü ve “Sanatçı - Şarkı” dosya adı.
- Bilgisayarınızda olan şarkılar indirilmez, kopyalanır.

**Kaynaktan çalıştırma:** Python 3.10+ ile `pip install -r requirements-gui.txt`, ardından `python playlist_app.py`. Spektrum kontrolü ve AIFF için ffmpeg önerilir. Testler ve paketleme yukarıdaki İngilizce bölümde.
