"""Portable application binaries, persistent per-user settings, one-time setup."""
from __future__ import annotations

from i18n import LANGUAGES, tr
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QDialogButtonBox, QMessageBox, QComboBox, QCheckBox, QFormLayout, QFrame)


def data_root():
    if getattr(sys, 'frozen', False):
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SoulseekPlaylists'
    # Keep existing source installations and their in-flight transfers in place.
    return Path(__file__).resolve().parent


def defaults(root):
    local = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'slskd'
    executables = sorted((Path.home() / 'Downloads').glob('slskd*/slskd.exe'))
    config = {
        'slskd_url': 'http://localhost:5030',
        'slskd_executable': str(executables[-1]) if executables else '',
        'slskd_config_path': str(local / 'slskd.yml') if (local/'slskd.yml').is_file() else '',
        'slskd_download_dir': str(local / 'downloads'),
        'gui_playlist_output_dir': str(Path.home() / 'Music' / 'Soulseek Playlists'),
        'auto_start_slskd': True, 'prompt_for_web_credentials': False,
        'language': 'auto',
        # Search, peer and quality tuning defaults live in engine.DEFAULTS.
    }
    if config['slskd_config_path']:
        configured = yaml_download_directory(config['slskd_config_path'])
        if configured:
            config['slskd_download_dir'] = configured
    return config


def yaml_download_directory(path):
    """Only return an absolute directory, never a YAML error or credential."""
    try:
        document = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
        value = (document.get('directories') or {}).get('downloads')
        expanded = Path(os.path.expandvars(str(value))) if value else None
        return str(expanded) if expanded and expanded.is_absolute() else ''
    except Exception:
        return ''


def startup_script() -> Path:
    return (Path(os.environ.get('APPDATA', str(Path.home()))) / 'Microsoft' / 'Windows' / 'Start Menu'
            / 'Programs' / 'Startup' / 'Soulseek Playlists.vbs')


def set_autostart(enabled: bool) -> None:
    """Start minimised to the tray at Windows sign-in (a small script in the user's Startup folder)."""
    path = startup_script()
    if not enabled:
        if path.is_file():
            path.unlink()
        return
    if getattr(sys, 'frozen', False):
        command = f'""{sys.executable}"" --minimized'
    else:
        python = Path(sys.executable)
        pythonw = python.with_name('pythonw.exe') if python.with_name('pythonw.exe').is_file() else python
        command = f'""{pythonw}"" ""{Path(__file__).resolve().parent / "playlist_app.py"}"" --minimized'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'CreateObject("WScript.Shell").Run "{command}", 0, False\r\n', encoding='utf-8')


def load_settings(root):
    path = Path(root) / 'config.json'
    if path.exists():
        config = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(config, dict) or not isinstance(config.get('slskd_url'), str):
            raise ValueError(tr('Geçersiz yapılandırma'))
        # Preserve the historical GUI output folder; the legacy CLI uses a different key.
        config.setdefault('gui_playlist_output_dir', str(Path(root) / 'Playlists'))
        return config
    return defaults(root)


def save_settings(root, config):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='config-', suffix='.tmp', dir=root)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / 'config.json')
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


class ConnectionCheck(QThread):
    result = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

    def run(self):
        from slskd_api import SlskdApi
        from slskd_runtime import SlskdRuntime
        api = None
        try:
            ready, message = SlskdRuntime(self.config).prepare()
            if not ready:
                self.result.emit(message + tr(' · Biraz sonra yeniden kontrol edin.'))
                return
            api = SlskdApi(dict(self.config, prompt_for_web_credentials=False))
            api.authenticate()
            self.result.emit(tr('API hazır · Soulseek bağlı') if api.server_connected(api.server())
                             else tr('API hazır · Soulseek bağlantısı henüz kurulmadı'))
        except Exception:
            self.result.emit(tr('Bağlantı kurulamadı. Adresi ve slskd web erişim ayarlarını kontrol edin.'))
        finally:
            if api is not None:
                api.session.close()


class SetupDialog(QDialog):
    def __init__(self, root, config, parent=None):
        super().__init__(parent)
        self.root, self.config = Path(root), dict(config)
        if not self.config.get('slskd_executable'):
            # Older configs relied on auto-detection; show the detected path so saving keeps auto-start on.
            self.config['slskd_executable'] = defaults(root)['slskd_executable']
        config = self.config
        self.check = None
        self.setWindowTitle(tr('Soulseek Playlists · Ayarlar'))
        self.resize(760, 760)
        layout = QVBoxLayout(self)
        intro = QLabel(tr('Bir kez ayarlayın, sonraki açılışlarda hatırlansın.\n'
                          'slskd kendi Soulseek hesabınızla kurulmuş olmalı. Mevcut ayar dosyası değiştirilmez.'))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        link = QLabel(tr('<a href="https://github.com/slskd/slskd/releases">slskd indirme sayfası</a>'))
        link.setOpenExternalLinks(True)
        layout.addWidget(link)
        self.fields = {}
        for key, label, kind in [
            ('slskd_url', tr('slskd adresi'), None),
            ('slskd_executable', tr('slskd.exe (otomatik başlatmak için)'), 'exe'),
            ('slskd_config_path', tr('slskd.yml (kayıtlı web erişim ayarları)'), 'yaml'),
            ('slskd_listen_port', tr('Soulseek giriş portu (isteğe bağlı; boş: slskd ayarı)'), None),
            ('slskd_download_dir', tr('slskd tamamlanan indirmeler klasörü'), 'folder'),
            ('gui_playlist_output_dir', tr('Playlistlerin kaydedileceği klasör'), 'folder'),
        ]:
            layout.addWidget(QLabel(label))
            row = QHBoxLayout()
            field = QLineEdit(str(config.get(key, '')))
            self.fields[key] = field
            row.addWidget(field)
            if kind:
                button = QPushButton(tr('Seç…'))
                button.clicked.connect(lambda checked=False, k=key, t=kind: self.browse(k, t))
                row.addWidget(button)
            layout.addLayout(row)
        layout.addWidget(self.options_section(config))
        note = QLabel(tr('Parola bu uygulamada saklanmaz. Web erişimi seçtiğiniz slskd.yml dosyasından '
                         'veya SLSKD_API_KEY / SLSKD_WEB_USERNAME / SLSKD_WEB_PASSWORD ortam değişkenlerinden okunur.\n'
                         'Klasör değişikliği yalnızca yeni playlistler için geçerlidir.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.test_button = QPushButton(tr('Bağlantıyı kontrol et'))
        self.test_button.clicked.connect(self.test_connection)
        layout.addWidget(self.test_button)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr('Kaydet'))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('Vazgeç'))
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def options_section(self, config):
        from audio import find_ffmpeg
        frame = QFrame()
        form = QFormLayout(frame)
        form.setContentsMargins(0, 8, 0, 0)
        ffmpeg = bool(find_ffmpeg(str(config.get('ffmpeg_path') or '')))
        self.language = QComboBox()
        for code, label in LANGUAGES.items():
            self.language.addItem(label, code)
        self.language.setCurrentIndex(max(0, self.language.findData(config.get('language', 'auto'))))
        form.addRow('Dil / Language', self.language)      # readable whichever language is active
        self.output_format = QComboBox()
        self.output_format.addItem(tr('Orijinal format (FLAC / MP3 / AIFF / WAV)'), 'original')
        self.output_format.addItem(tr('FLAC ve WAV’ı AIFF’e çevir (rekordbox / CDJ)'), 'aiff')
        self.output_format.setCurrentIndex(max(0, self.output_format.findData(config.get('output_format', 'original'))))
        self.output_format.setEnabled(ffmpeg)
        form.addRow(tr('Dosya formatı'), self.output_format)
        self.filename_template = QComboBox()
        self.filename_template.addItem(tr('Peer’deki dosya adı'), 'original')
        self.filename_template.addItem(tr('Sanatçı - Şarkı (listedeki adla)'), 'artist_title')
        self.filename_template.setCurrentIndex(max(0, self.filename_template.findData(config.get('filename_template', 'original'))))
        form.addRow(tr('Dosya adı'), self.filename_template)
        self.check_fake = QCheckBox(tr('İnen FLAC/MP3’ün spektrumunu kontrol et (sahte kaliteyi ele)'))
        self.check_fake.setChecked(bool(config.get('check_fake_lossless', True)) and ffmpeg)
        self.check_fake.setEnabled(ffmpeg)
        form.addRow('', self.check_fake)
        self.rekordbox = QCheckBox(tr('Her playlist için rekordbox XML dosyası yaz'))
        self.rekordbox.setChecked(bool(config.get('rekordbox_xml', True)))
        form.addRow('', self.rekordbox)
        library = config.get('library_dirs') or []
        self.library = QLineEdit('; '.join(library) if isinstance(library, list) else str(library))
        self.library.setPlaceholderText(tr('Örn. D:\\Müzik; E:\\DJ — eşleşen dosya indirilmez, kopyalanır'))
        row = QHBoxLayout()
        row.addWidget(self.library)
        add = QPushButton(tr('Ekle…'))
        add.clicked.connect(self.add_library)
        row.addWidget(add)
        form.addRow(tr('Kütüphane klasörleri'), row)
        self.autostart = QCheckBox(tr('Windows açılışında tepside başlat'))
        self.autostart.setChecked(startup_script().is_file())
        form.addRow('', self.autostart)
        if not ffmpeg:
            form.addRow('', QLabel(tr('ffmpeg bulunamadı: AIFF dönüşümü ve spektrum kontrolü kapalı. '
                                      '(winget install Gyan.FFmpeg)')))
        return frame

    def add_library(self):
        folder = QFileDialog.getExistingDirectory(self, tr('Müzik klasörü seç'))
        if folder:
            current = [p.strip() for p in self.library.text().split(';') if p.strip()]
            if folder not in current:
                current.append(folder)
            self.library.setText('; '.join(current))

    def browse(self, key, kind):
        current = self.fields[key].text()
        if kind == 'folder':
            selected = QFileDialog.getExistingDirectory(self, tr('Klasör seç'), current)
        else:
            selected, _ = QFileDialog.getOpenFileName(self, tr('Dosya seç'), current,
                'slskd (slskd.exe)' if kind == 'exe' else 'YAML (*.yml *.yaml)')
        if selected:
            self.fields[key].setText(selected)
            if kind == 'yaml':
                # Read only the directory option. Never display credentials or parser errors.
                downloads = yaml_download_directory(selected)
                if downloads:
                    self.fields['slskd_download_dir'].setText(downloads)

    def values(self):
        config = dict(self.config)
        config.update({key: field.text().strip() for key, field in self.fields.items()})
        url = urlsplit(config['slskd_url'])
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError(tr('Geçerli bir port girin.'))
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError(tr('Geçerli bir HTTP(S) adresi girin; adrese parola veya erişim anahtarı eklemeyin.'))
        for key in ('slskd_executable', 'slskd_config_path'):
            value = os.path.expandvars(config[key])
            if value and (not Path(value).is_absolute() or not Path(value).is_file()):
                raise ValueError(tr('Seçilen slskd dosyası bulunamadı. Dosya yolunu kontrol edin.'))
        for key in ('slskd_download_dir', 'gui_playlist_output_dir'):
            if not config[key] or not Path(os.path.expandvars(config[key])).is_absolute():
                raise ValueError(tr('İndirme ve playlist klasörleri için tam bir klasör yolu seçin.'))
        config['setup_complete'] = True
        port = config.get('slskd_listen_port', '')
        if port:
            try:
                port = int(port)
                if not 1 <= port <= 65535:raise ValueError
            except (TypeError,ValueError):
                raise ValueError(tr('Soulseek giriş portu 1–65535 arasında olmalı.'))
            config['slskd_listen_port'] = port
        else:
            config.pop('slskd_listen_port', None)
        config['prompt_for_web_credentials'] = False
        config['auto_start_slskd'] = bool(config['slskd_executable'])
        config['output_format'] = self.output_format.currentData()
        config['filename_template'] = self.filename_template.currentData()
        config['check_fake_lossless'] = self.check_fake.isChecked()
        config['rekordbox_xml'] = self.rekordbox.isChecked()
        config['language'] = self.language.currentData()
        folders = [p.strip() for p in self.library.text().split(';') if p.strip()]
        if any(not Path(p).is_dir() for p in folders):
            raise ValueError(tr('Kütüphane klasörlerinden biri bulunamadı.'))
        config['library_dirs'] = folders
        return config

    def save(self):
        try:
            config = self.values()
            Path(config['gui_playlist_output_dir']).mkdir(parents=True, exist_ok=True)
            save_settings(self.root, config)
            set_autostart(self.autostart.isChecked())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        except OSError:
            self.status.setText(tr('Ayarlar kaydedilemedi. Dosya yollarını, adresi ve klasör yazma iznini kontrol edin.'))
            return
        self.config = config
        self.accept()

    def test_connection(self):
        try:
            config = self.values()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.test_button.setEnabled(False)
        self.buttons.setEnabled(False)
        self.status.setText(tr('Bağlantı kontrol ediliyor…'))
        self.check = ConnectionCheck(config, self)
        self.check.result.connect(self.status.setText)
        self.check.finished.connect(lambda: self.test_button.setEnabled(True))
        self.check.finished.connect(lambda: self.buttons.setEnabled(True))
        self.check.start()

    def reject(self):
        if self.check is None or not self.check.isRunning():
            super().reject()

    def closeEvent(self, event):
        if self.check is not None and self.check.isRunning():
            event.ignore()
            return
        super().closeEvent(event)
