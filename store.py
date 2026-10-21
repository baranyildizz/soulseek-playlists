"""SQLite persistence. The schema is append-only so existing databases keep working."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from matching import detect_layout, parse_tracks, quality_label

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, name TEXT, folder TEXT UNIQUE, source TEXT,
  mode TEXT, status TEXT, created REAL);
CREATE TABLE IF NOT EXISTS tracks(id TEXT PRIMARY KEY, job TEXT REFERENCES jobs(id), key TEXT, position INTEGER,
  artist TEXT, title TEXT, status TEXT DEFAULT 'pending', detail TEXT DEFAULT '', path TEXT DEFAULT '',
  hash TEXT DEFAULT '', next_search REAL DEFAULT 0, rounds INTEGER DEFAULT 0, variant INTEGER DEFAULT 0,
  search_id TEXT, search_deadline REAL DEFAULT 0, UNIQUE(job, key));
CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, track TEXT REFERENCES tracks(id), peer TEXT,
  filename TEXT, data TEXT, score REAL, eligible INTEGER, created REAL, UNIQUE(track, peer, filename));
CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, track TEXT REFERENCES tracks(id), peer TEXT,
  filename TEXT, size INTEGER, data TEXT, transfer_id TEXT, status TEXT, bytes INTEGER DEFAULT 0,
  speed REAL DEFAULT 0, stage TEXT DEFAULT '', created REAL, detail TEXT DEFAULT '',
  cancel_requested INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, created REAL, message TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS approvals(track TEXT PRIMARY KEY REFERENCES tracks(id),
  candidate TEXT REFERENCES candidates(id), created REAL);
CREATE TABLE IF NOT EXISTS track_choices(track TEXT PRIMARY KEY REFERENCES tracks(id), choice TEXT,
  previous_status TEXT, created REAL);
CREATE TABLE IF NOT EXISTS peer_stats(peer TEXT PRIMARY KEY, succeeded INTEGER DEFAULT 0,
  failed INTEGER DEFAULT 0, updated REAL);
CREATE TABLE IF NOT EXISTS library(path TEXT PRIMARY KEY, name TEXT, size INTEGER, mtime REAL, seen REAL);
'''
# Columns added after the first release: (table, column, declaration)
ADDED_COLUMNS = [
    ('jobs', 'layout', "TEXT DEFAULT ''"),
    ('tracks', 'search_started', 'REAL DEFAULT 0'),
    ('tracks', 'query', "TEXT DEFAULT ''"),
    ('attempts', 'progress_at', 'REAL DEFAULT 0'),
    ('attempts', 'manual', 'INTEGER DEFAULT 0'),
    ('tracks', 'duration', 'REAL'),
    ('tracks', 'album', "TEXT DEFAULT ''"),
    ('tracks', 'search_kind', "TEXT DEFAULT ''"),
    ('tracks', 'quality_note', "TEXT DEFAULT ''"),
    ('tracks', 'upgrade', 'INTEGER DEFAULT 0'),   # 1: finished file kept while a better source is sought
]
INDEXES = '''
CREATE INDEX IF NOT EXISTS tracks_job ON tracks(job, position);
CREATE INDEX IF NOT EXISTS tracks_key ON tracks(key);
CREATE INDEX IF NOT EXISTS candidates_track ON candidates(track, eligible, score);
CREATE INDEX IF NOT EXISTS attempts_track ON attempts(track, status);
CREATE INDEX IF NOT EXISTS attempts_status ON attempts(status);
CREATE INDEX IF NOT EXISTS library_name ON library(name);
'''
ACTIVE = ('submitting', 'queued', 'downloading', 'finishing')
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}


def safe_name(text: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text).strip(' .')[:150]
    if not name or name.split('.')[0].upper() in _RESERVED:
        name = 'Playlist_' + name
    return name


class Store:
    def __init__(self, root, output_root=None):
        self.root = Path(root)
        self.output_root = Path(output_root) if output_root else self.root / 'Playlists'
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'playlists.sqlite3', timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(SCHEMA)
        for table, column, declaration in ADDED_COLUMNS:
            existing = {r['name'] for r in self.db.execute(f'PRAGMA table_info({table})')}
            if column not in existing:
                self.db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
        self.db.executescript(INDEXES)
        self.db.commit()

    # -- generic helpers ----------------------------------------------------
    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def execute(self, sql, args=()):
        self.db.execute(sql, args)
        self.db.commit()

    def update(self, table, ident, **values):
        self.execute(f'UPDATE {table} SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?',
                     (*values.values(), ident))

    def event(self, job, message):
        self.db.execute('INSERT INTO events(job,created,message) VALUES(?,?,?)', (job, time.time(), message))
        self.db.execute('DELETE FROM events WHERE id < (SELECT MAX(id)-5000 FROM events)')
        self.db.commit()

    def setting(self, key, default='0'):
        row = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return row['value'] if row else default

    def set_setting(self, key, value):
        self.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))

    # -- jobs -----------------------------------------------------------------
    def add_job(self, source, layout='auto', mode='fast', name=None):
        if layout == 'auto':
            layout = detect_layout(source)
        tracks = parse_tracks(source, layout)
        ident = str(uuid.uuid4())
        name = ' '.join(str(name or Path(source).stem).split()) or Path(source).stem
        folder = self.output_root / safe_name(name)
        base, n = folder, 2
        while folder.exists() or self.one('SELECT id FROM jobs WHERE folder=?', (str(folder),)):
            folder = base.with_name(f'{base.name} ({n})')
            n += 1
        folder.mkdir(parents=True)
        snapshot = self.root / 'imports' / (ident + (Path(source).suffix.lower() or '.txt'))
        snapshot.parent.mkdir(exist_ok=True)
        shutil.copy2(source, snapshot)
        with self.db:
            self.db.execute('INSERT INTO jobs(id,name,folder,source,mode,status,created,layout) VALUES(?,?,?,?,?,?,?,?)',
                            (ident, name, str(folder), str(snapshot), mode, 'paused', time.time(), layout))
            for position, t in enumerate(tracks, 1):
                self.db.execute('INSERT INTO tracks(id,job,key,position,artist,title,duration,album) '
                                'VALUES(?,?,?,?,?,?,?,?)',
                                (str(uuid.uuid4()), ident, t.key, position, t.artist, t.title, t.duration, t.album))
        label = {'title_artist': 'Şarkı - Sanatçı', 'csv': 'CSV · süre bilgisiyle'}.get(layout, 'Sanatçı - Şarkı')
        self.event(ident, f'{len(tracks)} parça içe aktarıldı ({label}). Başlat düğmesiyle başlayın.')
        return ident

    def stored_layout(self, job) -> str:
        """Which layout produced the stored rows (older jobs did not record it)."""
        if job.get('layout'):
            return job['layout']
        first = self.one('SELECT artist,title FROM tracks WHERE job=? ORDER BY position LIMIT 1', (job['id'],))
        try:
            parsed = parse_tracks(job['source'], 'artist_title')[0]
        except (OSError, ValueError, IndexError):
            return ''
        if first and (first['artist'], first['title']) == (parsed.artist, parsed.title):
            return 'artist_title'
        return 'title_artist'

    def remove_job(self, job_id):
        """Forget a playlist; its folder and audio files stay on disk."""
        with self.db:
            tracks = '(SELECT id FROM tracks WHERE job=?)'
            for table, column in (('approvals', 'track'), ('track_choices', 'track'), ('candidates', 'track'),
                                  ('attempts', 'track')):
                self.db.execute(f'DELETE FROM {table} WHERE {column} IN {tracks}', (job_id,))
            self.db.execute('DELETE FROM tracks WHERE job=?', (job_id,))
            self.db.execute('DELETE FROM events WHERE job=?', (job_id,))
            self.db.execute('DELETE FROM jobs WHERE id=?', (job_id,))

    def relayout_job(self, job_id, layout) -> int:
        """Re-read the job's TXT snapshot with another column order; finished files are kept."""
        job = self.one('SELECT * FROM jobs WHERE id=?', (job_id,))
        tracks = parse_tracks(job['source'], layout)
        rows = self.rows('SELECT * FROM tracks WHERE job=? ORDER BY position', (job_id,))
        if len(tracks) != len(rows):
            return 0
        with self.db:
            for row, track in zip(rows, tracks):
                self.db.execute('DELETE FROM approvals WHERE track=?', (row['id'],))
                self.db.execute('DELETE FROM candidates WHERE track=?', (row['id'],))
                reset = {} if row['status'] in ('done', 'skipped', 'deferred') else {
                    'status': 'pending', 'detail': 'Sütun sırası düzeltildi; yeniden aranacak',
                    'rounds': 0, 'variant': 0, 'next_search': 0}
                values = {'artist': track.artist, 'title': track.title, 'key': f'{row["id"]}:tmp', **reset}
                self.db.execute('UPDATE tracks SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?',
                                (*values.values(), row['id']))
            for row, track in zip(rows, tracks):
                self.db.execute('UPDATE tracks SET key=? WHERE id=?', (track.key, row['id']))
            self.db.execute('UPDATE jobs SET layout=? WHERE id=?', (layout, job_id))
        return len(rows)

    # -- UI ---------------------------------------------------------------------
    def snapshot(self):
        attempts = self.rows('SELECT id,track,peer,filename,size,transfer_id,status,bytes,speed,stage,created,detail,'
                             'cancel_requested,data FROM attempts ORDER BY created')
        for a in attempts:
            try:
                a['quality'] = quality_label(json.loads(a.pop('data') or '{}'))
            except ValueError:
                a['quality'] = '—'
        return {'jobs': self.rows('SELECT * FROM jobs ORDER BY created'),
                'tracks': self.rows('SELECT * FROM tracks ORDER BY position'),
                'attempts': attempts,
                'approvals': self.rows('SELECT track,candidate FROM approvals'),
                'events': self.rows('SELECT * FROM events ORDER BY id DESC LIMIT 150'),
                'next_search': float(self.setting('next_search')),
                'connection': self.setting('connection', 'Bağlanıyor…'),
                'share_warning': self.setting('share_warning', '') == '1',
                'library_files': self.one('SELECT COUNT(*) n FROM library')['n']}
