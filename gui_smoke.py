"""Offscreen GUI verification; no live API or download requests."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import json
import tempfile
import time
from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from engine import Engine
from matching import Candidate
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from playlist_app import STYLE, ImportDialog, OcrDialog, Window, Worker
from store import Store

HERE = Path(__file__).parent
app = QApplication([])
for font in ('segoeui.ttf', 'segoeuib.ttf', 'seguisym.ttf'):
    QFontDatabase.addApplicationFont(rf'C:\Windows\Fonts\{font}')
app.setStyle('Fusion')
app.setStyleSheet(STYLE)


def commands(worker):
    items = []
    while not worker.commands.empty():
        items.append(worker.commands.get_nowait())
    return items


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    source = root / 'Akşam Seti.txt'
    source.write_text('Prospa - Don’t Stop\nSammy Virji - If U Need It\nTim Deluxe - JAS - Club Mix\n'
                      'Oden & Fatzo - Fly Away\n', encoding='utf-8')
    store = Store(root)
    job = store.add_job(source, 'artist_title')
    store.update('jobs', job, status='active')
    tracks = store.rows('SELECT * FROM tracks ORDER BY position')
    store.update('tracks', tracks[0]['id'], status='done', detail='Tamamlandı', quality_note='15 kHz · şüpheli')
    store.update('tracks', tracks[1]['id'], status='waiting', detail='Queued, Remotely')
    store.update('tracks', tracks[2]['id'], status='review', detail='"Tim Deluxe JAS": 40 ses dosyası · 0 güvenli eşleşme')
    store.update('tracks', tracks[3]['id'], status='not_found', next_search=time.time() + 5400)
    store.event(job, 'Aranıyor · Tim Deluxe — JAS - Club Mix · "Tim Deluxe JAS"')
    store.set_setting('connection', 'slskd · Soulseek bağlı')
    store.set_setting('share_warning', '1')
    store.set_setting('spectrum_revision', 'cliff-1')
    for i, (name, score) in enumerate([(r'Tim Deluxe\The Radicle\16 - Tim Deluxe - JAS (Dub).flac', 89),
                                       (r'x\Tim Deluxe - JAS.mp3', 89)]):
        c = Candidate(f'peer{i}', name, 30000000, None if name.endswith('flac') else 320, name[-4:].strip('.'),
                      0, 1000000, True, bit_depth=16, sample_rate=44100, score=score, reason='Sürüm farklı: dub')
        store.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)',
                      (f'cand{i}', tracks[2]['id'], c.peer, c.filename, json.dumps(c.to_dict()), score, 0, time.time()))

    worker = Worker(root, {})
    window = Window(root, worker)
    window.receive(store.snapshot())
    window.show()
    app.processEvents()
    assert window.table.rowCount() == 4
    assert window.table.horizontalHeaderItem(4).text() == 'Kalite'
    assert window.chips['attention'].text() == 'Seçim bekliyor 1' and window.chips['attention'].isVisible()
    assert not window.chips['skipped'].isVisible()
    assert window.banner.isVisible()
    assert window.retry_button.isVisible()
    assert window.toggle_button.text() == 'Duraklat'
    assert 'şüpheli' in window.table.item(0, 4).text()
    assert window.table.item(3, 0).text().startswith('Bulunamadı · 1 sa ')

    # Filter chip → only the track that needs a decision
    window.set_filter('attention')
    assert window.table.rowCount() == 1
    window.table.selectRow(0)
    app.processEvents()
    detail = window.detail
    assert detail.title.text() == 'Tim Deluxe — JAS - Club Mix'
    assert detail.table.rowCount() == 2
    dub = next(i for i in range(2) if 'Dub' in detail.table.item(i, 0).text())
    assert '(Dub)</b>' in detail.table.item(dub, 0).data(256)
    assert '#d9534f' not in detail.table.item(1 - dub, 0).data(256)
    detail.table.selectRow(dub)
    assert detail.download.isEnabled()
    detail.approve()
    assert commands(worker) == [('approve', {'track': tracks[2]['id'], 'candidate': 'cand0'})]
    detail.query.setText('Tim Deluxe JAS club')
    detail.manual_search()
    detail.choice('defer_track')
    assert [c[0] for c in commands(worker)] == ['manual_search', 'defer_track']
    engine = Engine(store, {'slskd_url': 'http://localhost:5030'}, api=object())
    engine.command('defer_track', tracks[2]['id'])
    window.receive(store.snapshot())
    app.processEvents()
    assert detail.restore.isVisible() and not detail.skip.isVisible()
    window.set_filter('suspicious')
    assert window.table.rowCount() == 1 and window.chips['suspicious'].text() == 'Şüpheli kalite 1'
    window.table.selectRow(0)
    app.processEvents()
    assert detail.better.isVisible()
    detail.better.click()
    assert commands(worker) == [('upgrade_track', tracks[0]['id'])]
    window.set_filter('all')
    window.table.selectRow(2)
    app.processEvents()
    window.grab().save(str(HERE / 'gui-preview.png'))

    window.toggle_theme()
    app.processEvents()
    assert window.dark
    window.grab().save(str(HERE / 'gui-dark-preview.png'))
    reopened = Window(root)
    assert reopened.dark
    reopened.quitting = True
    reopened.close()
    window.toggle_theme()

    window.open_logs()
    assert 'Tim Deluxe JAS' in window.logs.toPlainText()
    window.log_dialog.hide()
    assert 'JAS' in window.latest_event.text()

    dialog = ImportDialog(source, window)
    assert dialog.ok.isEnabled() and dialog.preview.rowCount() == 4
    assert dialog.payload()['mode'] == 'fast' and dialog.payload()['name'] == 'Akşam Seti'
    csv_source = root / 'spotify.csv'
    csv_source.write_text('Track Name,Artist Name(s),Duration (ms)\nDon\'t Stop,Prospa,185000\n', encoding='utf-8')
    dialog = ImportDialog(csv_source, window)
    assert dialog.ok.isEnabled() and dialog.preview.item(0, 2).text() == '3:05'
    assert dialog.payload()['layout'] == 'csv'
    # Screenshot import: editable preview from OCR lines (stubbed, then real Windows OCR).
    def wait(dialog):
        deadline = time.time() + 60
        while dialog.thread.isRunning() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)
        app.processEvents()

    image = QImage(900, 160, QImage.Format.Format_RGB32)
    image.fill(QColor('#1e1e1e'))
    painter = QPainter(image)
    painter.setPen(QColor('#e6e6e6'))
    painter.setFont(QFont('Segoe UI', 13))
    for row, text in enumerate(['00:00:00 Disco Party Baby (Original Mix) by Vampire Sex',
                                '00:03:12 Tell Me by Marius Acke', '00:06:20 Be Real by Kolter']):
        painter.drawText(12, 40 + row * 40, text)
    painter.end()
    dialog = OcrDialog(image, window, recognize=lambda img: ['00:00:00 Tell Me by Marius Acke', '01', 'Kolter - Be Real'])
    wait(dialog)
    assert dialog.lines() == ['Marius Acke - Tell Me', 'Kolter - Be Real'] and dialog.ok.isEnabled()
    dialog.text.setPlainText('Marius Acke - Tell Me\nBe Real Kolter')
    assert not dialog.ok.isEnabled() and 'ayırıcısı olmayan satır: 2' in dialog.summary.text()
    dialog.reject()
    if os.name == 'nt':
        real = OcrDialog(image, window)
        wait(real)
        assert real.lines() == ['Vampire Sex - Disco Party Baby (Original Mix)', 'Marius Acke - Tell Me',
                                'Kolter - Be Real'], real.lines()
        real.grab().save(str(HERE / 'ocr-preview.png'))
        real.reject()
    window.quitting = True
    window.close()
    store.db.close()
print('GUI smoke passed: chips, details panel, candidates, manual search, choices, import (TXT/CSV), themes.')
