"""Persistent playlist jobs. One worker owns API writes; SQLite survives restarts."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import time
import unicodedata
import uuid
from dataclasses import asdict
from pathlib import Path, PureWindowsPath
from urllib.parse import quote

import mutagen
import requests
from rapidfuzz import fuzz
from downloader import Candidate, Track, SlskdClient, BatchDownloader, get_ci
from slskd_runtime import SlskdRuntime


def norm(text):
    text = unicodedata.normalize('NFKD', text.replace('_', ' ')).casefold()
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"['’`‐‑]", '', text)
    return re.sub(r'[^\w]+', ' ', text).strip()


def safe_name(text):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text).strip(' .')[:150]
    if not name or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(10)), *('LPT'+str(i) for i in range(10))}:
        name = 'Playlist_' + name
    return name


def parse_tracks(path, layout='artist_title'):
    tracks, seen = [], set()
    for n, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        line = re.sub(r'^\d{1,4}[.)]\s+', '', line)
        parts = re.split(r'\s+[-–—]\s+', line)
        if len(parts) < 2 or not all(p.strip() for p in parts):
            raise ValueError(f'Satır {n}: sanatçı ve şarkıyı boşluk-tire-boşluk ile ayırın.')
        artist, title = (parts[-1], ' - '.join(parts[:-1])) if layout == 'title_artist' else (parts[0], ' - '.join(parts[1:]))
        track = Track(artist.strip(), title.strip(), n)
        if track.key not in seen:
            tracks.append(track)
            seen.add(track.key)
    if not tracks:
        raise ValueError('TXT dosyasında parça bulunamadı.')
    return tracks


VERSIONS = ('live', 'concert', 'karaoke', 'cover', 'tribute', 'instrumental', 'remix', 'mix', 'edit', 'sped up', 'slowed', 'nightcore', 'extended', 'remaster', 'remastered')


def phrase(text, target):
    return bool(target) and f' {target} ' in f' {text} '


def identity_title(text):
    text = re.sub(r'\s*[\[(](?:feat\.?|ft\.?|with|featuring)\s+.*?[\])]', '', text, flags=re.I)
    return norm(text)


def comparable_title(text):
    # Extended is accepted; remix names and every other version remain significant.
    return re.sub(r'\s+', ' ', re.sub(r'\bextended(?:\s+mix)?(?=\s+(?:remix|edit)\b|$)', '', text)).strip()


def quality_allowed(c, manual=False):
    mb=c.size/1048576
    return ((c.extension=='flac' and 5<=mb<=500) or
            (c.extension=='mp3' and (c.bitrate is None and manual or (c.bitrate or 0)>=320) and 2<=mb<=80))


def quality_label(data):
    ext=data.get('extension','').lower()
    if ext=='mp3': return 'MP3 · '+(str(data['bitrate'])+'k' if data.get('bitrate') else '? kbps')
    if ext=='flac':
        bits=data.get('bit_depth');rate=data.get('sample_rate')
        return 'FLAC'+(' · '+str(bits)+' bit' if bits else '')+(' / '+f'{rate/1000:g}k' if rate else '')
    return ext.upper() or '—'


def score_candidate(track, c):
    """Identity gates are independent of quality and availability bonuses."""
    parts = PureWindowsPath(c.filename.replace('/', '\\')).parts
    stem = re.sub(r'^\s*\d{1,3}(?:[-. ]+)', '', PureWindowsPath(parts[-1]).stem)
    stem_n = identity_title(stem)
    # Do not split '&': Oden & Fatzo and Borai & Denham Audio are artist identities.
    lead = norm(re.split(r',|\s+(?:feat\.?|ft\.?|featuring)\s+', track.artist, maxsplit=1, flags=re.I)[0])
    contexts = [norm(p) for p in parts[-4:-1]] + [stem_n]
    artist_ok = any(phrase(p, lead) for p in contexts)
    title = identity_title(track.title)
    stripped = stem_n
    credits = [norm(p) for p in re.split(r',|\s+(?:feat\.?|ft\.?)\s+', track.artist, flags=re.I)]
    for credit in sorted(credits, key=len, reverse=True):
        # Remix authors may also appear in the artist credits; retain their name
        # when the requested title includes it (Franky Rizardo Remix, etc.).
        if phrase(title, credit): continue
        if phrase(stripped, credit):
            stripped = re.sub(r'(?<!\w)' + re.escape(credit) + r'(?!\w)', ' ', stripped)
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    title=comparable_title(title)
    stripped=comparable_title(stripped)
    title_similarity = fuzz.ratio(title, stripped)
    # A title being a subset of a different title must not score as an exact match.
    title_ok = title == stripped or (len(title.split()) >= 3 and title_similarity >= 96)
    extra_versions = [v for v in VERSIONS if v!='extended' and phrase(comparable_title(stem_n), v) and not phrase(title, v)]
    missing_versions = [v for v in VERSIONS if v!='extended' and phrase(title, v) and not phrase(comparable_title(stem_n), v)]
    # A parent artist/album directory can identify the artist, but album names
    # must not introduce version penalties into an otherwise correct filename.
    quality = c.extension == 'flac' or (c.extension == 'mp3' and (c.bitrate or 0) >= 320)
    mb = c.size / 1048576
    size_ok = 5 <= mb <= 500 if c.extension == 'flac' else 2 <= mb <= 80
    c.eligible = bool(artist_ok and title_ok and quality and size_ok and not extra_versions and not missing_versions)
    c.score = round(min(100, 40 * artist_ok + .55 * title_similarity + (5 if c.extension == 'flac' else 3)), 1)
    if not c.eligible:
        c.score = min(c.score, 89)
    reasons = []
    if not artist_ok: reasons.append('Ana sanatçı doğrulanamadı')
    if not title_ok: reasons.append(f'Başlık eşleşmesi %{title_similarity:.0f}')
    if extra_versions or missing_versions: reasons.append('Sürüm farklı: ' + ', '.join(extra_versions + missing_versions))
    if not quality:
        reasons.append('MP3 bitrate bilinmiyor; elle seçilirse indirildikten sonra doğrulanır'
                       if c.extension=='mp3' and c.bitrate is None else 'Yalnızca FLAC / MP3 320')
    if not size_ok: reasons.append('Dosya boyutu şüpheli')
    c.reason = '; '.join(reasons) or ('Kimlik doğrulandı; FLAC kaynağı spektral olarak doğrulanmadı' if c.extension == 'flac' else 'Kimlik ve kalite doğrulandı')
    return c


def queries(track):
    artist = re.split(r',|\s+(?:feat\.?|ft\.?)\s+', track.artist, maxsplit=1, flags=re.I)[0]
    def search_text(value):
        value=re.sub(r'\s*[\[(](?:feat\.?|ft\.?|with|featuring)\s+.*?[\])]', '', value, flags=re.I)
        value=unicodedata.normalize('NFKD',value.replace('_',' ')).casefold().replace('’',"'")
        value=''.join(c for c in value if not unicodedata.combining(c))
        return re.sub(r'\s+',' ',re.sub(r"[^\w\s']",' ',value)).strip()
    title = search_text(track.title)
    core = re.split(r'\s+[-–—]\s+', track.title, maxsplit=1)[0]
    if core == track.title and re.search(r'(?:\.{3}|…)$', core):
        # A copied display label may end in an incomplete word.
        core = re.sub(r'\s+\S+(?:\.{3}|…)$', '', core)
    core = search_text(core) or title
    artist_terms = search_text(artist)
    words = artist_terms.split() + title.split()
    # Soulseek is token search; omit punctuation and very short words from first query.
    first = ' '.join(w for w in words if len(w) > 1)
    focused = ' '.join(w for w in (artist_terms+' '+core).split() if len(w)>1)
    # Keep title-only searches for distinctive titles; short names produce
    # thousands of unrelated files and hide rare peers behind response limits.
    result = [first, focused]
    if len(title.split()) >= 3: result.append(title)
    if truncated_input(track) and len(core.split()) >= 2: result.append(core)
    if len(artist_terms.split()) >= 2: result.append(artist_terms)
    return list(dict.fromkeys(q for q in result if q))


def truncated_input(track):
    return any(len(value) > 10 and re.search(r'(?:\.{3}|…)$', value.strip())
               for value in (track.artist,track.title))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def audio_metadata(path):
    """Read common tags without treating a missing tag as an error."""
    audio=mutagen.File(path,easy=True)
    if audio is None or not getattr(audio,'info',None):return {}
    def first(name):
        value=audio.get(name,[]) if getattr(audio,'tags',None) is not None else []
        if isinstance(value,(list,tuple)):value=value[0] if value else ''
        return str(value or '').strip()
    return {'length':float(getattr(audio.info,'length',0) or 0),'album':first('album'),
            'genre':first('genre'),'tracknumber':first('tracknumber'),
            'bitrate':round(float(getattr(audio.info,'bitrate',0) or 0)/1000),
            'bit_depth':getattr(audio.info,'bits_per_sample',None),
            'sample_rate':getattr(audio.info,'sample_rate',None),
            'extension':Path(path).suffix[1:].lower()}


def write_track_number(path,position):
    """Write only the playlist order tag; preserve all other audio metadata."""
    try:
        audio=mutagen.File(path,easy=True)
        if audio is None:return False
        if getattr(audio,'tags',None) is None:audio.add_tags()
        audio['tracknumber']=[str(position)];audio.save()
        saved=mutagen.File(path,easy=True)
        values=saved.get('tracknumber',[]) if saved is not None and getattr(saved,'tags',None) is not None else []
        return str(values[0] if isinstance(values,(list,tuple)) and values else values)==str(position)
    except (OSError,ValueError,TypeError,mutagen.MutagenError):
        return False


class Store:
    def __init__(self, root, output_root=None):
        self.root = Path(root)
        self.output_root = Path(output_root) if output_root else self.root / 'Playlists'
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'playlists.sqlite3', timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,name TEXT,folder TEXT UNIQUE,source TEXT,mode TEXT,status TEXT,created REAL);
          CREATE TABLE IF NOT EXISTS tracks(id TEXT PRIMARY KEY,job TEXT REFERENCES jobs(id),key TEXT,position INTEGER,artist TEXT,title TEXT,
            status TEXT DEFAULT 'pending',detail TEXT DEFAULT '',path TEXT DEFAULT '',hash TEXT DEFAULT '',next_search REAL DEFAULT 0,
            rounds INTEGER DEFAULT 0, variant INTEGER DEFAULT 0,search_id TEXT,search_deadline REAL DEFAULT 0, UNIQUE(job,key));
          CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,track TEXT REFERENCES tracks(id),peer TEXT,filename TEXT,data TEXT,
            score REAL,eligible INTEGER,created REAL, UNIQUE(track,peer,filename));
          CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,track TEXT REFERENCES tracks(id),peer TEXT,filename TEXT,size INTEGER,
            data TEXT,transfer_id TEXT,status TEXT,bytes INTEGER DEFAULT 0,speed REAL DEFAULT 0,stage TEXT DEFAULT '',created REAL,
            detail TEXT DEFAULT '',cancel_requested INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT,created REAL,message TEXT);
          CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE IF NOT EXISTS approvals(track TEXT PRIMARY KEY REFERENCES tracks(id),candidate TEXT REFERENCES candidates(id),created REAL);
          CREATE TABLE IF NOT EXISTS track_choices(track TEXT PRIMARY KEY REFERENCES tracks(id),choice TEXT,previous_status TEXT,created REAL);
        ''')
        self.db.commit()

    def rows(self, sql, args=()): return [dict(r) for r in self.db.execute(sql, args)]
    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        self.db.execute(sql, args)
        self.db.commit()

    def update(self, table, ident, **values):
        self.execute(f'UPDATE {table} SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?', (*values.values(), ident))

    def event(self, job, message):
        self.execute('INSERT INTO events(job,created,message) VALUES(?,?,?)', (job, time.time(), message))
        self.execute('DELETE FROM events WHERE id < (SELECT MAX(id)-5000 FROM events)')

    def setting(self, key, default='0'):
        row = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return row['value'] if row else default

    def set_setting(self, key, value):
        self.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))

    def add_job(self, source, layout, mode='fast'):
        tracks = parse_tracks(source, layout)
        ident = str(uuid.uuid4())
        folder = self.output_root / safe_name(Path(source).stem)
        base, n = folder, 2
        while folder.exists() or self.one('SELECT id FROM jobs WHERE folder=?', (str(folder),)):
            folder = base.with_name(base.name + f' ({n})')
            n += 1
        folder.mkdir(parents=True)
        snapshot = self.root / 'imports' / (ident + '.txt')
        snapshot.parent.mkdir(exist_ok=True)
        shutil.copy2(source, snapshot)
        with self.db:
            self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)', (ident, Path(source).stem, str(folder), str(snapshot), mode, 'paused', time.time()))
            for position, t in enumerate(tracks, 1):
                self.db.execute('INSERT INTO tracks(id,job,key,position,artist,title) VALUES(?,?,?,?,?,?)', (str(uuid.uuid4()), ident, t.key, position, t.artist, t.title))
        self.event(ident, f'{len(tracks)} parça içe aktarıldı. Başlat düğmesiyle başlayın.')
        return ident

    def snapshot(self):
        jobs = self.rows('SELECT * FROM jobs ORDER BY created')
        tracks = self.rows('SELECT * FROM tracks ORDER BY position')
        attempts = self.rows('SELECT * FROM attempts ORDER BY created')
        for a in attempts:
            a['quality']=quality_label(json.loads(a.pop('data')))
        approvals=self.rows('SELECT track,candidate FROM approvals')
        return {'jobs': jobs, 'tracks': tracks, 'attempts': attempts,
                'approvals': approvals,
                'events': self.rows('SELECT * FROM events ORDER BY id DESC LIMIT 150'),
                'next_search': float(self.setting('next_search')), 'connection': self.setting('connection', 'Bağlanıyor…')}


class Api(SlskdClient):
    def _request(self, method, endpoint, **kwargs):
        # Never surface arbitrary server bodies/credential-containing exception dumps.
        try:
            r = self.session.request(method, self._url(endpoint), timeout=(4, 12), **kwargs)
        except requests.RequestException:
            raise RuntimeError('slskd bağlantısı yanıt vermedi; otomatik yeniden denenecek.') from None
        if r.status_code == 401:
            raise PermissionError('slskd oturumu yenilenmeli.')
        if r.status_code >= 400:
            err = RuntimeError(f'slskd HTTP {r.status_code}')
            err.status = r.status_code
            raise err
        return r

    def create_search(self, query, ident):
        return self._request('POST', '/searches', json={'id':ident,'searchText':query,
            'searchTimeout':20000,'responseLimit':250,'fileLimit':10000,'filterResponses':True}).json()

    def enqueue(self, a):
        c = json.loads(a['data'])
        return self._request('POST', '/transfers/downloads/batches', json={'id':a['id'],
            'username':a['peer'],'files':[{'filename':a['filename'],'size':a['size']}],
            'options':{'destination':a['stage'],'externalId':'SoulseekAuto:'+a['track']}}).json()

    def batch(self, ident): return self._request('GET', '/transfers/downloads/batches/'+quote(ident, safe='')).json()
    def all_downloads(self):
        users=self._request('GET','/transfers/downloads').json()
        return {str(get_ci(f,'id')):f for u in users for d in get_ci(u,'directories',[]) for f in get_ci(d,'files',[])}
    def cancel(self, peer, ident):
        self._request('DELETE', f'/transfers/downloads/{quote(peer,safe="")}/{quote(ident,safe="")}?remove=false')


ACTIVE = ('submitting', 'queued', 'downloading', 'finishing')


class Engine:
    def __init__(self, store, config, api=None, validator=None):
        self.s = store
        self.config = dict(config, prompt_for_web_credentials=False)
        self.api = api or Api(self.config, logging.getLogger('playlist'))
        self.runtime = SlskdRuntime(self.config) if api is None else None
        self.validator = validator or self.validate_audio
        self.connected = False
        self.next_connect = 0
        self.next_poll = 0
        self.next_fill = 0
        self.next_export = 0
        self.source_root = Path(os.path.expandvars(config.get('slskd_download_dir', r'%LOCALAPPDATA%\slskd\downloads'))).resolve()
        self.rescore_saved()
        self.backfill_track_numbers()

    def rescore_saved(self):
        if self.s.setting('scoring_revision')=='separator-normalization-2': return
        with self.s.db:
            for r in self.s.rows("SELECT c.*,t.artist,t.title,t.position FROM candidates c JOIN tracks t ON t.id=c.track WHERE t.status!='done'"):
                c=score_candidate(Track(r['artist'],r['title'],r['position']),Candidate(**json.loads(r['data'])))
                self.s.db.execute('UPDATE candidates SET data=?,score=?,eligible=? WHERE id=?',
                    (json.dumps(asdict(c),ensure_ascii=False),c.score,int(c.eligible),r['id']))
            self.s.db.execute("UPDATE tracks SET status='pending',rounds=0,variant=0,next_search=0,detail='Eşleşme düzeltildi; kaynak yeniden aranacak' WHERE status IN ('review','not_found') AND id IN (SELECT track FROM candidates WHERE eligible=1 AND score>=?)",(float(self.config.get('auto_download_threshold',92)),))
        self.s.set_setting('scoring_revision','separator-normalization-2')

    def backfill_track_numbers(self):
        if self.s.setting('tracknumber_revision')=='playlist-position-1':return
        changed=failed=0
        playlist_root=(self.s.root/'Playlists').resolve()
        backup_root=self.s.root/'backups'/'playlist-order-originals'
        for t in self.s.rows("SELECT * FROM tracks WHERE status='done' AND path!=''"):
            path=Path(t['path'])
            if not path.is_file():continue
            try:
                path=path.resolve()
                if playlist_root not in path.parents:failed+=1;continue
                backup=backup_root/path.relative_to(playlist_root)
                current_hash=digest(path)
                if current_hash!=t['hash']:
                    # Recover a crash between atomic replacement and DB update,
                    # only when our original backup proves the previous hash.
                    if (backup.is_file() and digest(backup)==t['hash'] and
                            audio_metadata(path).get('tracknumber')==str(t['position'])):
                        self.validate_audio(path,0)
                        self.s.update('tracks',t['id'],hash=current_hash,detail='Tamamlandı · sıra etiketi: '+str(t['position']))
                        changed+=1;continue
                    failed+=1;continue
                current=audio_metadata(path)
                if current.get('tracknumber')==str(t['position']):continue
                backup.parent.mkdir(parents=True,exist_ok=True)
                if not backup.exists():shutil.copy2(path,backup)
                if digest(backup)!=t['hash']:failed+=1;continue
                staged=path.with_name('.'+uuid.uuid4().hex+path.suffix)
                try:
                    shutil.copyfile(path,staged)
                    if digest(staged)!=t['hash']:raise ValueError('Geçici kopya doğrulanamadı')
                    if not write_track_number(staged,t['position']):raise ValueError('Sıra etiketi yazılamadı')
                    self.validate_audio(staged,0)
                    tagged_hash=digest(staged)
                    staged.replace(path)
                    self.s.update('tracks',t['id'],hash=tagged_hash,detail='Tamamlandı · sıra etiketi: '+str(t['position']))
                    changed+=1
                finally:
                    if staged.exists():staged.unlink()
            except (OSError,ValueError,mutagen.MutagenError):failed+=1
        if not failed:self.s.set_setting('tracknumber_revision','playlist-position-1')
        if changed or failed:
            self.s.execute('INSERT INTO events(job,created,message) VALUES(?,?,?)',
                (None,time.time(),f'Sıra etiketi güncellendi: {changed} dosya'+(f' · kontrol gerekli: {failed}' if failed else '')))

    @staticmethod
    def validate_audio(path, size):
        if size and Path(path).stat().st_size != size:
            raise ValueError('Dosya boyutu beklenen boyutla eşleşmiyor.')
        audio = mutagen.File(path)
        if audio is None or not getattr(audio,'info',None) or audio.info.length <= 0:
            raise ValueError('Ses başlığı okunamadı.')
        if Path(path).suffix.lower()=='.mp3' and getattr(audio.info,'bitrate',0)<300000:
            raise ValueError('MP3 başlığındaki gerçek bitrate 320 kbps kalitesini doğrulamıyor.')

    def active(self, tid=None):
        return self.s.rows("SELECT * FROM attempts WHERE status IN ('submitting','queued','downloading','finishing')" + (' AND track=?' if tid else ''), (tid,) if tid else ())

    def command(self, action, payload):
        if action == 'import':
            return self.s.add_job(**payload)
        if action in ('skip_track','defer_track','restore_track'):
            t=self.s.one('SELECT * FROM tracks WHERE id=?',(payload,))
            if not t or t['status']=='done':raise ValueError('Tamamlanan parça bu işlemle değiştirilemez.')
            if self.active(payload):raise ValueError('Bu parçanın aktif transferi var; transfer bittikten sonra seçebilirsiniz.')
            with self.s.db:
                if action=='restore_track':
                    self.s.db.execute('DELETE FROM track_choices WHERE track=?',(payload,))
                    self.s.db.execute('DELETE FROM approvals WHERE track=?',(payload,))
                    self.s.db.execute("UPDATE tracks SET status='pending',next_search=0,detail='Tekrar işleme alındı' WHERE id=?",(payload,))
                    self.s.db.execute("UPDATE jobs SET status='active' WHERE id=? AND status='completed'",(t['job'],))
                else:
                    choice='skipped' if action=='skip_track' else 'deferred'
                    self.s.db.execute('INSERT OR REPLACE INTO track_choices VALUES(?,?,?,?)',(payload,choice,t['status'],time.time()))
                    self.s.db.execute('DELETE FROM approvals WHERE track=?',(payload,))
                    self.s.db.execute('UPDATE tracks SET status=?,detail=? WHERE id=?',(choice,'Kullanıcı kararı; adaylar korundu',payload))
            self.s.event(t['job'],f"{t['title']} · "+{'skip_track':'Atlandı (Pass)','defer_track':'Seçim sonraya bırakıldı','restore_track':'Tekrar işleme alındı'}[action])
            return
        if action == 'approve':
            r=self.s.one('SELECT * FROM candidates WHERE id=? AND track=?',(payload['candidate'],payload['track']))
            t=self.s.one('SELECT * FROM tracks WHERE id=?',(payload['track'],))
            if not r or not t or t['status']=='done': raise ValueError('Aday artık seçilemiyor; listeyi yenileyin.')
            if self.active(t['id']):raise ValueError('Bu parçanın aktif transferi var; seçim için tamamlanmasını bekleyin.')
            job=self.s.one('SELECT * FROM jobs WHERE id=?',(t['job'],))
            if job['mode']=='preview' or job['status']=='cancelled': raise ValueError('Arama testi / iptal edilen iş için indirme seçilemez.')
            if not quality_allowed(Candidate(**json.loads(r['data'])),manual=True): raise ValueError('Seçim için makul boyutlu FLAC veya MP3 gerekir; MP3 kalite indirme sonrası doğrulanır.')
            with self.s.db:
                self.s.db.execute('INSERT OR REPLACE INTO approvals VALUES(?,?,?)',(t['id'],r['id'],time.time()))
                self.s.db.execute('DELETE FROM track_choices WHERE track=?',(t['id'],))
                self.s.db.execute("UPDATE jobs SET status='active' WHERE id=? AND status='completed'",(t['job'],))
                self.s.db.execute("UPDATE tracks SET status='ready',detail='Seçtiğiniz aday sıraya alındı; iş duraklatılmışsa sürdürün.' WHERE id=?",(t['id'],))
            self.s.event(t['job'],f"Kullanıcı seçimi · {t['title']} · {r['peer']}")
            return
        job = self.s.one('SELECT * FROM jobs WHERE id=?', (payload,))
        if not job: return
        if action in ('start', 'pause', 'cancel'):
            self.s.update('jobs', payload, status={'start':'active','pause':'paused','cancel':'cancelled'}[action])
            self.s.event(payload, {'start':'İş sürdürülüyor.','pause':'Yeni aramalar duraklatıldı. Mevcut transferler izleniyor.','cancel':'İş iptal edildi; bu işe ait aktif istekler iptal edilecek. Dosyalar korunur.'}[action])
        elif action == 'retry':
            self.s.execute("UPDATE tracks SET next_search=0,variant=0 WHERE job=? AND status != 'done' AND search_id IS NULL", (payload,))
            self.s.update('jobs', payload, status='active')
        elif action == 'mode': pass

    def tick(self):
        now = time.time()
        if not self.connected:
            if now < self.next_connect: return
            if self.runtime is not None:
                ready,message=self.runtime.prepare()
                if not ready:
                    self.next_connect=time.time()+5
                    self.s.set_setting('connection',message)
                    return
            try:
                self.s.set_setting('connection','slskd hazır · kayıtlı bilgilerle oturum açılıyor')
                self.api.authenticate()
                if not self.api.server_connected(self.api.server()):
                    self.next_connect=time.time()+10
                    self.s.set_setting('connection','slskd hazır · Soulseek hesabının bağlanması bekleniyor')
                    return
                self.connected = True
                self.s.set_setting('connection', 'slskd · Soulseek bağlı')
            except PermissionError:
                self.next_connect=time.time()+30
                self.s.set_setting('connection','slskd oturumu açılamadı · kayıtlı web giriş bilgilerini kontrol edin')
                return
            except Exception:
                self.next_connect = now + 30
                self.s.set_setting('connection', 'slskd oturum / bağlantı kontrolü başarısız · 30 sn sonra yeniden denenecek')
                return
        try:
            if now >= self.next_poll:
                self.poll_transfers()
                self.next_poll = time.time() + 5
            self.collect_searches()
            if now >= self.next_fill:
                self.fill_slots()
                self.next_fill=time.time()+3
            self.schedule_search()
            if now>=self.next_export:
                for job in self.s.rows('SELECT id FROM jobs'):self.export(job['id'])
                self.next_export=time.time()+30
            for job in self.s.rows("SELECT * FROM jobs WHERE status='active'"):
                if job['mode']=='preview' and not self.s.one("SELECT id FROM tracks WHERE job=? AND status!='skipped' AND (rounds=0 OR search_id IS NOT NULL)",(job['id'],)):
                    self.s.update('jobs',job['id'],status='preview_complete')
                    self.s.event(job['id'],'İndirmesiz arama testi tamamlandı.')
                    continue
                if not self.s.one("SELECT id FROM tracks WHERE job=? AND status NOT IN ('done','skipped')", (job['id'],)):
                    self.s.update('jobs', job['id'], status='completed')
                    self.s.event(job['id'], 'Playlist tamamlandı.')
        except (RuntimeError, PermissionError, requests.RequestException):
            self.connected = False
            self.next_connect = time.time() + 20
            self.s.set_setting('connection', 'API bağlantısı yenileniyor · işler korunuyor')

    def schedule_search(self):
        now = time.time()
        if now < float(self.s.setting('next_search')): return
        if self.s.one('SELECT id FROM tracks WHERE search_id IS NOT NULL'): return
        row = self.s.one("""SELECT t.* FROM tracks t JOIN jobs j ON j.id=t.job WHERE j.status='active'
            AND t.status!='done' AND t.next_search<=? AND t.search_id IS NULL AND (j.mode!='preview' OR t.rounds=0)
            AND t.id NOT IN (SELECT track FROM approvals)
            AND t.id NOT IN (SELECT track FROM track_choices)
            ORDER BY t.rounds,t.next_search,j.created,t.position LIMIT 1""", (now,))
        if not row: return
        track = Track(row['artist'], row['title'], row['position'])
        qs = queries(track)
        if not qs: return
        sid = str(uuid.uuid4())
        query = qs[row['variant'] % len(qs)]
        # Persist intent BEFORE the network call; uncertain outcomes recover via GET.
        self.s.update('tracks', row['id'], search_id=sid, search_deadline=now+23, detail='Aranıyor: '+query)
        self.s.set_setting('next_search', now+max(30, float(self.config.get('search_cooldown_seconds',60))))
        self.s.event(row['job'], f"Aranıyor · {track.artist} — {track.title} · {query}")
        self.api.create_search(query, sid)

    def collect_searches(self):
        for t in self.s.rows('SELECT * FROM tracks WHERE search_id IS NOT NULL'):
            if time.time() < t['search_deadline']: continue
            job = self.s.one('SELECT * FROM jobs WHERE id=?', (t['job'],))
            try:
                responses = self.api.search_responses(t['search_id'])
            except Exception as e:
                if getattr(e,'status',None) != 404: raise
                responses = []
            track = Track(t['artist'],t['title'],t['position'])
            candidates = [score_candidate(track,c) for c in BatchDownloader._extract_candidates(responses,t['search_id'])]
            candidates.sort(key=lambda c:(c.eligible,c.score),reverse=True)
            # Retain all safe peers plus a bounded review set, not thousands of wrong files.
            retained = [c for c in candidates if c.eligible][:80] + [c for c in candidates if not c.eligible][:5]
            for c in retained:
                self.s.execute('''INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(track,peer,filename)
                    DO UPDATE SET data=excluded.data,score=excluded.score,eligible=excluded.eligible,created=excluded.created''',
                    (str(uuid.uuid4()),t['id'],c.peer,c.filename,json.dumps(asdict(c),ensure_ascii=False),c.score,int(c.eligible),time.time()))
            good = [c for c in candidates if c.eligible and c.score>=float(self.config.get('auto_download_threshold',92))]
            variant = t['variant']+1
            full_round = bool(good) or variant >= len(queries(track))
            active = self.active(t['id'])
            prior_good=self.s.one('SELECT id FROM candidates WHERE track=? AND eligible=1 AND score>=? LIMIT 1',
                                  (t['id'],float(self.config.get('auto_download_threshold',92))))
            prior_attempt=self.s.one('SELECT id FROM attempts WHERE track=? LIMIT 1',(t['id'],))
            reviewable=any(c.score>=float(self.config.get('review_threshold',75)) for c in candidates)
            state = ('waiting' if active else 'ready' if good else
                     'retry_wait' if prior_good and prior_attempt else
                     'review' if reviewable else 'not_found')
            if t['status']=='done': state='done'
            held=self.s.one('SELECT choice FROM track_choices WHERE track=?',(t['id'],))
            if held:state=held['choice']
            if self.s.one('SELECT track FROM approvals WHERE track=?',(t['id'],)) and t['status']!='done': state='waiting' if active else 'ready'
            delay = min(7200, 900 * (2**min(t['rounds'],3))) if full_round else 0
            detail = f'{len(candidates)} aday · {len(good)} güvenli eşleşme'
            if truncated_input(track): detail += ' · TXT adı kesilmiş görünüyor; tam adla yeniden ekleyin veya adayı elle seçin'
            self.s.update('tracks',t['id'],search_id=None,search_deadline=0,status=state,detail=detail,
                          variant=0 if full_round else variant,rounds=t['rounds']+int(full_round),next_search=time.time()+delay)
            self.s.event(t['job'],f"{track.artist} — {track.title}: {detail}")

    def recover_submission(self, a):
        try:
            result = self.api.batch(a['id'])
        except Exception as e:
            if getattr(e,'status',None)!=404: raise
            # Absence verified: repeat with the SAME batch ID, never a new one.
            try:
                result = self.api.enqueue(a)
            except Exception as refusal:
                if getattr(refusal,'status',None) in (400,403,404):
                    self.s.update('attempts',a['id'],status='failed',detail=f'Peer isteği reddedildi (HTTP {refusal.status})')
                    return
                raise
        batch = get_ci(result,'batch',result) or {}
        transfers = get_ci(batch,'transfers',[]) or []
        if transfers:
            transfer_id = get_ci(transfers[0],'id')
            if transfer_id:
                self.s.update('attempts',a['id'],transfer_id=str(transfer_id),status='queued')
                return
        # A request that timed out may still be building its batch on the server.
        # Allow that batch to settle before declaring an empty result a failure.
        if get_ci(result,'failures',[]) or time.time()-a['created']>120:
            self.s.update('attempts',a['id'],status='failed',detail='Peer isteği kabul etmedi veya batch boş döndü.')

    def fill_slots(self):
        active_total = len(self.active())
        global_cap=max(2,int(self.config.get('gui_max_pending_transfers',64)))
        for t in self.s.rows("SELECT t.*,j.mode FROM tracks t JOIN jobs j ON j.id=t.job WHERE j.status='active' AND j.mode!='preview' AND t.status!='done' AND t.id NOT IN (SELECT track FROM track_choices) ORDER BY t.rounds,t.position"):
            # Reuse a verified local copy from another playlist.
            reused = self.s.one("SELECT path,hash FROM tracks WHERE key=? AND status='done' AND path!=''", (t['key'],))
            if reused and Path(reused['path']).is_file() and digest(reused['path'])==reused['hash']:
                self.deliver(t,Path(reused['path']),reused['hash'],False)
                continue
            other = self.s.one("SELECT a.id FROM attempts a JOIN tracks x ON x.id=a.track WHERE x.key=? AND x.id!=? AND a.status IN ('submitting','queued','downloading','finishing')",(t['key'],t['id']))
            if other: continue
            active = self.active(t['id'])
            cap = 2 if t['mode']=='fast' else 1
            if len(active)>=cap or active_total>=global_cap: continue
            attempted = {a['peer'].casefold() for a in self.s.rows('SELECT peer FROM attempts WHERE track=? AND created>?',(t['id'],time.time()-86400))}
            available = []
            approval=self.s.one('SELECT candidate FROM approvals WHERE track=?',(t['id'],))
            rows=(self.s.rows('SELECT * FROM candidates WHERE id=?',(approval['candidate'],)) if approval else
                  self.s.rows('SELECT * FROM candidates WHERE track=? AND eligible=1 AND score>=? AND created>?',(t['id'],float(self.config.get('auto_download_threshold',92)),time.time()-86400)))
            for r in rows:
                c = Candidate(**json.loads(r['data']))
                if approval and not quality_allowed(c,manual=True): continue
                if c.peer.casefold() not in attempted: available.append(c)
            failed_peers={r['peer'].casefold():r['failures'] for r in self.s.rows(
                "SELECT peer,COUNT(*) failures FROM attempts WHERE track=? AND status='failed' GROUP BY peer",(t['id'],))}
            available.sort(key=lambda c:(c.extension=='flac',-failed_peers.get(c.peer.casefold(),0),
                                         c.free_upload_slot is True,-(c.queue_length or 0),c.upload_speed or 0,c.score),reverse=True)
            if not active and attempted and (not available or len(attempted)>=int(self.config.get('max_peer_attempts',3))):
                detail='Uygun peer’ler denendi; yeni kaynak / yeniden deneme zamanı bekleniyor.'
                if t['status']!='retry_wait' or t['detail']!=detail:
                    self.s.update('tracks',t['id'],status='retry_wait',detail=detail)
            for c in available:
                if len(active)>=cap or active_total>=global_cap: break
                if c.peer.casefold() in attempted: continue
                # Three distinct peers per day; no retry storm against one peer.
                if len(attempted)>=int(self.config.get('max_peer_attempts',3)): break
                if self.s.one("SELECT id FROM attempts WHERE peer=? AND filename=? AND status IN ('submitting','queued','downloading','finishing')",(c.peer,c.filename)): continue
                ident = str(uuid.uuid4())
                stage = '_SoulseekAuto/'+ident
                self.s.execute('INSERT INTO attempts(id,track,peer,filename,size,data,status,stage,created) VALUES(?,?,?,?,?,?,?,?,?)',
                    (ident,t['id'],c.peer,c.filename,c.size,json.dumps(asdict(c)), 'submitting',stage,time.time()))
                a=self.s.one('SELECT * FROM attempts WHERE id=?',(ident,))
                self.recover_submission(a)
                active=self.active(t['id'])
                active_total=len(self.active())
                attempted.add(c.peer.casefold())
                self.s.update('tracks',t['id'],status='waiting' if active else 'retry_wait',
                    detail='Peer yanıtı / indirme bekleniyor' if active else 'Peer isteği kabul etmedi; başka kaynak bekleniyor.')
                self.s.event(t['job'],f"Peer isteği · {t['title']} · {c.peer} · {c.extension.upper()} · %{c.score:.0f}")

    def poll_transfers(self):
        # Failed attempts with known IDs remain visible in slskd, but only active
        # attempts are polled. Never cancel another application's downloads.
        attempts=self.active()
        snapshot=self.api.all_downloads() if attempts and hasattr(self.api,'all_downloads') else None
        for a in attempts:
            t=self.s.one('SELECT * FROM tracks WHERE id=?',(a['track'],))
            job=self.s.one('SELECT * FROM jobs WHERE id=?',(t['job'],))
            if a['status']=='submitting':
                if job['status']=='cancelled' or t['status']=='done':
                    try:
                        batch=self.api.batch(a['id'])
                        transfers=get_ci(get_ci(batch,'batch',batch),'transfers',[]) or []
                        if transfers:
                            self.s.update('attempts',a['id'],transfer_id=str(get_ci(transfers[0],'id')),status='queued')
                        else: self.s.update('attempts',a['id'],status='cancelled')
                    except Exception as e:
                        if getattr(e,'status',None)!=404: raise
                        self.s.update('attempts',a['id'],status='cancelled')
                    continue
                self.recover_submission(a)
                continue
            try:
                if snapshot is not None and a['transfer_id'] in snapshot:
                    transfer=snapshot[a['transfer_id']]
                else:transfer=self.api.get_download(a['peer'],a['transfer_id'])
            except Exception as e:
                if getattr(e,'status',None)==404:
                    self.s.update('attempts',a['id'],status='failed',detail='Transfer slskd kayıtlarında bulunamadı')
                    continue
                raise
            state=str(get_ci(transfer,'state',''))
            count=int(get_ci(transfer,'bytesTransferred',0) or 0)
            if not a['size']:
                a['size']=int(get_ci(transfer,'size',0) or 0)
                self.s.update('attempts',a['id'],size=a['size'])
            speed=float(get_ci(transfer,'averageSpeed',0) or 0)
            self.s.update('attempts',a['id'],bytes=count,speed=speed,detail=state)
            completed='Completed' in state
            success=completed and 'Succeeded' in state
            if success:
                path=self.locate(a)
                if not path:
                    self.s.update('attempts',a['id'],status='finishing',detail='Tamamlandı; yerel dosya konumu doğrulanıyor')
                    continue
                try:
                    self.validator(path,a['size'])
                except Exception:
                    self.s.update('attempts',a['id'],status='failed',detail='Dosya doğrulaması başarısız; dosya korundu')
                    self.s.event(t['job'],f"Doğrulama başarısız · {t['title']} · dosya silinmedi")
                    continue
                if t['status']=='done' or job['status']=='cancelled':
                    # Only our own uniquely staged complete duplicates are moved.
                    self.quarantine(a,path)
                else:
                    self.deliver(t,path,digest(path),True)
                self.s.update('attempts',a['id'],status='succeeded')
            elif completed:
                self.s.update('attempts',a['id'],status='cancelled' if a['cancel_requested'] else 'failed',detail=state)
                if t['status']!='done':
                    self.s.update('tracks',t['id'],status='waiting' if len(self.active(t['id'])) else 'not_found',detail='Peer tamamlayamadı; diğer kaynaklar deneniyor',next_search=min(t['next_search'],time.time()+300))
                self.s.event(t['job'],f"Peer sonlandı · {t['title']} · {a['peer']} · {state}")
            elif t['status']=='done' or job['status']=='cancelled':
                if not a['cancel_requested'] or time.time()-a['created']>60:
                    self.api.cancel(a['peer'],a['transfer_id'])
                    self.s.update('attempts',a['id'],cancel_requested=1)
            else:
                new='downloading' if count>0 and 'InProgress' in state else 'queued'
                self.s.update('attempts',a['id'],status=new)
                self.s.update('tracks',t['id'],status='downloading' if new=='downloading' else 'waiting',detail=state)

    def locate(self,a):
        if a['stage']:
            folder=(self.source_root/a['stage']).resolve()
            if self.source_root not in folder.parents: raise ValueError('Geçersiz geçici klasör')
            found=[p for p in folder.glob('*') if p.is_file() and p.stat().st_size==a['size'] and p.suffix.lower() in ('.flac','.mp3')]
        else:
            # Legacy migration: match full basename + expected size, refuse ambiguity.
            name=PureWindowsPath(a['filename']).name
            found=[p for p in self.source_root.rglob('*') if p.is_file() and p.name==name and (not a['size'] or p.stat().st_size==a['size'])]
        return found[0] if len(found)==1 else None

    def quarantine(self,a,path):
        if not a['stage']: return
        folder=self.s.root/'Quarantine'/a['id']
        folder.mkdir(parents=True,exist_ok=True)
        destination=folder/path.name
        if destination.exists(): return
        shutil.move(str(path),str(destination))

    def deliver(self,t,source,sha,move):
        job=self.s.one('SELECT * FROM jobs WHERE id=?',(t['job'],))
        folder=Path(job['folder'])
        folder.mkdir(parents=True,exist_ok=True)
        temporary=folder/('.'+t['id']+source.suffix)
        shutil.copyfile(source,temporary)
        if digest(temporary)!=sha: raise ValueError('Dosya kopyası doğrulanamadı')
        tagged=write_track_number(temporary,t['position'])
        self.validator(temporary,0)
        final_sha=digest(temporary)
        dest=folder/(safe_name(source.stem)+source.suffix);n=2
        while dest.exists() and digest(dest)!=final_sha:
            dest=folder/(safe_name(source.stem)+f' ({n})'+source.suffix);n+=1
        if dest.exists():temporary.unlink()
        else:temporary.replace(dest)
        # Durable record before removing only the verified source file.
        detail='Tamamlandı · sıra etiketi: '+str(t['position']) if tagged else 'Tamamlandı · sıra etiketi yazılamadı'
        self.s.update('tracks',t['id'],status='done',path=str(dest),hash=final_sha,detail=detail)
        self.export(job['id'])
        if move and source.resolve()!=dest.resolve() and self.source_root in source.resolve().parents:
            source.unlink()
        self.s.event(job['id'],f"Tamamlandı · {t['artist']} — {t['title']}")

    def export(self,job_id):
        job=self.s.one('SELECT * FROM jobs WHERE id=?',(job_id,))
        tracks=self.s.rows('SELECT * FROM tracks WHERE job=? ORDER BY position',(job_id,))
        folder=Path(job['folder'])
        lines=['#EXTM3U']+[Path(t['path']).name for t in tracks if t['status']=='done' and Path(t['path']).is_file()]
        temp=folder/'.playlist.tmp'
        temp.write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')
        temp.replace(folder/(safe_name(job['name'])+'.m3u8'))
        with (folder/'rapor.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(['sıra','sanatçı','şarkı','durum','dosya','açıklama'])
            for t in tracks: w.writerow([t['position'],t['artist'],t['title'],t['status'],t['path'],t['detail']])

    def migrate_legacy(self):
        if self.s.setting('legacy_migrated','') or not (self.s.root/'state.json').exists(): return
        job=self.s.setting('legacy_migration_job','')
        if not job:
            job=self.s.add_job(self.s.root/'songs.txt','title_artist','fast')
            self.s.set_setting('legacy_migration_job',job)
        state=json.loads((self.s.root/'state.json').read_text(encoding='utf-8'))
        for t in self.s.rows('SELECT * FROM tracks WHERE job=?',(job,)):
            if t['status']=='done' or self.active(t['id']):continue
            r=state.get('tracks',{}).get(t['key'],{})
            if r.get('status')=='succeeded' and r.get('playlist_path') and Path(r['playlist_path']).is_file():
                source=Path(r['playlist_path'])
                try:
                    self.validator(source,0)
                    self.deliver(t,source,digest(source),False)
                except (ValueError,mutagen.MutagenError):
                    self.s.update('tracks',t['id'],status='review',detail='Eski dosyanın ses başlığı doğrulanamadı; kaynak korundu, yeniden aranacak.')
                    self.s.event(job,f"Eski dosya doğrulanamadı · {t['title']} · kaynak dosya korundu")
            elif r.get('status')=='queued' and r.get('transfer_id'):
                a=str(uuid.uuid4())
                self.s.execute('INSERT INTO attempts(id,track,peer,filename,size,data,transfer_id,status,stage,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (a,t['id'],r['peer'],r['selected_filename'],r.get('size',0),'{}',r['transfer_id'],'queued','',time.time()))
                self.s.update('tracks',t['id'],status='waiting',next_search=0)
            # Old failures can be retried fairly; legacy reports remain as archive.
        self.s.update('jobs',job,status='active')
        self.s.set_setting('legacy_migrated',job)
        self.s.event(job,'Eski playlist ve aktif transferler aktarıldı. Eski dosyalar yedek olarak korunuyor.')
        self.export(job)
        return job
