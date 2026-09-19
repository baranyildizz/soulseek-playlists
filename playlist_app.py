"""Soulseek Playlists — native Windows UI, no browser/server required."""
from __future__ import annotations

import json
import queue
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QLockFile, QUrl, QSettings, QSize
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap, QAction, QPalette, QPen, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QSplitter, QFileDialog, QDialog, QComboBox, QDialogButtonBox,
    QPlainTextEdit, QMessageBox, QProgressBar, QSystemTrayIcon, QMenu,
    QAbstractItemView, QFrame, QLineEdit, QSizePolicy, QSizeGrip,
)
from playlist_core import Store, Engine, parse_tracks, quality_label, quality_allowed, Candidate, audio_metadata
from app_setup import data_root, load_settings, SetupDialog

ROOT=data_root()
REVIEW_STATES={'review','retry_wait','not_found','failed'}
LABELS={'pending':'Sırada','ready':'Eşleşti','retry_wait':'Yeni kaynak bekliyor','waiting':'Peer bekleniyor','downloading':'İndiriliyor',
        'skipped':'Atlandı','deferred':'Sonraya bırakıldı',
        'done':'Tamamlandı','review':'İnceleme','not_found':'Tekrar aranacak','active':'Çalışıyor',
        'paused':'Duraklatıldı','cancelled':'İptal','completed':'Tamamlandı','preview_complete':'Test tamamlandı',
        'submitting':'İstek gönderiliyor','queued':'Kuyrukta','finishing':'Dosya doğrulanıyor',
        'failed':'Başarısız','succeeded':'Tamamlandı'}
STATUS_PRESENTATION={
    'skipped': ('—  Atlandı', '#edf0f3', '#4f5f70'),
    'deferred': ('◷  Sonraya bırakıldı', '#eee8f7', '#5f477b'),
    'searching': ('●  Aranıyor', '#e8eef9', '#34527a'),
    'pending': ('•  Sırada', '#eef1f4', '#4f5f70'),
    'ready': ('✓  Eşleşti', '#e1f2ee', '#17665c'),
    'retry_wait': ('↻  Yeni kaynak bekliyor', '#edf0f3', '#4f5f70'),
    'waiting': ('◷  Peer bekleniyor', '#fff1cf', '#725500'),
    'downloading': ('↓  İndiriliyor', '#dceef8', '#185d7a'),
    'done': ('✓  Tamamlandı', '#dff3e8', '#176442'),
    'review': ('!  İnceleme', '#eee8f7', '#5f477b'),
    'not_found': ('↻  Tekrar aranacak', '#edf0f3', '#4f5f70'),
    'failed': ('!  Başarısız', '#e9edf1', '#3f4d5b'),
}
STYLE='''
QWidget { background:#f4f6f9; color:#1c2938; font-family:"Segoe UI"; font-size:13px; }
QMainWindow { background:#f4f6f9; }
QLabel#title { font-size:20px; font-weight:700; }
QLabel#brand { font-size:17px; font-weight:700; }
QFrame#titleBar { background:transparent; }
QLabel#muted { color:#627286; }
QLabel#cardValue { font-size:20px; font-weight:700; color:#174c60; }
QFrame#card { background:white; border:1px solid #dfe5ec; border-radius:10px; }
QFrame#card QLabel { background:transparent; }
QFrame#listPanel { background:white; border:1px solid #dfe5ec; border-radius:6px; }
QFrame#listPanel QListWidget { border:0; border-radius:5px; }
QFrame#listPanel QLabel { background:transparent; padding:8px; border-top:1px solid #dfe5ec; }
QFrame#attentionBar { background:#fff4d6; border:1px solid #e5c66f; border-radius:6px; }
QPushButton#attentionAction { background:transparent; color:#684d00; border:0; font-weight:700; text-align:left; }
QPushButton#attentionAction:hover { background:#f7e8bc; }
QPushButton#attentionCount { background:#d98d00; color:white; border:0; border-radius:11px; font-weight:800; padding:3px 8px; min-width:22px; }
QPushButton { background:white; border:1px solid #ccd6e2; padding:6px 10px; border-radius:6px; }
QPushButton:hover { background:#e6eef4; }
QPushButton:disabled { color:#a1abba; }
QPushButton#primary { background:#126b64; color:white; border:0; font-weight:600; }
QPushButton#primary:hover { background:#0d5751; }
QPushButton#themeToggle { padding:0; min-width:30px; max-width:30px; min-height:30px; max-height:30px; border-radius:15px; font-size:17px; font-weight:600; }
QPushButton#windowControl { padding:0; min-width:34px; max-width:34px; min-height:30px; max-height:30px; border:0; border-radius:5px; background:transparent; }
QPushButton#windowControl:hover { background:#e6eef4; }
QPushButton#closeControl { padding:0; min-width:34px; max-width:34px; min-height:30px; max-height:30px; border:0; border-radius:5px; background:transparent; }
QPushButton#closeControl:hover { background:#c74343; }
QListWidget, QTableWidget, QPlainTextEdit { background:white; border:1px solid #dfe5ec; border-radius:6px; }
QListWidget::item { padding:10px 8px; margin:3px; border-radius:5px; }
QListWidget::item:selected { background:#e0f0ec; color:#134a45; }
QTableWidget { alternate-background-color:#f5f6f8; gridline-color:#edf0f3; selection-background-color:#e5e9ed; selection-color:#1c2938; }
QTableWidget::item { padding:7px; }
QTableWidget::item:selected { background:#e5e9ed; color:#1c2938; }
QHeaderView::section { background:#edf1f6; padding:9px; border:0; border-bottom:1px solid #dfe5ec; font-weight:600; }
QProgressBar { border:0; background:#e1e7ee; border-radius:4px; height:8px; }
QProgressBar::chunk { background:#27998a; border-radius:4px; }
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
    if not dark:return STYLE
    colors={'#f4f6f9':'#141414','#1c2938':'#e6e6e6','white':'#202020',
        '#627286':'#ababab','#174c60':'#d0d0d0','#dfe5ec':'#383838',
        '#ccd6e2':'#484848','#e6eef4':'#2d2d2d','#a1abba':'#777777',
        '#e0f0ec':'#333333','#134a45':'#f0f0f0','#f5f6f8':'#252525',
        '#edf0f3':'#303030','#e5e9ed':'#3a3a3a','#edf1f6':'#292929',
        '#e1e7ee':'#333333','#e8edf3':'#292929','#fff4d6':'#29261e',
        '#e5c66f':'#5a5034','#684d00':'#ead18b','#f7e8bc':'#343027','#d98d00':'#b97900',
        '#bcc7d3':'#515151','#91a2b5':'#707070'}
    import re
    result=re.sub('|'.join(re.escape(k) for k in colors),lambda m:colors[m.group()],STYLE)
    return result.replace('color:#202630','color:#ffffff')


class ElidedLabel(QLabel):
    """Single-line labels never force the table off-screen."""
    def __init__(self,text=''):
        super().__init__(text);self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)

    def setText(self,text):
        super().setText(text);self.setToolTip(text)

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(self.contentsRect(),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,
            self.fontMetrics().elidedText(self.text(),Qt.TextElideMode.ElideRight,self.contentsRect().width()))


def app_icon():
    pix=QPixmap(64,64);pix.fill(Qt.GlobalColor.transparent)
    p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor('#126b64'));p.setPen(Qt.PenStyle.NoPen);p.drawRoundedRect(2,2,60,60,15,15)
    p.setBrush(QColor('white'))
    for x,h in [(16,16),(26,34),(36,24),(46,42)]: p.drawRoundedRect(x,32-h//2,5,h,2,2)
    p.end();return QIcon(pix)


def ui_icon(kind,color):
    """Small vector icons remain centered and sharp at every Windows scale."""
    pix=QPixmap(24,24);pix.fill(Qt.GlobalColor.transparent)
    p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen=QPen(QColor(color),1.7);pen.setCapStyle(Qt.PenCapStyle.RoundCap);pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin);p.setPen(pen)
    if kind=='minimize':p.drawLine(6,13,18,13)
    elif kind=='maximize':p.drawRoundedRect(6,6,12,12,1,1)
    elif kind=='restore':
        p.drawRoundedRect(8,5,11,11,1,1);p.drawRoundedRect(5,8,11,11,1,1)
    elif kind=='close':
        p.drawLine(7,7,17,17);p.drawLine(17,7,7,17)
    elif kind=='sun':
        p.drawEllipse(8,8,8,8)
        for a,b,c,d in [(12,4,12,6),(12,18,12,20),(4,12,6,12),(18,12,20,12),(6,6,8,8),(16,16,18,18),(18,6,16,8),(8,16,6,18)]:p.drawLine(a,b,c,d)
    elif kind=='moon':
        path=QPainterPath();path.addEllipse(5,4,14,16)
        cut=QPainterPath();cut.addEllipse(10,2,12,14)
        p.fillPath(path.subtracted(cut),QColor(color))
    p.end();return QIcon(pix)


class TitleBar(QFrame):
    def __init__(self,parent):
        super().__init__(parent);self.setObjectName('titleBar')

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove();event.accept();return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.window().toggle_maximize();event.accept();return
        super().mouseDoubleClickEvent(event)


class Worker(QThread):
    snapshot=Signal(dict)
    notice=Signal(str)

    def __init__(self,root,config):
        super().__init__();self.root=root;self.config=config;self.commands=queue.Queue()

    def run(self):
        store=Store(self.root,self.config.get('gui_playlist_output_dir'));engine=Engine(store,self.config)
        last=0
        try:
            while not self.isInterruptionRequested():
                while not self.commands.empty():
                    action,payload=self.commands.get()
                    try: engine.command(action,payload)
                    except Exception as e:
                        self.notice.emit(str(e) if isinstance(e,ValueError) else 'İşlem yapılamadı; dosya erişimini kontrol edin.')
                try: engine.tick()
                except Exception as e:
                    # Do not expose raw credential-bearing server bodies or tracebacks.
                    store.set_setting('connection','İşlem hatası: '+type(e).__name__+' · işler korunuyor')
                    self.msleep(2000)
                if time.time()-last>1:
                    self.snapshot.emit(store.snapshot());last=time.time()
                self.msleep(300)
        finally: store.db.close()


class ImportDialog(QDialog):
    def __init__(self,path,parent=None):
        super().__init__(parent);self.path=Path(path);self.setWindowTitle('Playlist ekle');self.resize(680,480)
        box=QVBoxLayout(self);title=QLabel(self.path.name);title.setObjectName('title');box.addWidget(title)
        self.layout_choice=QComboBox()
        self.layout_choice.addItem('Sanatçı - Şarkı','artist_title')
        self.layout_choice.addItem('Şarkı - Sanatçı (mevcut numaralı export)','title_artist')
        box.addWidget(QLabel('TXT içindeki sütun sırası'));box.addWidget(self.layout_choice)
        self.preview=QTableWidget(0,2);self.preview.setHorizontalHeaderLabels(['Sanatçı','Şarkı'])
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);box.addWidget(self.preview)
        self.mode=QComboBox()
        self.mode.addItem('Hızlı · parça başına en fazla 2 peer','fast')
        self.mode.addItem('Sabırlı · 1 peer, uzun kuyrukta bekle','patient')
        self.mode.addItem('Sadece arama testi · hiçbir dosya indirme','preview')
        box.addWidget(self.mode)
        self.summary=QLabel();self.summary.setWordWrap(True);box.addWidget(self.summary)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Playlist oluştur')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Vazgeç')
        self.ok=buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);box.addWidget(buttons)
        self.layout_choice.currentIndexChanged.connect(self.refresh);self.refresh()

    def refresh(self):
        try:
            tracks=parse_tracks(self.path,self.layout_choice.currentData())
            self.preview.setRowCount(min(len(tracks),50))
            for i,t in enumerate(tracks[:50]):
                self.preview.setItem(i,0,QTableWidgetItem(t.artist));self.preview.setItem(i,1,QTableWidgetItem(t.title))
            self.summary.setText(f'{len(tracks)} parça · Playlists/{self.path.stem}/\nDosya adlarına sıra numarası eklenmez. Oluşturduktan sonra Başlat’a basın.')
            self.ok.setEnabled(True)
        except Exception as e:
            self.summary.setText(str(e) if isinstance(e,ValueError) else 'Dosya UTF-8 TXT olarak okunamadı.');self.ok.setEnabled(False)


class Window(QMainWindow):
    def open_settings(self):
        try:
            dialog=SetupDialog(self.root,load_settings(self.root),self)
        except (OSError,ValueError):
            QMessageBox.warning(self,'Ayarlar','Ayar dosyası okunamadı; mevcut dosya korunuyor.')
            return
        if dialog.exec()==QDialog.DialogCode.Accepted:
            QMessageBox.information(self,'Ayarlar kaydedildi','Ayarlar bir sonraki açılışta uygulanacak.\nTepsi menüsünden Tamamen çık seçip uygulamayı yeniden açın.')

    def nativeEvent(self,event_type,message):
        if sys.platform=='win32':
            import ctypes
            from ctypes import wintypes
            msg=wintypes.MSG.from_address(int(message))
            if msg.message==0x0084 and not self.isMaximized():  # WM_NCHITTEST
                bounds=wintypes.RECT()
                user32=ctypes.windll.user32
                user32.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
                if user32.GetWindowRect(msg.hWnd,ctypes.byref(bounds)):
                    x=ctypes.c_short(msg.lParam & 0xffff).value
                    y=ctypes.c_short((msg.lParam >> 16) & 0xffff).value
                    border=max(7,round(8*user32.GetDpiForWindow(msg.hWnd)/96))
                    left=bounds.left<=x<bounds.left+border
                    right=bounds.right-border<=x<bounds.right
                    top=bounds.top<=y<bounds.top+border
                    bottom=bounds.bottom-border<=y<bounds.bottom
                    if top and left:return True,13   # HTTOPLEFT
                    if top and right:return True,14  # HTTOPRIGHT
                    if bottom and left:return True,16 # HTBOTTOMLEFT
                    if bottom and right:return True,17 # HTBOTTOMRIGHT
                    if left:return True,10           # HTLEFT
                    if right:return True,11          # HTRIGHT
                    if top:return True,12            # HTTOP
                    if bottom:return True,15         # HTBOTTOM
        return super().nativeEvent(event_type,message)

    def __init__(self,root=ROOT,worker=None):
        super().__init__();self.root=Path(root);self.worker=worker;self.data={};self.job_id=None;self.track_ids=[];self.quitting=False
        self.received_at=0
        self.review_dialog=None;self.notified_reviews=set();self.audio_metadata_cache={}
        self.next_notice=0;self.last_log_id=None
        self.preferences=QSettings(str(self.root/'ui.ini'),QSettings.Format.IniFormat)
        self.dark=self.preferences.value('theme','light')=='dark'
        self.setWindowFlags(Qt.WindowType.Window|Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle('Soulseek Playlists');self.setWindowIcon(app_icon());self.resize(1230,800);self.setMinimumSize(990,620);self.setAcceptDrops(True)
        container=QWidget();self.setCentralWidget(container);layout=QVBoxLayout(container);layout.setContentsMargins(14,10,14,8);layout.setSpacing(8)
        self.title_bar=TitleBar(self);head=QHBoxLayout(self.title_bar);head.setContentsMargins(0,0,0,0);head.setSpacing(7)
        title=QLabel('Soulseek Playlists');title.setObjectName('brand');head.addWidget(title)
        sub=QLabel('· TXT’den DJ klasörüne');sub.setObjectName('muted');head.addWidget(sub)
        self.connection=ElidedLabel('Bağlanıyor…');self.connection.setObjectName('muted');head.addWidget(self.connection,1)
        settings_button=QPushButton('Ayarlar');settings_button.clicked.connect(self.open_settings);head.addWidget(settings_button)
        self.theme_button=QPushButton();self.theme_button.setObjectName('themeToggle');self.theme_button.clicked.connect(self.toggle_theme);head.addWidget(self.theme_button)
        self.minimize_button=self.window_button('minimize','Küçült',self.showMinimized);head.addWidget(self.minimize_button)
        self.maximize_button=self.window_button('maximize','Büyüt',self.toggle_maximize);head.addWidget(self.maximize_button)
        self.close_button=self.window_button('close','Tepsiye küçült',self.close,True);head.addWidget(self.close_button)
        layout.addWidget(self.title_bar)
        splitter=QSplitter();layout.addWidget(splitter,1)
        left=QWidget();lv=QVBoxLayout(left);lv.setContentsMargins(0,0,12,0)
        left_head=QHBoxLayout();left_head.addWidget(QLabel('PLAYLISTLER'));left_head.addStretch()
        add=QPushButton('+ TXT');add.setToolTip('TXT dosyası ekle veya pencereye sürükle');add.setObjectName('primary');add.clicked.connect(self.choose);left_head.addWidget(add);lv.addLayout(left_head)
        panel=QFrame();panel.setObjectName('listPanel');panel_layout=QVBoxLayout(panel);panel_layout.setContentsMargins(0,0,0,0);panel_layout.setSpacing(0)
        self.jobs=QListWidget();self.jobs.currentItemChanged.connect(self.select_job);panel_layout.addWidget(self.jobs,1)
        hint=QLabel('TXT dosyasını buraya bırakabilirsiniz.');hint.setWordWrap(True);hint.setAlignment(Qt.AlignmentFlag.AlignCenter);hint.setObjectName('muted');panel_layout.addWidget(hint)
        lv.addWidget(panel,1);splitter.addWidget(left)
        right=QWidget();rv=QVBoxLayout(right);rv.setContentsMargins(0,0,0,0)
        job_head=QHBoxLayout();self.job_title=QLabel('Bir playlist ekleyin');self.job_title.setObjectName('title');job_head.addWidget(self.job_title)
        self.path_label=ElidedLabel('Her playlist kendi klasöründe saklanır.');self.path_label.setObjectName('muted');job_head.addWidget(self.path_label,1);rv.addLayout(job_head)
        cards=QHBoxLayout();self.cards={}
        for key,label in [('total','Parça'),('done','Tamamlandı'),('active','Peer / indirme'),('remaining','Aranacak / inceleme')]:
            frame=QFrame();frame.setObjectName('card');cl=QHBoxLayout(frame);cl.setContentsMargins(10,5,10,5);v=QLabel('0');v.setObjectName('cardValue');cl.addWidget(v);cl.addWidget(QLabel(label));cards.addWidget(frame);self.cards[key]=v
        rv.addLayout(cards);self.progress=QProgressBar();self.progress.setTextVisible(False);rv.addWidget(self.progress)
        buttons=QHBoxLayout();self.controls=[]
        for label,action in [('Başlat / sürdür','start'),('Aramayı duraklat','pause'),('Tekrar ara','retry'),('İşi iptal et','cancel'),('Klasörü aç','folder')]:
            b=QPushButton(label);b.clicked.connect(lambda checked=False,a=action:self.action(a));buttons.addWidget(b);self.controls.append(b)
        rv.addLayout(buttons)
        search_row=QHBoxLayout()
        self.filter=QLineEdit();self.filter.setPlaceholderText('Sanatçı, şarkı veya durum ara…');self.filter.textChanged.connect(self.render_tracks);search_row.addWidget(self.filter,1)
        self.review_box=QFrame();self.review_box.setObjectName('attentionBar');attention=QHBoxLayout(self.review_box);attention.setContentsMargins(3,1,5,1);attention.setSpacing(2)
        self.review_button=QPushButton('Adaylar / seçimler');self.review_button.setObjectName('attentionAction');self.review_button.clicked.connect(self.open_reviews);attention.addWidget(self.review_button)
        self.review_count=QPushButton('0');self.review_count.setObjectName('attentionCount');self.review_count.setToolTip('İncelenebilecek parça sayısı');self.review_count.clicked.connect(self.open_reviews);attention.addWidget(self.review_count)
        search_row.addWidget(self.review_box);rv.addLayout(search_row)
        self.table=QTableWidget(0,9);self.table.setHorizontalHeaderLabels(['Durum','#','Sanatçı','Şarkı','Kalite','Süre','Albüm','Tür','İlerleme'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide();self.table.setAlternatingRowColors(True);self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col,width in enumerate([165,38,120,180,110,58,135,90,70]):self.table.setColumnWidth(col,width)
        self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch);self.table.cellDoubleClicked.connect(self.details)
        rv.addWidget(self.table,1)
        self.log_dialog=QDialog(self);self.log_dialog.setWindowTitle('Canlı günlük · tüm playlistler');self.log_dialog.resize(920,500)
        log_layout=QVBoxLayout(self.log_dialog)
        self.logs=QPlainTextEdit();self.logs.setReadOnly(True);self.logs.setMaximumBlockCount(5000);log_layout.addWidget(self.logs)
        splitter.addWidget(right);splitter.setSizes([230,930])
        bottom=QHBoxLayout();self.latest_event=ElidedLabel('Henüz işlem yok');self.latest_event.setObjectName('muted');bottom.addWidget(self.latest_event,1)
        self.footer=QLabel();self.footer.setObjectName('muted');bottom.addWidget(self.footer)
        log_button=QPushButton('Log');log_button.clicked.connect(self.open_logs);bottom.addWidget(log_button)
        self.size_grip=QSizeGrip(self);self.size_grip.setFixedSize(15,15);self.size_grip.setToolTip('Pencereyi yeniden boyutlandır');bottom.addWidget(self.size_grip);layout.addLayout(bottom)
        self.tray=QSystemTrayIcon(self.windowIcon(),self);self.tray.setToolTip('Soulseek Playlists')
        menu=QMenu();show=menu.addAction('Uygulamayı göster');show.triggered.connect(self.reveal)
        quit_action=menu.addAction('Tamamen çık');quit_action.triggered.connect(self.quit_app);self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda why:self.reveal() if why in (QSystemTrayIcon.ActivationReason.Trigger,QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.messageClicked.connect(self.open_reviews)
        if QSystemTrayIcon.isSystemTrayAvailable():self.tray.show()
        if worker:
            worker.snapshot.connect(self.receive);worker.notice.connect(lambda msg:QMessageBox.warning(self,'İşlem bilgisi',msg))
        self.clock=QTimer(self);self.clock.timeout.connect(self.update_clock);self.clock.start(1000)
        self.apply_theme()

    def window_button(self,kind,tooltip,callback,close=False):
        button=QPushButton();button.setObjectName('closeControl' if close else 'windowControl')
        button.setProperty('iconKind',kind);button.setToolTip(tooltip);button.setAccessibleName(tooltip)
        button.clicked.connect(callback);return button

    def update_window_icons(self):
        color='#e6e6e6' if self.dark else '#344050'
        self.theme_button.setIcon(ui_icon('sun' if self.dark else 'moon',color));self.theme_button.setIconSize(QSize(18,18));self.theme_button.setText('')
        self.minimize_button.setIcon(ui_icon('minimize',color));self.minimize_button.setIconSize(QSize(18,18))
        maximum='restore' if self.isMaximized() else 'maximize'
        self.maximize_button.setIcon(ui_icon(maximum,color));self.maximize_button.setToolTip('Geri al' if self.isMaximized() else 'Büyüt')
        self.maximize_button.setAccessibleName(self.maximize_button.toolTip());self.maximize_button.setIconSize(QSize(18,18))
        self.close_button.setIcon(ui_icon('close',color));self.close_button.setIconSize(QSize(18,18))

    def toggle_maximize(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()
        QTimer.singleShot(0,self.update_window_icons)

    def apply_theme(self):
        app=QApplication.instance();palette=app.style().standardPalette()
        if self.dark:
            for role,color in {'Window':'#141414','WindowText':'#e6e6e6','Base':'#202020','AlternateBase':'#252525',
                'Text':'#e6e6e6','Button':'#202020','ButtonText':'#e6e6e6','Highlight':'#3a3a3a','HighlightedText':'#ffffff',
                'ToolTipBase':'#292929','ToolTipText':'#e6e6e6'}.items():palette.setColor(getattr(QPalette.ColorRole,role),QColor(color))
        app.setPalette(palette);app.setStyleSheet(theme_style(self.dark))
        self.theme_button.setToolTip('Açık moda geç' if self.dark else 'Koyu moda geç')
        self.theme_button.setAccessibleName(self.theme_button.toolTip())
        self.update_window_icons()
        self.render_tracks()

    def toggle_theme(self):
        self.dark=not self.dark;self.preferences.setValue('theme','dark' if self.dark else 'light');self.preferences.sync();self.apply_theme()

    def open_logs(self,*_,reveal=True):
        store=Store(self.root)
        try:
            events=store.rows('SELECT e.*,j.name FROM events e LEFT JOIN jobs j ON j.id=e.job ORDER BY e.id')
        finally:store.db.close()
        self.logs.setPlainText('\n'.join(time.strftime('%H:%M:%S',time.localtime(e['created']))+'  ['+str(e['name'] or 'Uygulama')+'] '+e['message'] for e in events))
        self.logs.verticalScrollBar().setValue(self.logs.verticalScrollBar().maximum())
        if reveal:self.log_dialog.show();self.log_dialog.raise_()

    def choose(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Playlist TXT seç',str(self.root),'Şarkı listesi (*.txt)')
        for path in paths:self.import_path(path)

    def import_path(self,path):
        dialog=ImportDialog(path,self)
        if dialog.exec()==QDialog.DialogCode.Accepted and self.worker:
            self.worker.commands.put(('import',{'source':str(path),'layout':dialog.layout_choice.currentData(),'mode':dialog.mode.currentData()}))

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() and u.toLocalFile().lower().endswith('.txt') for u in event.mimeData().urls()):event.acceptProposedAction()

    def dropEvent(self,event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith('.txt'):self.import_path(url.toLocalFile())

    def action(self,action):
        if not self.job_id:return
        if action=='folder':
            job=next(j for j in self.data['jobs'] if j['id']==self.job_id);QDesktopServices.openUrl(QUrl.fromLocalFile(job['folder']));return
        if action=='cancel' and QMessageBox.question(self,'İşi iptal et','Bu playlist’in indirme istekleri iptal edilsin mi? Tamamlanan dosyalar korunacak.')!=QMessageBox.StandardButton.Yes:return
        if self.worker:self.worker.commands.put((action,self.job_id))

    def receive(self,data):
        self.data=data;self.received_at=time.time();selected=self.job_id
        self.jobs.blockSignals(True);self.jobs.clear()
        for job in data['jobs']:
            tracks=[t for t in data['tracks'] if t['job']==job['id']];done=sum(t['status']=='done' for t in tracks)
            item=QListWidgetItem(f"{job['name']}\n{done}/{len(tracks)} · {LABELS.get(job['status'],job['status'])}")
            item.setData(Qt.ItemDataRole.UserRole,job['id']);self.jobs.addItem(item)
            if job['id']==selected:self.jobs.setCurrentItem(item)
        if not self.jobs.currentItem() and self.jobs.count():self.jobs.setCurrentRow(0)
        self.jobs.blockSignals(False);self.select_job();self.update_clock()
        reviews=self.review_tracks()
        self.review_count.setText(str(len(reviews)))
        self.review_button.setEnabled(bool(data['tracks']));self.review_count.setEnabled(bool(data['tracks']))
        new=[t for t in reviews if not self.preferences.value('notified/'+t['id']+'/'+t['status'],False,type=bool)]
        if new and time.time()>=self.next_notice:
            self.next_notice=time.time()+60
            for t in new:self.preferences.setValue('notified/'+t['id']+'/'+t['status'],True)
            self.preferences.sync()
            self.tray.showMessage('Bekleyen şarkıları inceleyebilirsiniz',f'{len(reviews)} parçada seçim / yeniden arama bekleniyor. Diğer işler devam ediyor.',QSystemTrayIcon.MessageIcon.Information,10000)
        if data.get('events'):
            event=data['events'][0];job=next((j['name'] for j in data['jobs'] if j['id']==event['job']),'Uygulama')
            self.latest_event.setText(time.strftime('%H:%M:%S',time.localtime(event['created']))+' · '+job+' · '+event['message'])
            if self.log_dialog.isVisible() and self.last_log_id!=event['id']:self.open_logs(reveal=False)
            self.last_log_id=event['id']
        if self.review_dialog and self.review_dialog.isVisible():self.review_dialog.sync_pending()

    def select_job(self,*_):
        item=self.jobs.currentItem();self.job_id=item.data(Qt.ItemDataRole.UserRole) if item else None
        for b in self.controls:b.setEnabled(bool(item))
        if not item:return
        job=next(j for j in self.data['jobs'] if j['id']==self.job_id)
        self.job_title.setText(job['name']);self.path_label.setText(job['folder'])
        tracks=[t for t in self.data['tracks'] if t['job']==self.job_id]
        done=sum(t['status']=='done' for t in tracks);active=sum(t['status'] in ('waiting','downloading') for t in tracks)
        skipped=sum(t['status']=='skipped' for t in tracks)
        for key,n in [('total',len(tracks)),('done',done),('active',active),('remaining',len(tracks)-done-active-skipped)]:self.cards[key].setText(str(n))
        self.progress.setMaximum(max(1,len(tracks)));self.progress.setValue(done+skipped)
        self.progress.setToolTip(f'{done} tamamlandı · {skipped} atlandı · {len(tracks)} parça')
        self.render_tracks()

    def render_tracks(self):
        term=self.filter.text().casefold();tracks=[t for t in self.data.get('tracks',[]) if t['job']==self.job_id and term in (t['artist']+' '+t['title']+' '+LABELS.get(t['status'],t['status'])).casefold()]
        scroll=self.table.verticalScrollBar().value();self.table.setRowCount(len(tracks));self.track_ids=[]
        for i,t in enumerate(tracks):
            self.track_ids.append(t['id']);attempts=[a for a in self.data.get('attempts',[]) if a['track']==t['id'] and a['status'] in ('queued','downloading','finishing','submitting')]
            percent=100 if t['status']=='done' else max((a['bytes']*100/max(1,a['size']) for a in attempts),default=0)
            status_key='searching' if t['search_id'] else t['status']
            status=STATUS_PRESENTATION.get(status_key,(LABELS.get(t['status'],t['status']),'#edf0f3','#3f4d5b'))[0]
            meta=self.track_metadata(t)
            quality=quality_label(meta) if meta else (' / '.join(dict.fromkeys(a.get('quality','—') for a in attempts)) or '—')
            length=int(round(meta.get('length',0))) if meta else 0
            duration=f'{length//60}:{length%60:02d}' if length else '—'
            values=[status,str(t['position']),t['artist'],t['title'],quality,duration,meta.get('album') or '—',meta.get('genre') or '—',f'{percent:.0f}%']
            for c,value in enumerate(values):
                item=QTableWidgetItem(value);item.setToolTip(t['detail'] if c==0 else value)
                if c==0:
                    _label,bg,fg=STATUS_PRESENTATION.get(status_key,(status,'#edf0f3','#3f4d5b'))
                    if self.dark:
                        bg,fg={'done':('#2b2b2b','#91dfb3'),'downloading':('#303030','#a8d8ee'),
                            'searching':('#303030','#c4d4ee'),'waiting':('#303030','#e9cf87'),
                            'review':('#303030','#d8c0eb'),'deferred':('#303030','#d8c0eb'),
                            'ready':('#2b2b2b','#9edfcf')}.get(status_key,('#303030','#cfcfcf'))
                    item.setBackground(QColor(bg));item.setForeground(QColor(fg))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    font=item.font();font.setBold(True);item.setFont(font)
                self.table.setItem(i,c,item)
        self.table.verticalScrollBar().setValue(scroll)

    def track_metadata(self,t):
        if t['status']!='done' or not t['path']:return {}
        try:
            stamp=Path(t['path']).stat().st_mtime_ns;cached=self.audio_metadata_cache.get(t['path'])
            if not cached or cached[0]!=stamp:self.audio_metadata_cache[t['path']]=(stamp,audio_metadata(t['path']))
            return self.audio_metadata_cache[t['path']][1]
        except Exception:return {'extension':Path(t['path']).suffix[1:]}

    def review_tracks(self,scope='attention'):
        approved={r['track'] for r in self.data.get('approvals',[])}
        tracks=self.data.get('tracks',[])
        if scope=='all':return tracks
        if scope in ('deferred','skipped'):return [t for t in tracks if t['status']==scope]
        return [t for t in tracks if t['status'] in REVIEW_STATES and (t['id'] not in approved or t['status']=='retry_wait')]

    def open_reviews(self,*_,track_id=None):
        self.reveal()
        if self.review_dialog is None:
            self.review_dialog=ReviewDialog(self)
        self.review_dialog.refresh(track_id)
        self.review_dialog.show();self.review_dialog.raise_();self.review_dialog.activateWindow()

    def update_clock(self):
        self.connection.setText(self.data.get('connection','Bağlanıyor…'))
        wait=max(0,int(self.data.get('next_search',0)-time.time()))
        updated=time.strftime('%H:%M:%S',time.localtime(self.received_at)) if self.received_at else 'bekleniyor'
        self.footer.setText(f'Arama: {wait} sn')
        self.footer.setToolTip(f'Son motor güncellemesi: {updated} · Uzun kuyruklar korunur')

    def details(self,row,_column):
        if row>=len(self.track_ids):return
        self.open_reviews(track_id=self.track_ids[row])

    def reveal(self):
        self.showNormal()
        # Recover windows left on a disconnected monitor as well as hidden launches.
        screens=QApplication.screens()
        frame=self.frameGeometry()
        if screens and not any(s.availableGeometry().contains(frame.center()) for s in screens):
            area=QApplication.primaryScreen().availableGeometry()
            self.resize(min(self.width(),area.width()),min(self.height(),area.height()))
            frame=self.frameGeometry();frame.moveCenter(area.center());self.move(frame.topLeft())
        if sys.platform=='win32':
            # STARTUPINFO/SW_HIDE can hide the native window while Qt thinks it is
            # visible. Explicit ShowWindow repairs this mismatch on tray activation.
            import ctypes
            from ctypes import wintypes
            user32=ctypes.windll.user32
            user32.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
            user32.SetForegroundWindow.argtypes=[wintypes.HWND]
            handle=int(self.winId())
            user32.ShowWindow(handle,9)  # SW_RESTORE
            user32.ShowWindow(handle,5)  # SW_SHOW
            user32.SetForegroundWindow(handle)
        self.raise_();self.activateWindow()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==event.Type.WindowStateChange and hasattr(self,'maximize_button'):
            self.size_grip.setVisible(not self.isMaximized())
            QTimer.singleShot(0,self.update_window_icons)

    def closeEvent(self,event):
        if not self.quitting and self.tray.isVisible():
            self.hide();event.ignore();self.tray.showMessage('Soulseek Playlists','Arka planda çalışıyor. Tepsi simgesinden tekrar açabilirsiniz.');return
        if not self.quitting:
            event.ignore();self.quit_app();return
        event.accept()

    def quit_app(self):
        if self.quitting:return
        self.quitting=True;self.footer.setText('İşler kaydediliyor…');self.setEnabled(False)
        if self.worker:
            self.worker.requestInterruption();self.worker.finished.connect(QApplication.instance().quit)
            if not self.worker.isRunning():QApplication.instance().quit()
        else:QApplication.instance().quit()


class ReviewDialog(QDialog):
    """Non-modal selection; the worker and the rest of the playlists keep running."""
    def __init__(self,window):
        super().__init__(window);self.window=window;self.rows=[]
        self.pending=None
        self.setWindowTitle('Eşleşmeleri incele ve seç');self.resize(1100,560)
        box=QVBoxLayout(self)
        hint=QLabel('Diğer parçalar çalışmaya devam eder. Seç: adayı sıraya ekle · Pass: parçayı atla · Sonraya bırak: siz dönene kadar beklet.')
        hint.setWordWrap(True);box.addWidget(hint)
        self.scope=QComboBox()
        for label,value in [('Seçim bekleyenler','attention'),('Sonraya bırakılanlar','deferred'),('Atlananlar (Pass)','skipped'),('Tüm şarkılar','all')]:self.scope.addItem(label,value)
        self.scope.currentIndexChanged.connect(lambda:self.refresh());box.addWidget(self.scope)
        self.tracks=QComboBox();box.addWidget(self.tracks);self.tracks.currentIndexChanged.connect(self.load_candidates)
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(['Dosya / sürüm','Kalite','Boyut','Peer','Puan','Kuyruk','İnceleme nedeni'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True);self.table.verticalHeader().hide()
        for col,width in enumerate([360,125,75,120,55,95,270]):self.table.setColumnWidth(col,width)
        self.table.itemSelectionChanged.connect(self.selection_changed);box.addWidget(self.table)
        self.transfer_info=QPlainTextEdit();self.transfer_info.setReadOnly(True);self.transfer_info.setMaximumHeight(90);box.addWidget(self.transfer_info)
        self.note=QLabel();self.note.setWordWrap(True);box.addWidget(self.note)
        actions=QHBoxLayout();self.select=QPushButton('Seçili adayı sıraya ekle');self.select.setObjectName('primary')
        self.select.clicked.connect(self.approve);actions.addWidget(self.select)
        refresh=QPushButton('Listeyi yenile');refresh.clicked.connect(lambda:self.refresh());actions.addWidget(refresh)
        self.skip=QPushButton('Pass · atla');self.skip.clicked.connect(lambda:self.track_action('skip_track'));actions.addWidget(self.skip)
        self.later=QPushButton('Sonraya bırak');self.later.clicked.connect(lambda:self.track_action('defer_track'));actions.addWidget(self.later)
        self.restore=QPushButton('Geri al / yeniden ara');self.restore.clicked.connect(lambda:self.track_action('restore_track'));actions.addWidget(self.restore)
        close=QPushButton('Kapat');close.clicked.connect(self.hide);actions.addWidget(close);box.addLayout(actions)

    def refresh(self,track_id=None):
        wanted=track_id or self.tracks.currentData()
        if track_id and not any(t['id']==track_id for t in self.window.review_tracks(self.scope.currentData())):
            self.scope.blockSignals(True);self.scope.setCurrentIndex(self.scope.findData('all'));self.scope.blockSignals(False)
        self.tracks.blockSignals(True);self.tracks.clear()
        jobs={j['id']:j['name'] for j in self.window.data.get('jobs',[])}
        for t in self.window.review_tracks(self.scope.currentData()):
            self.tracks.addItem('['+jobs.get(t['job'],'')+'] '+t['artist']+' — '+t['title']+' · '+LABELS.get(t['status'],t['status']),t['id'])
        index=self.tracks.findData(wanted)
        if index>=0:self.tracks.setCurrentIndex(index)
        self.tracks.blockSignals(False);self.load_candidates()

    def load_candidates(self,*_):
        store=Store(self.window.root)
        try:self.rows=store.rows('SELECT * FROM candidates WHERE track=? ORDER BY score DESC LIMIT 40',(self.tracks.currentData(),))
        finally:store.db.close()
        tid=self.tracks.currentData()
        track=next((t for t in self.window.data.get('tracks',[]) if t['id']==tid),None)
        lines=[track['detail'],track['path']] if track else ['Bu listede parça yok.']
        for a in self.window.data.get('attempts',[]):
            if a['track']==tid:lines.append(a['peer']+' · '+LABELS.get(a['status'],a['status'])+' · '+a['detail'])
        self.transfer_info.setPlainText('\n'.join(line for line in lines if line))
        self.table.setRowCount(0);self.table.setRowCount(len(self.rows))
        for i,r in enumerate(self.rows):
            c=json.loads(r['data'])
            slot='Boş slot' if c.get('free_upload_slot') else str(c.get('queue_length') if c.get('queue_length') is not None else '?')
            values=[c['filename'],quality_label(c),f"{c['size']/1048576:.1f} MB",c['peer'],str(c['score']),slot,c['reason']]
            for col,value in enumerate(values):
                item=QTableWidgetItem(value);item.setToolTip(value);self.table.setItem(i,col,item)
        self.selection_changed()

    def selection_changed(self):
        row=self.table.currentRow();allowed=False
        if 0<=row<len(self.rows):allowed=quality_allowed(Candidate(**json.loads(self.rows[row]['data'])))
        tid=self.tracks.currentData();track=next((t for t in self.window.data.get('tracks',[]) if t['id']==tid),None)
        job=next((j for j in self.window.data.get('jobs',[]) if track and j['id']==track['job']),None)
        active=any(a['track']==tid and a['status'] in ('queued','downloading','finishing','submitting') for a in self.window.data.get('attempts',[]))
        editable=bool(track and track['status']!='done' and not active and self.window.worker and self.pending is None)
        self.select.setEnabled(bool(allowed and editable and job and job['mode']!='preview' and job['status']!='cancelled'))
        self.skip.setEnabled(editable);self.later.setEnabled(editable);self.restore.setEnabled(editable)
        self.note.setText('İşlem kaydediliyor…' if self.pending else
            'Aktif transfer izleniyor; dosya tamamlanınca yeniden seçebilirsiniz.' if active else
            'Aday yok. Yeniden arayabilir, Pass diyebilir veya sonraya bırakabilirsiniz.' if not self.rows else
            'Seçiminiz kaydedilir. FLAC / MP3 320, dosya kontrolü ve peer bekleme süreleri uygulanır; duraklatılmış işi sürdürmeniz gerekir.')

    def approve(self):
        row=self.table.currentRow()
        if not 0<=row<len(self.rows) or not self.window.worker:return
        selected=self.rows[row]
        self.pending=('approve',selected['track'],time.time())
        self.window.worker.commands.put(('approve',{'track':selected['track'],'candidate':selected['id']}))
        self.selection_changed()

    def track_action(self,action):
        tid=self.tracks.currentData()
        if not tid or not self.window.worker:return
        self.pending=(action,tid,time.time())
        self.window.worker.commands.put((action,tid));self.selection_changed()

    def sync_pending(self):
        if not self.pending:return
        action,tid,started=self.pending
        t=next((t for t in self.window.data.get('tracks',[]) if t['id']==tid),None)
        expected={'skip_track':'skipped','defer_track':'deferred','restore_track':'pending'}
        done=bool(t and (t['status']==expected.get(action) or action=='approve' and any(a['track']==tid for a in self.window.data.get('approvals',[]))))
        if done or time.time()-started>30:
            self.pending=None;self.refresh()


def main():
    root=ROOT
    app=QApplication(sys.argv);app.setApplicationName('Soulseek Playlists');app.setStyle('Fusion');app.setStyleSheet(STYLE);app.setQuitOnLastWindowClosed(False)
    if '--package-check' in sys.argv:
        from package_check import run
        return run(app, sys.argv[sys.argv.index('--package-check')+1])
    if '--connection-check' in sys.argv:
        from package_check import connection_check
        pos=sys.argv.index('--connection-check')
        return connection_check(sys.argv[pos+1],sys.argv[pos+2])
    if '--delivery-check' in sys.argv:
        from package_check import delivery_check
        pos=sys.argv.index('--delivery-check')
        return delivery_check(sys.argv[pos+1],sys.argv[pos+2])
    if '--lifecycle-check' in sys.argv:
        from package_lifecycle import attach
        pos=sys.argv.index('--lifecycle-check')
        phase,folder,output=sys.argv[pos+1:pos+4]
        if phase not in ('first','restart'):return 2
        root=Path(folder).resolve()
        lifecycle_timer=attach(app,root,phase,output)
    root.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(root/'playlist-app.lock'));lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        # Ask the existing instance to reveal itself on the next UI tick.
        (root/'show-window.signal').touch();return 0
    try:
        config=load_settings(root)
    except (OSError,ValueError):
        QMessageBox.warning(None,'Ayarlar okunamadı','config.json okunamadı. Dosya korunuyor; ayar dosyasını kontrol edin.')
        return 1
    if not (root/'config.json').exists() or (getattr(sys,'frozen',False) and not config.get('setup_complete')):
        setup=SetupDialog(root,config)
        if setup.exec()!=QDialog.DialogCode.Accepted:return 0
        config=setup.config
    worker=Worker(root,config);window=Window(root,worker)
    timer=QTimer(window)
    def reveal_requested():
        signal=root/'show-window.signal'
        if signal.exists():signal.unlink();window.reveal()
    timer.timeout.connect(reveal_requested);timer.start(700)
    worker.start();window.show();QTimer.singleShot(0,window.reveal)
    result=app.exec();worker.requestInterruption();worker.wait();lock.unlock();return result


if __name__=='__main__':
    raise SystemExit(main())
