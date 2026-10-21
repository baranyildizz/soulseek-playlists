"""Soulseek Playlists — native Windows UI, no browser/server required."""
from __future__ import annotations

import ctypes
import html
import json
import queue
import re
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QLockFile, QUrl, QSettings, QSize, QRectF
from PySide6.QtGui import (QColor, QDesktopServices, QIcon, QPainter, QPixmap, QPalette, QPen, QPainterPath,
                           QKeySequence, QShortcut, QTextDocument, QAbstractTextDocumentLayout, QImage)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QFileDialog, QDialog, QComboBox,
    QDialogButtonBox, QPlainTextEdit, QMessageBox, QProgressBar, QSystemTrayIcon, QMenu, QAbstractItemView,
    QFrame, QLineEdit, QSizePolicy, QSizeGrip, QToolButton, QButtonGroup, QCheckBox, QInputDialog,
    QStyledItemDelegate, QStyle, QFormLayout, QScrollArea,
)
from audio import audio_metadata, find_ffmpeg
from engine import Engine
from matching import (Candidate, Track, clock, detect_layout, mismatch_html, parse_tracks, quality_allowed,
                      quality_label, queries, tracklist_from_ocr)
from ocr import OcrError, recognize_image
from store import Store
from app_setup import data_root, load_settings, SetupDialog

ROOT = data_root()
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')
ACTIVE_ATTEMPTS = ('queued', 'downloading', 'finishing', 'submitting')
# Filter chips: key -> (label, track statuses)
FILTERS = [
    ('all', 'Tümü', None),
    ('done', 'Tamamlandı', {'done'}),
    ('active', 'İniyor', {'ready', 'waiting', 'downloading'}),
    ('searching', 'Aranıyor', {'pending', 'not_found', 'retry_wait'}),
    ('attention', 'Seçim bekliyor', {'review', 'deferred'}),
    ('suspicious', 'Şüpheli kalite', None),
    ('skipped', 'Atlandı', {'skipped'}),
]


def suspicious(t):
    return t['status'] == 'done' and 'şüpheli' in (t.get('quality_note') or '')


def matches_filter(key, statuses, t):
    if key == 'suspicious':
        return suspicious(t)
    return statuses is None or t['status'] in statuses
# status -> (light background, light text, dark background, dark text)
COLORS = {
    'done': ('#dff3e8', '#176442', '#26342c', '#91dfb3'),
    'downloading': ('#dceef8', '#185d7a', '#24313a', '#a8d8ee'),
    'waiting': ('#fff1cf', '#725500', '#37301e', '#e9cf87'),
    'ready': ('#e1f2ee', '#17665c', '#243331', '#9edfcf'),
    'searching': ('#e8eef9', '#34527a', '#283040', '#c4d4ee'),
    'review': ('#eee8f7', '#5f477b', '#302a3a', '#d8c0eb'),
    'deferred': ('#eee8f7', '#5f477b', '#302a3a', '#d8c0eb'),
    'other': ('#edf0f3', '#4f5f70', '#2e2e2e', '#cfcfcf'),
}
STYLE = '''
QWidget { background:#f4f6f9; color:#1c2938; font-family:"Segoe UI"; font-size:13px; }
QMainWindow { background:#f4f6f9; }
QLabel#title { font-size:20px; font-weight:700; }
QLabel#brand { font-size:17px; font-weight:700; }
QLabel#section { font-size:11px; font-weight:700; color:#627286; padding-top:6px; }
QLabel#detailTitle { font-size:16px; font-weight:700; }
QFrame#titleBar { background:transparent; }
QLabel#muted { color:#627286; }
QFrame#listPanel, QFrame#detailPanel { background:white; border:1px solid #dfe5ec; border-radius:6px; }
QFrame#listPanel QListWidget { border:0; border-radius:5px; }
QFrame#listPanel QLabel { background:transparent; padding:8px; border-top:1px solid #dfe5ec; }
QFrame#detailPanel QLabel, QFrame#detailPanel QCheckBox { background:transparent; }
QFrame#banner { background:#fff4d6; border:1px solid #e5c66f; border-radius:6px; }
QFrame#banner QLabel { background:transparent; color:#684d00; }
QPushButton { background:white; border:1px solid #ccd6e2; padding:6px 10px; border-radius:6px; }
QPushButton:hover { background:#e6eef4; }
QPushButton:disabled { color:#a1abba; }
QPushButton#primary { background:#126b64; color:white; border:0; font-weight:600; }
QPushButton#primary:hover { background:#0d5751; }
QPushButton#primary:disabled { background:#9fb8b5; color:white; }
QPushButton#chip { border:1px solid #dfe5ec; border-radius:13px; padding:4px 11px; background:white; }
QPushButton#chip:checked { background:#126b64; color:white; border-color:#126b64; font-weight:600; }
QPushButton#chipAttention { border:1px solid #e5c66f; border-radius:13px; padding:4px 11px; background:#fff4d6; color:#684d00; font-weight:600; }
QPushButton#chipAttention:checked { background:#d98d00; color:white; border-color:#d98d00; }
QToolButton { background:white; border:1px solid #ccd6e2; padding:5px 10px; border-radius:6px; }
QToolButton::menu-indicator { image:none; width:0; }
QPushButton#themeToggle { padding:0; min-width:30px; max-width:30px; min-height:30px; max-height:30px; border-radius:15px; }
QPushButton#windowControl { padding:0; min-width:34px; max-width:34px; min-height:30px; max-height:30px; border:0; border-radius:5px; background:transparent; }
QPushButton#windowControl:hover { background:#e6eef4; }
QPushButton#closeControl { padding:0; min-width:34px; max-width:34px; min-height:30px; max-height:30px; border:0; border-radius:5px; background:transparent; }
QPushButton#closeControl:hover { background:#c74343; }
QListWidget, QTableWidget, QPlainTextEdit { background:white; border:1px solid #dfe5ec; border-radius:6px; }
QListWidget::item { padding:10px 8px; margin:3px; border-radius:5px; }
QListWidget::item:selected { background:#e0f0ec; color:#134a45; }
QTableWidget { alternate-background-color:#f5f6f8; gridline-color:#edf0f3; selection-background-color:#e5e9ed; selection-color:#1c2938; }
QTableWidget::item { padding:5px; }
QTableWidget::item:selected { background:#e5e9ed; color:#1c2938; }
QHeaderView::section { background:#edf1f6; padding:7px; border:0; border-bottom:1px solid #dfe5ec; font-weight:600; }
QProgressBar { border:0; background:#e1e7ee; border-radius:2px; }
QProgressBar::chunk { background:#27998a; border-radius:2px; }
QLineEdit, QComboBox { background:white; padding:6px; border:1px solid #ccd6e2; border-radius:5px; }
QToolTip { background:#edf1f6; color:#1c2938; border:1px solid #ccd6e2; }
QAbstractScrollArea::corner { background:transparent; }
QScrollBar:vertical { background:transparent; width:10px; margin:2px; }
QScrollBar::handle:vertical { background:#bcc7d3; min-height:30px; border-radius:4px; }
QScrollBar::handle:vertical:hover { background:#91a2b5; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; border:0; background:transparent; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QScrollBar:horizontal { background:transparent; height:10px; margin:2px; }
QScrollBar::handle:horizontal { background:#bcc7d3; min-width:30px; border-radius:4px; }
QScrollBar::handle:horizontal:hover { background:#91a2b5; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; border:0; background:transparent; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:transparent; }
'''


def theme_style(dark):
    if not dark:
        return STYLE
    colors = {'#f4f6f9': '#141414', '#1c2938': '#e6e6e6', 'white': '#202020', '#627286': '#ababab',
              '#dfe5ec': '#383838', '#ccd6e2': '#484848', '#e6eef4': '#2d2d2d', '#a1abba': '#777777',
              '#e0f0ec': '#333333', '#134a45': '#f0f0f0', '#f5f6f8': '#252525', '#edf0f3': '#303030',
              '#e5e9ed': '#3a3a3a', '#edf1f6': '#292929', '#e1e7ee': '#333333', '#fff4d6': '#29261e',
              '#e5c66f': '#5a5034', '#684d00': '#ead18b', '#d98d00': '#b97900', '#bcc7d3': '#515151',
              '#91a2b5': '#707070', '#9fb8b5': '#355550'}
    return re.sub('|'.join(re.escape(k) for k in colors), lambda m: colors[m.group()], STYLE)


def human_wait(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f'{seconds // 3600} sa {seconds % 3600 // 60} dk'
    if seconds >= 60:
        return f'{seconds // 60} dk'
    return f'{seconds} sn'


def status_view(t, attempts, now):
    """(color key, human text) for a track row."""
    s = t['status']
    if t.get('search_id'):
        return 'searching', 'Aranıyor…'
    if t.get('upgrade') and s in ('pending', 'not_found', 'retry_wait', 'review', 'ready'):
        return 'searching', 'Daha iyisi aranıyor'
    if s == 'done':
        return 'done', 'Tamamlandı'
    if s == 'downloading':
        percent = max((a['bytes'] * 100 / max(1, a['size']) for a in attempts), default=0)
        return 'downloading', f'İniyor · %{percent:.0f}'
    if s == 'waiting':
        return 'waiting', 'Peer kuyruğunda'
    if s == 'ready':
        return 'ready', 'Kaynak bulundu'
    if s == 'review':
        return 'review', 'Seçim bekliyor'
    if s == 'deferred':
        return 'deferred', 'Sonraya bırakıldı'
    if s == 'skipped':
        return 'other', 'Atlandı'
    wait = (t.get('next_search') or 0) - now
    if s == 'pending':
        return 'other', 'Sırada' if wait <= 0 else f'Sırada · {human_wait(wait)}'
    label = 'Bulunamadı' if s == 'not_found' else 'Yeni kaynak aranacak'
    return 'other', label + (f' · {human_wait(wait)} sonra' if wait > 0 else '')


_DOTS = {}


def dot_icon(color):
    """Painted status dot; never depends on a font having a symbol glyph."""
    if color not in _DOTS:
        pix = QPixmap(12, 12)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, 8, 8)
        p.end()
        _DOTS[color] = QIcon(pix)
    return _DOTS[color]


def app_icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor('#126b64'))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 15, 15)
    p.setBrush(QColor('white'))
    for x, h in [(16, 16), (26, 34), (36, 24), (46, 42)]:
        p.drawRoundedRect(x, 32 - h // 2, 5, h, 2, 2)
    p.end()
    return QIcon(pix)


def ui_icon(kind, color):
    """Small vector icons remain centered and sharp at every Windows scale."""
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    if kind == 'minimize':
        p.drawLine(6, 13, 18, 13)
    elif kind == 'maximize':
        p.drawRoundedRect(6, 6, 12, 12, 1, 1)
    elif kind == 'restore':
        p.drawRoundedRect(8, 5, 11, 11, 1, 1)
        p.drawRoundedRect(5, 8, 11, 11, 1, 1)
    elif kind == 'close':
        p.drawLine(7, 7, 17, 17)
        p.drawLine(17, 7, 7, 17)
    elif kind == 'sun':
        p.drawEllipse(8, 8, 8, 8)
        for a, b, c, d in [(12, 4, 12, 6), (12, 18, 12, 20), (4, 12, 6, 12), (18, 12, 20, 12),
                           (6, 6, 8, 8), (16, 16, 18, 18), (18, 6, 16, 8), (8, 16, 6, 18)]:
            p.drawLine(a, b, c, d)
    elif kind == 'moon':
        path = QPainterPath()
        path.addEllipse(5, 4, 14, 16)
        cut = QPainterPath()
        cut.addEllipse(10, 2, 12, 14)
        p.fillPath(path.subtracted(cut), QColor(color))
    elif kind == 'more':
        p.setBrush(QColor(color))
        for x in (6, 12, 18):
            p.drawEllipse(x - 1, 11, 2, 2)
    p.end()
    return QIcon(pix)


class ElidedLabel(QLabel):
    """Single-line labels never force the layout off-screen."""
    def __init__(self, text=''):
        super().__init__(text)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight,
                                                       self.contentsRect().width()))


class HtmlDelegate(QStyledItemDelegate):
    """Renders Qt.UserRole HTML (highlighted file names) inside table cells."""
    def paint(self, painter, option, index):
        html = index.data(Qt.ItemDataRole.UserRole)
        if not html:
            return super().paint(painter, option, index)
        self.initStyleOption(option, index)
        option.text = ''
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setDocumentMargin(2)
        doc.setTextWidth(option.rect.width() - 6)
        color = option.palette.color(QPalette.ColorRole.Text).name()
        doc.setHtml(f'<span style="color:{color}">{html}</span>')
        painter.save()
        painter.translate(option.rect.left() + 3, option.rect.top() + max(0, (option.rect.height() - doc.size().height()) / 2))
        painter.setClipRect(QRectF(0, 0, option.rect.width() - 6, option.rect.height()))
        doc.documentLayout().draw(painter, QAbstractTextDocumentLayout.PaintContext())
        painter.restore()


class TitleBar(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('titleBar')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class Worker(QThread):
    snapshot = Signal(dict)
    notice = Signal(str)

    def __init__(self, root, config):
        super().__init__()
        self.root = root
        self.config = config
        self.commands = queue.Queue()
        self.keep_awake = False

    def stay_awake(self, active):
        """Keep Windows from sleeping while transfers are running."""
        if sys.platform != 'win32' or active == self.keep_awake:
            return
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if active else 0))
        self.keep_awake = active

    def run(self):
        store = Store(self.root, self.config.get('gui_playlist_output_dir'))
        engine = Engine(store, self.config)
        last = 0
        try:
            while not self.isInterruptionRequested():
                while not self.commands.empty():
                    action, payload = self.commands.get()
                    try:
                        engine.command(action, payload)
                    except Exception as e:
                        self.notice.emit(str(e) if isinstance(e, ValueError) else 'İşlem yapılamadı; dosya erişimini kontrol edin.')
                try:
                    engine.tick()
                except Exception as e:
                    # Do not expose raw credential-bearing server bodies or tracebacks.
                    store.set_setting('connection', 'İşlem hatası: ' + type(e).__name__ + ' · işler korunuyor')
                    self.msleep(2000)
                if time.time() - last > 1:
                    data = store.snapshot()
                    self.stay_awake(any(a['status'] in ACTIVE_ATTEMPTS for a in data['attempts']))
                    self.snapshot.emit(data)
                    last = time.time()
                self.msleep(300)
        finally:
            self.stay_awake(False)
            store.db.close()


class ImportDialog(QDialog):
    def __init__(self, path, parent=None, name=None):
        super().__init__(parent)
        self.path = Path(path)
        self.is_csv = self.path.suffix.lower() == '.csv'
        self.setWindowTitle('Playlist ekle')
        self.resize(720, 520)
        box = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(name or self.path.stem)
        form.addRow('Playlist adı', self.name)
        self.layout_choice = QComboBox()
        if self.is_csv:
            self.layout_choice.addItem('CSV sütunları (başlıktan okunur)', 'csv')
        else:
            self.layout_choice.addItem('Sanatçı - Şarkı', 'artist_title')
            self.layout_choice.addItem('Şarkı - Sanatçı', 'title_artist')
            try:
                detected = detect_layout(self.path)
            except (OSError, ValueError):
                detected = 'artist_title'
            index = self.layout_choice.findData(detected)
            self.layout_choice.setItemText(index, self.layout_choice.itemText(index) + '  (otomatik algılandı)')
            self.layout_choice.setCurrentIndex(index)
        form.addRow('Sütun sırası', self.layout_choice)
        box.addLayout(form)
        self.preview = QTableWidget(0, 3)
        self.preview.setHorizontalHeaderLabels(['Sanatçı', 'Şarkı', 'Süre'])
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.preview.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview.verticalHeader().hide()
        box.addWidget(self.preview)
        self.search_only = QCheckBox('Önce yalnızca ara — hiçbir dosya indirme (sonuçları görmek için)')
        box.addWidget(self.search_only)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        box.addWidget(self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Playlist oluştur')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Vazgeç')
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.layout_choice.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def payload(self):
        return {'source': str(self.path), 'layout': self.layout_choice.currentData(),
                'mode': 'preview' if self.search_only.isChecked() else 'fast', 'name': self.name.text().strip()}

    def refresh(self):
        try:
            tracks = parse_tracks(self.path, self.layout_choice.currentData())
            self.preview.setRowCount(min(len(tracks), 60))
            for i, t in enumerate(tracks[:60]):
                for col, value in enumerate([t.artist, t.title, clock(t.duration) if t.duration else '—']):
                    self.preview.setItem(i, col, QTableWidgetItem(value))
            timed = sum(1 for t in tracks if t.duration)
            extra = f' · {timed} parçada süre var; kısa sürümler (radio edit, önizleme) otomatik elenir.' if timed else ''
            self.summary.setText(f'{len(tracks)} parça{extra}\nOluşturduktan sonra Başlat’a basın.')
            self.ok.setEnabled(True)
        except Exception as e:
            self.summary.setText(str(e) if isinstance(e, ValueError) else 'Dosya UTF-8 metin olarak okunamadı.')
            self.ok.setEnabled(False)


class OcrThread(QThread):
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, image, recognize):
        super().__init__()
        self.image = image
        self.recognize = recognize

    def run(self):
        try:
            self.done.emit(tracklist_from_ocr(self.recognize(self.image)))
        except OcrError as e:
            self.failed.emit(str(e))
        except Exception:
            self.failed.emit('Ekran görüntüsü okunamadı.')


class OcrDialog(QDialog):
    """Screenshot → Windows OCR → editable "Artist - Title" lines → new playlist."""
    def __init__(self, image, parent=None, recognize=None):
        super().__init__(parent)
        self.setWindowTitle('Ekran görüntüsünden playlist')
        self.resize(1080, 640)
        box = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(time.strftime('Ekran görüntüsü %Y-%m-%d %H.%M'))
        form.addRow('Playlist adı', self.name)
        box.addLayout(form)
        row = QHBoxLayout()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        picture = QLabel()
        picture.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        pixmap = QPixmap.fromImage(image)
        picture.setPixmap(pixmap if pixmap.width() <= 520 else pixmap.scaledToWidth(520, Qt.TransformationMode.SmoothTransformation))
        scroll.setWidget(picture)
        row.addWidget(scroll, 1)
        right = QVBoxLayout()
        hint = QLabel('Her satır bir parça: <b>Sanatçı - Şarkı</b>. Metin tanıma “I/1”, “rn/m” gibi harfleri '
                      'karıştırabilir; aramadan önce adları göz ucuyla kontrol edin.')
        hint.setWordWrap(True)
        right.addWidget(hint)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText('Okunuyor…')
        self.text.setEnabled(False)
        self.text.textChanged.connect(self.validate)
        right.addWidget(self.text, 1)
        self.summary = QLabel('Windows metin tanıma çalışıyor…')
        self.summary.setWordWrap(True)
        right.addWidget(self.summary)
        row.addLayout(right, 1)
        box.addLayout(row, 1)
        self.search_only = QCheckBox('Önce yalnızca ara — hiçbir dosya indirme')
        box.addWidget(self.search_only)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Playlist oluştur')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Vazgeç')
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        if recognize is None:
            ffmpeg = find_ffmpeg(str(load_settings_safe(ROOT).get('ffmpeg_path') or ''))
            recognize = lambda img: recognize_image(img, ffmpeg)
        self.thread = OcrThread(image, recognize)
        self.thread.done.connect(self.loaded)
        self.thread.failed.connect(self.failed)
        self.thread.start()

    def loaded(self, lines):
        self.text.setEnabled(True)
        self.text.setPlainText('\n'.join(lines))
        self.text.setFocus()
        self.validate()

    def failed(self, message):
        self.text.setEnabled(True)
        self.summary.setText(message + ' Listeyi elle yazabilir veya yapıştırabilirsiniz.')

    def lines(self):
        return [line.strip() for line in self.text.toPlainText().splitlines() if line.strip()]

    def validate(self):
        lines = self.lines()
        bad = [i for i, line in enumerate(lines, 1) if not re.search(r'\s[-–—]\s', line)]
        if not lines:
            self.summary.setText('Parça bulunamadı.')
        elif bad:
            self.summary.setText(f'{len(lines)} satır · ayırıcısı olmayan satır: ' + ', '.join(map(str, bad[:12]))
                                 + ' (“Sanatçı - Şarkı” biçiminde düzeltin veya silin)')
        else:
            self.summary.setText(f'{len(lines)} parça hazır.')
        self.ok.setEnabled(bool(lines) and not bad)

    def done(self, result):
        if self.thread.isRunning():
            self.thread.wait(5000)
        super().done(result)


class DetailPanel(QFrame):
    """Everything about the selected track: why, candidates, manual search and choices."""
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setObjectName('detailPanel')
        self.track_id = None
        self.rows = []
        self.shown_state = None
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(6)
        self.title = QLabel('Bir parça seçin')
        self.title.setObjectName('detailTitle')
        self.title.setWordWrap(True)
        box.addWidget(self.title)
        self.status = QLabel('')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.status)
        row = QHBoxLayout()
        self.play = QPushButton('Dinle')
        self.play.clicked.connect(self.play_file)
        self.reveal = QPushButton('Klasörde göster')
        self.reveal.clicked.connect(self.reveal_file)
        self.rename = QPushButton('Adı düzelt…')
        self.rename.clicked.connect(self.edit_track)
        self.better = QPushButton('Daha iyisini bul')
        self.better.setToolTip('Daha iyi bir kaynak arar; doğrulanmış daha iyi dosya inene kadar mevcut dosya yerinde kalır')
        self.better.clicked.connect(lambda: self.window.send('upgrade_track', self.track_id))
        for b in (self.play, self.reveal, self.better, self.rename):
            row.addWidget(b)
        row.addStretch()
        box.addLayout(row)

        box.addWidget(self.section('ELLE ARA'))
        search = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('Soulseek’te aranacak sözcükler')
        self.query.returnPressed.connect(self.manual_search)
        self.search_button = QPushButton('Ara')
        self.search_button.clicked.connect(self.manual_search)
        search.addWidget(self.query, 1)
        search.addWidget(self.search_button)
        box.addLayout(search)

        box.addWidget(self.section('ADAYLAR'))
        # One cell per candidate: full file name (differing version in red) over a grey detail line.
        self.table = QTableWidget(0, 1)
        self.table.setItemDelegateForColumn(0, HtmlDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(58)
        self.table.setShowGrid(False)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.cellDoubleClicked.connect(lambda *_: self.approve())
        box.addWidget(self.table, 3)
        self.reason = QLabel('')
        self.reason.setWordWrap(True)
        self.reason.setObjectName('muted')
        box.addWidget(self.reason)
        actions = QHBoxLayout()
        self.download = QPushButton('Bunu indir')
        self.download.setObjectName('primary')
        self.download.clicked.connect(self.approve)
        self.skip = QPushButton('Atla')
        self.skip.clicked.connect(lambda: self.choice('skip_track'))
        self.later = QPushButton('Sonraya bırak')
        self.later.clicked.connect(lambda: self.choice('defer_track'))
        self.restore = QPushButton('Geri al')
        self.restore.clicked.connect(lambda: self.choice('restore_track'))
        for b in (self.download, self.skip, self.later, self.restore):
            actions.addWidget(b)
        box.addLayout(actions)

        box.addWidget(self.section('DENEMELER VE GEÇMİŞ'))
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        box.addWidget(self.history, 2)
        self.show_track(None)

    @staticmethod
    def section(text):
        label = QLabel(text)
        label.setObjectName('section')
        return label

    def track(self):
        return next((t for t in self.window.data.get('tracks', []) if t['id'] == self.track_id), None)

    def show_track(self, track_id):
        changed = track_id != self.track_id
        self.track_id = track_id
        t = self.track()
        for widget in (self.play, self.reveal, self.better, self.rename, self.query, self.search_button, self.table,
                       self.download, self.skip, self.later, self.restore, self.history):
            widget.setVisible(t is not None)
        if t is None:
            self.title.setText('Bir parça seçin')
            self.status.setText('Durumunu, adaylarını ve geçmişini burada görürsünüz. '
                                'Bulunamayan parçayı elle arayabilir veya adını düzeltebilirsiniz.')
            self.reason.setText('')
            return
        if changed:
            plan = queries(Track(t['artist'], t['title']))
            self.query.setText(t.get('query') or (plan[0] if plan else ''))
        self.refresh(force=changed)

    def refresh(self, force=False):
        t = self.track()
        if t is None:
            return self.show_track(None)
        job = next((j for j in self.window.data.get('jobs', []) if j['id'] == t['job']), {})
        attempts = [a for a in self.window.data.get('attempts', []) if a['track'] == t['id']]
        _key, text = status_view(t, [a for a in attempts if a['status'] in ACTIVE_ATTEMPTS], time.time())
        self.title.setText(f"{t['artist']} — {t['title']}")
        duration = f" · {clock(t['duration'])}" if t.get('duration') else ''
        note = f" · spektrum {t['quality_note']}" if t.get('quality_note') not in (None, '', '—') else ''
        self.status.setText(f"{job.get('name', '')} #{t['position']}{duration} · {text}{note}\n{t['detail']}")
        done = t['status'] == 'done'
        has_file = bool(t['path']) and (done or bool(t.get('upgrade')))
        active = any(a['status'] in ACTIVE_ATTEMPTS for a in attempts)
        self.play.setVisible(has_file)
        self.reveal.setVisible(has_file)
        self.better.setVisible(done)
        self.better.setEnabled(self.window.worker is not None)
        self.better.setObjectName('primary' if suspicious(t) else '')
        self.better.style().unpolish(self.better)
        self.better.style().polish(self.better)
        self.rename.setVisible(not done and not t.get('upgrade'))
        self.rename.setEnabled(not active)
        self.search_button.setEnabled(not done and not t.get('search_id'))
        held = t['status'] in ('skipped', 'deferred')
        self.skip.setVisible(not done and not held)
        self.later.setVisible(not done and not held)
        self.restore.setVisible(held)
        for b in (self.skip, self.later, self.restore):
            b.setEnabled(not active and self.window.worker is not None)
        # Rebuild the candidate list only when something about this track changed.
        state = (t['id'], t['status'], t['detail'], len(attempts), t.get('search_id'))
        if force or state != self.shown_state:
            self.shown_state = state
            self.load_candidates(t)
            self.load_history(t, attempts)
        self.selection_changed()

    def load_candidates(self, t):
        selected = self.rows[self.table.currentRow()]['id'] if 0 <= self.table.currentRow() < len(self.rows) else None
        self.rows = self.window.reader().rows(
            'SELECT * FROM candidates WHERE track=? ORDER BY eligible DESC, score DESC LIMIT 60', (t['id'],))
        track = Track(t['artist'], t['title'], t['position'], t.get('duration'))
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            c = json.loads(r['data'])
            name = Path(c['filename'].replace('\\', '/')).name
            recommended = i == 0 and r['eligible']
            slot = 'boş slot' if c.get('free_upload_slot') else f"kuyruk {c.get('queue_length', '?')}"
            size = f" · {c['size'] / 1048576:.0f} MB" if c.get('size') else ''
            length = f" · {clock(c['length'])}" if c.get('length') else ''
            meta = f"{quality_label(c)}{size}{length} · {c['peer']} · {slot} · puan {c['score']:.0f}"
            badge = '<b style="color:#1f8f6a">★ Önerilen</b> · ' if recommended else ''
            item = QTableWidgetItem(name)
            item.setToolTip(c['filename'] + '\n' + c.get('reason', ''))
            item.setData(Qt.ItemDataRole.UserRole, mismatch_html(track, c['filename'])
                         + f'<br><span style="color:#8a96a5; font-size:11px">{badge}{html.escape(meta)}</span>')
            self.table.setItem(i, 0, item)
            if r['id'] == selected:
                self.table.selectRow(i)
        if self.rows and self.table.currentRow() < 0:
            self.table.selectRow(0)

    def load_history(self, t, attempts):
        lines = []
        for a in attempts:
            stamp = time.strftime('%d.%m %H:%M', time.localtime(a['created']))
            label = {'succeeded': 'tamamlandı', 'failed': 'başarısız', 'cancelled': 'iptal',
                     'queued': 'kuyrukta', 'downloading': 'iniyor', 'submitting': 'gönderiliyor',
                     'finishing': 'doğrulanıyor'}.get(a['status'], a['status'])
            lines.append(f"{stamp}  {a['peer']} · {label} · {a['detail']}")
        marker = f"{t['artist']} — {t['title']}"
        for e in self.window.reader().rows(
                "SELECT created, message FROM events WHERE job=? AND (message LIKE ? OR message LIKE ?) "
                "ORDER BY id DESC LIMIT 25", (t['job'], f'%{marker}%', f"%· {t['title']} ·%")):
            lines.append(time.strftime('%d.%m %H:%M', time.localtime(e['created'])) + '  ' + e['message'])
        self.history.setPlainText('\n'.join(lines) or 'Henüz deneme yok.')

    def selection_changed(self):
        row = self.table.currentRow()
        t = self.track()
        if not (t and 0 <= row < len(self.rows)):
            self.download.setEnabled(False)
            self.reason.setText('Henüz aday yok. Elle arayabilir veya adı düzeltebilirsiniz.' if t and t['status'] != 'done' else '')
            return
        c = json.loads(self.rows[row]['data'])
        job = next((j for j in self.window.data.get('jobs', []) if j['id'] == t['job']), {})
        active = any(a['track'] == t['id'] and a['status'] in ACTIVE_ATTEMPTS for a in self.window.data.get('attempts', []))
        allowed = quality_allowed(Candidate.from_dict(c), manual=True)
        self.download.setVisible(t['status'] != 'done')
        self.download.setEnabled(bool(allowed and not active and self.window.worker and job.get('mode') != 'preview'
                                      and job.get('status') != 'cancelled'))
        self.reason.setText(c.get('reason', '') + ('' if not active else ' · Aktif indirme sürüyor.'))

    def approve(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.rows) and self.download.isEnabled():
            self.window.send('approve', {'track': self.track_id, 'candidate': self.rows[row]['id']})

    def choice(self, action):
        if self.track_id:
            self.window.send(action, self.track_id)

    def manual_search(self):
        if self.track_id and self.query.text().strip():
            self.window.send('manual_search', {'track': self.track_id, 'query': self.query.text()})

    def edit_track(self):
        t = self.track()
        if not t:
            return
        text, ok = QInputDialog.getText(self, 'Adı düzelt', 'Sanatçı - Şarkı', text=f"{t['artist']} - {t['title']}")
        if not ok:
            return
        parts = re.split(r'\s+[-–—]\s+', text.strip(), maxsplit=1)
        if len(parts) != 2:
            QMessageBox.warning(self, 'Adı düzelt', 'Sanatçı ve şarkıyı boşluk-tire-boşluk ile ayırın.')
            return
        self.window.send('edit_track', {'track': t['id'], 'artist': parts[0], 'title': parts[1]})

    def play_file(self):
        t = self.track()
        if t and t['path']:
            QDesktopServices.openUrl(QUrl.fromLocalFile(t['path']))

    def reveal_file(self):
        t = self.track()
        if t and t['path']:
            reveal_in_explorer(t['path'])


def reveal_in_explorer(path):
    if sys.platform == 'win32':
        subprocess.Popen(['explorer', '/select,', str(Path(path))])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))


class Window(QMainWindow):
    def __init__(self, root=ROOT, worker=None):
        super().__init__()
        self.root = Path(root)
        self.worker = worker
        self.data = {}
        self.job_id = None
        self.track_ids = []
        self.quitting = False
        self.received_at = 0
        self.filter_key = 'all'
        self.share_dismissed = False
        self.audio_metadata_cache = {}
        self.next_notice = 0
        self.last_log_id = None
        self.job_states = {}
        self._reader = None
        self.preferences = QSettings(str(self.root / 'ui.ini'), QSettings.Format.IniFormat)
        self.dark = self.preferences.value('theme', 'light') == 'dark'
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle('Soulseek Playlists')
        self.setWindowIcon(app_icon())
        self.resize(1360, 820)
        self.setMinimumSize(1040, 620)
        self.setAcceptDrops(True)
        container = QWidget()
        self.setCentralWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 10, 14, 8)
        layout.setSpacing(8)

        # Title bar
        self.title_bar = TitleBar(self)
        head = QHBoxLayout(self.title_bar)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(7)
        brand = QLabel('Soulseek Playlists')
        brand.setObjectName('brand')
        head.addWidget(brand)
        self.connection = ElidedLabel('Bağlanıyor…')
        self.connection.setObjectName('muted')
        head.addWidget(self.connection, 1)
        settings_button = QPushButton('Ayarlar')
        settings_button.clicked.connect(self.open_settings)
        head.addWidget(settings_button)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName('themeToggle')
        self.theme_button.clicked.connect(self.toggle_theme)
        head.addWidget(self.theme_button)
        self.minimize_button = self.window_button('minimize', 'Küçült', self.showMinimized)
        self.maximize_button = self.window_button('maximize', 'Büyüt', self.toggle_maximize)
        self.close_button = self.window_button('close', 'Tepsiye küçült', self.close, True)
        for b in (self.minimize_button, self.maximize_button, self.close_button):
            head.addWidget(b)
        layout.addWidget(self.title_bar)

        # Share warning
        self.banner = QFrame()
        self.banner.setObjectName('banner')
        banner = QHBoxLayout(self.banner)
        banner.setContentsMargins(10, 6, 6, 6)
        banner_text = QLabel('slskd’de paylaşılan klasör yok. Birçok Soulseek kullanıcısı hiç paylaşımı olmayanlara '
                             'dosya vermiyor (“Rejected”). slskd ayarlarından bir müzik klasörü paylaşmanız önerilir.')
        banner_text.setWordWrap(True)
        banner.addWidget(banner_text, 1)
        open_slskd = QPushButton('slskd’yi aç')
        open_slskd.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(str(load_settings_safe(self.root).get(
            'slskd_url', 'http://localhost:5030')))))
        banner.addWidget(open_slskd)
        dismiss = QPushButton('Kapat')
        dismiss.clicked.connect(self.dismiss_banner)
        banner.addWidget(dismiss)
        self.banner.hide()
        layout.addWidget(self.banner)

        splitter = QSplitter()
        layout.addWidget(splitter, 1)

        # Left: playlists
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 10, 0)
        left_head = QHBoxLayout()
        left_head.addWidget(QLabel('PLAYLISTLER'))
        left_head.addStretch()
        add = QPushButton('+ Ekle')
        add.setToolTip('TXT, CSV veya ekran görüntüsü ekleyin, pencereye sürükleyin ya da Ctrl+V ile yapıştırın')
        add.setObjectName('primary')
        add.clicked.connect(self.choose)
        left_head.addWidget(add)
        lv.addLayout(left_head)
        panel = QFrame()
        panel.setObjectName('listPanel')
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        self.jobs = QListWidget()
        self.jobs.currentItemChanged.connect(self.select_job)
        self.jobs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.jobs.customContextMenuRequested.connect(
            lambda pos: self.job_menu().exec(self.jobs.viewport().mapToGlobal(pos)) if self.jobs.itemAt(pos) else None)
        panel_layout.addWidget(self.jobs, 1)
        hint = QLabel('TXT, CSV veya ekran görüntüsü bırakın; ya da Ctrl+V ile yapıştırın.')
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setObjectName('muted')
        panel_layout.addWidget(hint)
        lv.addWidget(panel, 1)
        splitter.addWidget(left)

        # Middle: playlist
        middle = QWidget()
        mv = QVBoxLayout(middle)
        mv.setContentsMargins(0, 0, 10, 0)
        job_head = QHBoxLayout()
        self.job_title = QLabel('Bir playlist ekleyin')
        self.job_title.setObjectName('title')
        job_head.addWidget(self.job_title)
        self.path_label = ElidedLabel('')
        self.path_label.setObjectName('muted')
        job_head.addWidget(self.path_label, 1)
        self.toggle_button = QPushButton('Başlat')
        self.toggle_button.setObjectName('primary')
        self.toggle_button.clicked.connect(self.toggle_job)
        job_head.addWidget(self.toggle_button)
        self.retry_button = QPushButton('Tekrar ara')
        self.retry_button.setToolTip('Bulunamayanları bütün sorgularla hemen yeniden arar')
        self.retry_button.clicked.connect(lambda: self.action('retry'))
        job_head.addWidget(self.retry_button)
        self.more_button = QToolButton()
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setToolTip('Diğer işlemler')
        job_head.addWidget(self.more_button)
        mv.addLayout(job_head)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        self.chips = {}
        for key, label, _statuses in FILTERS:
            chip = QPushButton(label)
            chip.setObjectName('chipAttention' if key in ('attention', 'suspicious') else 'chip')
            chip.setCheckable(True)
            chip.clicked.connect(lambda checked=False, k=key: self.set_filter(k))
            self.chip_group.addButton(chip)
            chips.addWidget(chip)
            self.chips[key] = chip
        self.chips['all'].setChecked(True)
        chips.addStretch()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('Sanatçı veya şarkı ara…')
        self.filter.setMinimumWidth(150)
        self.filter.setMaximumWidth(260)
        self.filter.textChanged.connect(self.render_tracks)
        chips.addWidget(self.filter)
        mv.addLayout(chips)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        mv.addWidget(self.progress)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Durum', '#', 'Sanatçı', 'Şarkı', 'Kalite', 'Süre'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in enumerate([165, 34, 130, 200, 125, 48]):
            self.table.setColumnWidth(col, width)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.track_selected)
        self.table.cellDoubleClicked.connect(self.play_row)
        mv.addWidget(self.table, 1)
        splitter.addWidget(middle)

        # Right: details
        self.detail = DetailPanel(self)
        splitter.addWidget(self.detail)
        splitter.setSizes([190, 740, 430])
        splitter.setStretchFactor(1, 1)

        # Footer
        self.log_dialog = QDialog(self)
        self.log_dialog.setWindowTitle('Canlı günlük · tüm playlistler')
        self.log_dialog.resize(920, 500)
        log_layout = QVBoxLayout(self.log_dialog)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(5000)
        log_layout.addWidget(self.logs)
        bottom = QHBoxLayout()
        self.latest_event = ElidedLabel('Henüz işlem yok')
        self.latest_event.setObjectName('muted')
        bottom.addWidget(self.latest_event, 1)
        self.footer = QLabel()
        self.footer.setObjectName('muted')
        bottom.addWidget(self.footer)
        log_button = QPushButton('Günlük')
        log_button.clicked.connect(self.open_logs)
        bottom.addWidget(log_button)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(15, 15)
        bottom.addWidget(self.size_grip)
        layout.addLayout(bottom)

        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip('Soulseek Playlists')
        menu = QMenu()
        menu.addAction('Uygulamayı göster').triggered.connect(self.reveal)
        menu.addAction('Tamamen çık').triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda why: self.reveal() if why in (
            QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.messageClicked.connect(lambda: (self.reveal(), self.set_filter('attention')))
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        if worker:
            worker.snapshot.connect(self.receive)
            worker.notice.connect(lambda msg: QMessageBox.warning(self, 'İşlem bilgisi', msg))
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self.paste_list)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self.table, activated=lambda: self.detail.play_file())
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.update_clock)
        self.clock.start(1000)
        self.apply_theme()
        self.select_job()

    # ---------------------------------------------------------------- chrome
    def window_button(self, kind, tooltip, callback, close=False):
        button = QPushButton()
        button.setObjectName('closeControl' if close else 'windowControl')
        button.setProperty('iconKind', kind)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.clicked.connect(callback)
        return button

    def update_window_icons(self):
        color = '#e6e6e6' if self.dark else '#344050'
        self.theme_button.setIcon(ui_icon('sun' if self.dark else 'moon', color))
        self.theme_button.setIconSize(QSize(18, 18))
        self.minimize_button.setIcon(ui_icon('minimize', color))
        self.minimize_button.setIconSize(QSize(18, 18))
        self.maximize_button.setIcon(ui_icon('restore' if self.isMaximized() else 'maximize', color))
        self.maximize_button.setToolTip('Geri al' if self.isMaximized() else 'Büyüt')
        self.maximize_button.setIconSize(QSize(18, 18))
        self.close_button.setIcon(ui_icon('close', color))
        self.close_button.setIconSize(QSize(18, 18))
        self.more_button.setIcon(ui_icon('more', color))

    def toggle_maximize(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()
        QTimer.singleShot(0, self.update_window_icons)

    def apply_theme(self):
        app = QApplication.instance()
        palette = app.style().standardPalette()
        if self.dark:
            for role, color in {'Window': '#141414', 'WindowText': '#e6e6e6', 'Base': '#202020',
                                'AlternateBase': '#252525', 'Text': '#e6e6e6', 'Button': '#202020',
                                'ButtonText': '#e6e6e6', 'Highlight': '#3a3a3a', 'HighlightedText': '#ffffff',
                                'ToolTipBase': '#292929', 'ToolTipText': '#e6e6e6'}.items():
                palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
        app.setPalette(palette)
        app.setStyleSheet(theme_style(self.dark))
        self.theme_button.setToolTip('Açık moda geç' if self.dark else 'Koyu moda geç')
        self.update_window_icons()
        self.render_tracks()

    def toggle_theme(self):
        self.dark = not self.dark
        self.preferences.setValue('theme', 'dark' if self.dark else 'light')
        self.preferences.sync()
        self.apply_theme()

    def nativeEvent(self, event_type, message):
        if sys.platform == 'win32':
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084 and not self.isMaximized():  # WM_NCHITTEST: resize from the frameless edge
                bounds = wintypes.RECT()
                user32 = ctypes.windll.user32
                user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                if user32.GetWindowRect(msg.hWnd, ctypes.byref(bounds)):
                    x = ctypes.c_short(msg.lParam & 0xffff).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xffff).value
                    border = max(7, round(8 * user32.GetDpiForWindow(msg.hWnd) / 96))
                    left = bounds.left <= x < bounds.left + border
                    right = bounds.right - border <= x < bounds.right
                    top = bounds.top <= y < bounds.top + border
                    bottom = bounds.bottom - border <= y < bounds.bottom
                    for hit, code in ((top and left, 13), (top and right, 14), (bottom and left, 16),
                                      (bottom and right, 17), (left, 10), (right, 11), (top, 12), (bottom, 15)):
                        if hit:
                            return True, code
        return super().nativeEvent(event_type, message)

    # ---------------------------------------------------------------- data access
    def reader(self):
        """Read-only view for candidates and history (the worker owns all writes)."""
        if self._reader is None:
            self._reader = Store(self.root)
        return self._reader

    def send(self, action, payload):
        if self.worker:
            self.worker.commands.put((action, payload))

    def current_job(self):
        return next((j for j in self.data.get('jobs', []) if j['id'] == self.job_id), None)

    # ---------------------------------------------------------------- import
    def open_settings(self):
        try:
            dialog = SetupDialog(self.root, load_settings(self.root), self)
        except (OSError, ValueError):
            QMessageBox.warning(self, 'Ayarlar', 'Ayar dosyası okunamadı; mevcut dosya korunuyor.')
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(self, 'Ayarlar kaydedildi', 'Ayarlar bir sonraki açılışta uygulanacak.\n'
                                    'Tepsi menüsünden Tamamen çık seçip uygulamayı yeniden açın.')

    def choose(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Playlist seç', str(self.root),
                                                'Şarkı listesi veya ekran görüntüsü (*.txt *.csv *.png *.jpg *.jpeg *.bmp *.webp)')
        for path in paths:
            self.import_any(path)

    def import_path(self, path, name=None):
        dialog = ImportDialog(path, self, name)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.send('import', dialog.payload())

    def import_screenshot(self, image):
        dialog = OcrDialog(image, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            folder = self.root / 'imports'
            folder.mkdir(exist_ok=True)
            path = folder / f'ocr-{int(time.time())}.txt'
            path.write_text('\n'.join(dialog.lines()) + '\n', encoding='utf-8')
            self.send('import', {'source': str(path), 'layout': 'auto', 'name': dialog.name.text().strip(),
                                 'mode': 'preview' if dialog.search_only.isChecked() else 'fast'})

    def paste_list(self):
        if QApplication.focusWidget() and isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit)):
            return
        mime = QApplication.clipboard().mimeData()
        if mime is not None and mime.hasImage():
            image = QApplication.clipboard().image()
            if not image.isNull():
                self.import_screenshot(image)
                return
        text = QApplication.clipboard().text().strip()
        lines = tracklist_from_ocr(text.splitlines())    # also turns "Title by Artist" around
        if not any(re.search(r'\s[-–—]\s', line) for line in lines):
            QMessageBox.information(self, 'Yapıştır', 'Panoda "Sanatçı - Şarkı" satırları veya ekran görüntüsü bulunamadı.')
            return
        text = '\n'.join(lines)
        folder = self.root / 'imports'
        folder.mkdir(exist_ok=True)
        name = time.strftime('Pano %Y-%m-%d %H.%M')
        path = folder / f'paste-{int(time.time())}.txt'
        path.write_text(text + '\n', encoding='utf-8')
        self.import_path(path, name)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(
                u.isLocalFile() and u.toLocalFile().lower().endswith(('.txt', '.csv') + IMAGE_SUFFIXES)
                for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.import_any(url.toLocalFile())

    def import_any(self, path):
        if path.lower().endswith(IMAGE_SUFFIXES):
            self.import_screenshot(QImage(path))
        elif path.lower().endswith(('.txt', '.csv')):
            self.import_path(path)

    # ---------------------------------------------------------------- playlist actions
    def job_menu(self):
        job = self.current_job()
        menu = QMenu(self)
        if not job:
            return menu
        folder = Path(job['folder'])
        menu.addAction('Klasörü aç').triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
        xml = next(folder.glob('*.rekordbox.xml'), None) if folder.is_dir() else None
        action = menu.addAction('rekordbox XML’ini göster')
        action.setEnabled(bool(xml))
        action.triggered.connect(lambda: reveal_in_explorer(xml))
        action = menu.addAction('Raporu aç (rapor.csv)')
        action.setEnabled((folder / 'rapor.csv').is_file())
        action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder / 'rapor.csv'))))
        count = sum(1 for t in self.data.get('tracks', []) if t['job'] == job['id'] and suspicious(t))
        action = menu.addAction(f'Şüpheli dosyalar için daha iyisini bul ({count})')
        action.setEnabled(count > 0)
        action.triggered.connect(lambda: self.send('upgrade_suspicious', job['id']))
        menu.addSeparator()
        menu.addAction('Yeniden adlandır…').triggered.connect(self.rename_job)
        if job.get('layout') in ('artist_title', 'title_artist'):
            other = 'title_artist' if job['layout'] == 'artist_title' else 'artist_title'
            label = 'Şarkı - Sanatçı' if other == 'title_artist' else 'Sanatçı - Şarkı'
            menu.addAction(f'Sütun sırasını değiştir ({label})').triggered.connect(
                lambda: self.send('relayout', {'job': job['id'], 'layout': other}))
        menu.addSeparator()
        menu.addAction('İşi iptal et').triggered.connect(lambda: self.action('cancel'))
        menu.addAction('Listeden kaldır…').triggered.connect(self.remove_job)
        return menu

    def rename_job(self):
        job = self.current_job()
        if job:
            name, ok = QInputDialog.getText(self, 'Yeniden adlandır', 'Playlist adı', text=job['name'])
            if ok and name.strip():
                self.send('rename_job', {'job': job['id'], 'name': name})

    def remove_job(self):
        job = self.current_job()
        if job and QMessageBox.question(self, 'Listeden kaldır', f"“{job['name']}” listeden kaldırılsın mı?\n"
                                        'İndirilmiş dosyalar ve klasör silinmez; aktif istekler iptal edilir.'
                                        ) == QMessageBox.StandardButton.Yes:
            self.send('remove_job', job['id'])

    def toggle_job(self):
        job = self.current_job()
        if job:
            self.action('pause' if job['status'] == 'active' else 'start')

    def action(self, action):
        if not self.job_id:
            return
        if action == 'cancel' and QMessageBox.question(
                self, 'İşi iptal et', 'Bu playlist’in indirme istekleri iptal edilsin mi? Tamamlanan dosyalar korunacak.'
        ) != QMessageBox.StandardButton.Yes:
            return
        self.send(action, self.job_id)

    # ---------------------------------------------------------------- snapshot → widgets
    def receive(self, data):
        self.data = data
        self.received_at = time.time()
        selected = self.job_id
        self.jobs.blockSignals(True)
        self.jobs.clear()
        for job in data['jobs']:
            tracks = [t for t in data['tracks'] if t['job'] == job['id']]
            done = sum(t['status'] == 'done' for t in tracks)
            attention = sum(t['status'] == 'review' for t in tracks)
            state = {'active': 'çalışıyor', 'paused': 'duraklatıldı', 'completed': 'tamamlandı', 'cancelled': 'iptal',
                     'preview_complete': 'arama testi bitti'}.get(job['status'], job['status'])
            item = QListWidgetItem(f"{job['name']}\n{done}/{len(tracks)} · {state}" + (f' · {attention} seçim' if attention else ''))
            item.setData(Qt.ItemDataRole.UserRole, job['id'])
            self.jobs.addItem(item)
            if job['id'] == selected:
                self.jobs.setCurrentItem(item)
            if self.job_states.get(job['id']) == 'active' and job['status'] == 'completed':
                self.tray.showMessage('Playlist tamamlandı', f"{job['name']} · {done} parça hazır.",
                                      QSystemTrayIcon.MessageIcon.Information, 8000)
            self.job_states[job['id']] = job['status']
        if not self.jobs.currentItem() and self.jobs.count():
            self.jobs.setCurrentRow(0)
        self.jobs.blockSignals(False)
        self.select_job()
        self.update_clock()
        self.banner.setVisible(bool(data.get('share_warning')) and not self.share_dismissed)
        reviews = [t for t in data['tracks'] if t['status'] == 'review']
        new = [t for t in reviews if not self.preferences.value(f"notified/{t['id']}/review", False, type=bool)]
        if new and time.time() >= self.next_notice:
            self.next_notice = time.time() + 60
            for t in new:
                self.preferences.setValue(f"notified/{t['id']}/review", True)
            self.preferences.sync()
            self.tray.showMessage('Seçim bekleyen şarkılar', f'{len(reviews)} parça için yalnızca benzer sürümler bulundu. '
                                  'Seçmek için tıklayın.', QSystemTrayIcon.MessageIcon.Information, 10000)
        if data.get('events'):
            event = data['events'][0]
            name = next((j['name'] for j in data['jobs'] if j['id'] == event['job']), 'Uygulama')
            self.latest_event.setText(time.strftime('%H:%M:%S', time.localtime(event['created'])) + ' · ' + name + ' · ' + event['message'])
            if self.log_dialog.isVisible() and self.last_log_id != event['id']:
                self.open_logs(reveal=False)
            self.last_log_id = event['id']
        self.detail.refresh()

    def dismiss_banner(self):
        self.share_dismissed = True
        self.banner.hide()

    def select_job(self, *_):
        item = self.jobs.currentItem()
        self.job_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        job = self.current_job()
        for widget in (self.toggle_button, self.more_button, self.retry_button):
            widget.setEnabled(bool(job))
        self.more_button.setMenu(self.job_menu())
        if not job:
            self.job_title.setText('Bir playlist ekleyin')
            self.path_label.setText('TXT / CSV ekleyin veya Ctrl+V ile şarkı listesini yapıştırın.')
            self.retry_button.hide()
            self.render_tracks()
            return
        self.job_title.setText(job['name'])
        self.path_label.setText(job['folder'])
        running = job['status'] == 'active'
        self.toggle_button.setText('Duraklat' if running else 'Başlat')
        self.toggle_button.setToolTip('Yeni arama ve indirmeleri durdurur; başlamış transferler izlenmeye devam eder'
                                      if running else 'Arama ve indirmeyi başlatır')
        tracks = [t for t in self.data.get('tracks', []) if t['job'] == self.job_id]
        counts = {key: sum(matches_filter(key, statuses, t) for t in tracks) for key, _label, statuses in FILTERS}
        for key, label, _statuses in FILTERS:
            self.chips[key].setText(f'{label} {counts[key]}')
            self.chips[key].setVisible(key in ('all', 'done') or counts[key] > 0 or self.filter_key == key)
        unresolved = sum(t['status'] in ('not_found', 'review', 'retry_wait') for t in tracks)
        self.retry_button.setVisible(unresolved > 0)
        done = counts['done'] + counts['skipped']
        self.progress.setMaximum(max(1, len(tracks)))
        self.progress.setValue(done)
        self.progress.setToolTip(f"{counts['done']} tamamlandı · {counts['skipped']} atlandı · {len(tracks)} parça")
        self.render_tracks()

    def set_filter(self, key):
        self.filter_key = key
        self.chips[key].setChecked(True)
        self.select_job()

    def visible_tracks(self):
        statuses = next(s for k, _l, s in FILTERS if k == self.filter_key)
        term = self.filter.text().casefold()
        return [t for t in self.data.get('tracks', []) if t['job'] == self.job_id
                and matches_filter(self.filter_key, statuses, t)
                and term in (t['artist'] + ' ' + t['title']).casefold()]

    def render_tracks(self):
        tracks = self.visible_tracks()
        selected = self.detail.track_id
        scroll = self.table.verticalScrollBar().value()
        self.table.blockSignals(True)
        if selected not in {t['id'] for t in tracks}:
            self.table.clearSelection()        # the highlighted row must match the details panel
        self.table.setRowCount(len(tracks))
        self.track_ids = []
        now = time.time()
        for i, t in enumerate(tracks):
            self.track_ids.append(t['id'])
            attempts = [a for a in self.data.get('attempts', []) if a['track'] == t['id'] and a['status'] in ACTIVE_ATTEMPTS]
            key, text = status_view(t, attempts, now)
            meta = self.track_metadata(t)
            if meta:
                quality = quality_label(meta)
            else:
                quality = ' / '.join(dict.fromkeys(a.get('quality', '—') for a in attempts)) or '—'
            note = t.get('quality_note') or ''
            if 'şüpheli' in note:
                quality += ' · şüpheli (' + note.split(' ')[0] + ' kHz)'
            length = meta.get('length') if meta else t.get('duration')
            values = [text, str(t['position']), t['artist'], t['title'], quality, clock(length) if length else '—']
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(t['detail'] if c == 0 else value)
                if c == 0:
                    light_bg, light_fg, dark_bg, dark_fg = COLORS.get(key, COLORS['other'])
                    item.setBackground(QColor(dark_bg if self.dark else light_bg))
                    item.setForeground(QColor(dark_fg if self.dark else light_fg))
                    item.setIcon(dot_icon(dark_fg if self.dark else light_fg))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if c == 4 and 'şüpheli' in note:
                    item.setForeground(QColor('#e0a33a' if self.dark else '#a35d00'))
                    item.setToolTip('Spektrum ' + note + ': MP3’ten dönüştürülmüş olabilir.')
                if c == 5 and not meta and t.get('duration'):
                    item.setForeground(QColor('#8a96a5'))
                    item.setToolTip('Listedeki süre')
                self.table.setItem(i, c, item)
            if t['id'] == selected:
                self.table.selectRow(i)
        self.table.blockSignals(False)
        self.table.verticalScrollBar().setValue(scroll)

    def track_metadata(self, t):
        if not t['path'] or not (t['status'] == 'done' or t.get('upgrade')):
            return {}
        try:
            stamp = Path(t['path']).stat().st_mtime_ns
            cached = self.audio_metadata_cache.get(t['path'])
            if not cached or cached[0] != stamp:
                self.audio_metadata_cache[t['path']] = (stamp, audio_metadata(t['path']))
            return self.audio_metadata_cache[t['path']][1]
        except Exception:
            return {'extension': Path(t['path']).suffix[1:]}

    def track_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.track_ids):
            self.detail.show_track(self.track_ids[row])

    def play_row(self, row, _column):
        if 0 <= row < len(self.track_ids):
            self.detail.show_track(self.track_ids[row])
            self.detail.play_file()

    def update_clock(self):
        self.connection.setText(self.data.get('connection', 'Bağlanıyor…'))
        tracks = self.data.get('tracks', [])
        searching = sum(1 for t in tracks if t.get('search_id'))
        transfers = sum(1 for a in self.data.get('attempts', []) if a['status'] in ACTIVE_ATTEMPTS)
        library = self.data.get('library_files', 0)
        parts = [f'{searching} arama', f'{transfers} transfer']
        if library:
            parts.append(f'kütüphane {library:,} dosya'.replace(',', '.'))
        self.footer.setText(' · '.join(parts))
        updated = time.strftime('%H:%M:%S', time.localtime(self.received_at)) if self.received_at else 'bekleniyor'
        self.footer.setToolTip(f'Son motor güncellemesi: {updated}')

    def open_logs(self, *_, reveal=True):
        events = self.reader().rows('SELECT e.*, j.name FROM events e LEFT JOIN jobs j ON j.id=e.job ORDER BY e.id')
        self.logs.setPlainText('\n'.join(time.strftime('%H:%M:%S', time.localtime(e['created'])) + '  ['
                                         + str(e['name'] or 'Uygulama') + '] ' + e['message'] for e in events))
        self.logs.verticalScrollBar().setValue(self.logs.verticalScrollBar().maximum())
        if reveal:
            self.log_dialog.show()
            self.log_dialog.raise_()

    # ---------------------------------------------------------------- window lifecycle
    def reveal(self):
        self.showNormal()
        # Recover windows left on a disconnected monitor as well as hidden launches.
        screens = QApplication.screens()
        frame = self.frameGeometry()
        if screens and not any(s.availableGeometry().contains(frame.center()) for s in screens):
            area = QApplication.primaryScreen().availableGeometry()
            self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
            frame = self.frameGeometry()
            frame.moveCenter(area.center())
            self.move(frame.topLeft())
        if sys.platform == 'win32':
            # STARTUPINFO/SW_HIDE can hide the native window while Qt thinks it is visible.
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            handle = int(self.winId())
            user32.ShowWindow(handle, 9)  # SW_RESTORE
            user32.ShowWindow(handle, 5)  # SW_SHOW
            user32.SetForegroundWindow(handle)
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange and hasattr(self, 'maximize_button'):
            self.size_grip.setVisible(not self.isMaximized())
            QTimer.singleShot(0, self.update_window_icons)

    def closeEvent(self, event):
        if not self.quitting and self.tray.isVisible():
            self.hide()
            event.ignore()
            self.tray.showMessage('Soulseek Playlists', 'Arka planda çalışıyor. Tepsi simgesinden tekrar açabilirsiniz.')
            return
        if not self.quitting:
            event.ignore()
            self.quit_app()
            return
        if self._reader is not None:
            self._reader.db.close()
            self._reader = None
        event.accept()

    def quit_app(self):
        if self.quitting:
            return
        self.quitting = True
        self.footer.setText('İşler kaydediliyor…')
        self.setEnabled(False)
        if self.worker:
            self.worker.requestInterruption()
            self.worker.finished.connect(QApplication.instance().quit)
            if not self.worker.isRunning():
                QApplication.instance().quit()
        else:
            QApplication.instance().quit()


def load_settings_safe(root):
    try:
        return load_settings(root)
    except (OSError, ValueError):
        return {}


def main():
    root = ROOT
    app = QApplication(sys.argv)
    app.setApplicationName('Soulseek Playlists')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    if '--package-check' in sys.argv:
        from package_check import run
        return run(app, sys.argv[sys.argv.index('--package-check') + 1])
    if '--connection-check' in sys.argv:
        from package_check import connection_check
        pos = sys.argv.index('--connection-check')
        return connection_check(sys.argv[pos + 1], sys.argv[pos + 2])
    if '--delivery-check' in sys.argv:
        from package_check import delivery_check
        pos = sys.argv.index('--delivery-check')
        return delivery_check(sys.argv[pos + 1], sys.argv[pos + 2])
    if '--lifecycle-check' in sys.argv:
        from package_lifecycle import attach
        pos = sys.argv.index('--lifecycle-check')
        phase, folder, output = sys.argv[pos + 1:pos + 4]
        if phase not in ('first', 'restart'):
            return 2
        root = Path(folder).resolve()
        lifecycle_timer = attach(app, root, phase, output)  # noqa: F841 (kept alive)
    root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(root / 'playlist-app.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        # Ask the existing instance to reveal itself on the next UI tick.
        (root / 'show-window.signal').touch()
        return 0
    try:
        config = load_settings(root)
    except (OSError, ValueError):
        QMessageBox.warning(None, 'Ayarlar okunamadı', 'config.json okunamadı. Dosya korunuyor; ayar dosyasını kontrol edin.')
        return 1
    if not (root / 'config.json').exists() or (getattr(sys, 'frozen', False) and not config.get('setup_complete')):
        setup = SetupDialog(root, config)
        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0
        config = setup.config
    worker = Worker(root, config)
    window = Window(root, worker)
    timer = QTimer(window)

    def reveal_requested():
        signal = root / 'show-window.signal'
        if signal.exists():
            signal.unlink()
            window.reveal()
    timer.timeout.connect(reveal_requested)
    timer.start(700)
    worker.start()
    if '--minimized' not in sys.argv:
        window.show()
        QTimer.singleShot(0, window.reveal)
    result = app.exec()
    worker.requestInterruption()
    worker.wait()
    lock.unlock()
    return result


if __name__ == '__main__':
    raise SystemExit(main())
