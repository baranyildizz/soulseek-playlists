"""Playlist download engine.

One worker thread calls `tick()` a few times per second. Every step is
idempotent and persisted before network calls, so a crash or restart resumes
cleanly from the database.

Track lifecycle (tracks.status)
    pending      waiting for its next search
    ready        safe candidates found, waiting for a download slot
    waiting      a peer has accepted the request (queued remotely)
    downloading  bytes are arriving
    retry_wait   every suitable peer was tried; a fresh search is scheduled
    review       only near-matches found; the user may pick one
    not_found    nothing usable yet; searched again later with back-off
    done / skipped / deferred
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import time
import uuid
from pathlib import Path, PureWindowsPath

from audio import (NO_CUTOFF, audio_length, convert_to_aiff, digest, find_ffmpeg, spectral_cutoff,
                   validate_audio, write_track_number)
from matching import (AUDIO_EXTENSIONS, Candidate, Track, build_spec, duration_ok, duration_outliers,
                      extract_candidates, norm, quality_allowed, queries, rank_key, score_candidate)
from slskd_api import ApiError, ConnectionLost, SlskdApi, get_ci
from store import ACTIVE, Store, safe_name

DEFAULTS = {
    'auto_download_threshold': 92,
    'review_threshold': 75,
    'minimum_mp3_bitrate': 320,
    'accept_wav_aiff': True,
    'accept_unknown_mp3_bitrate': True,
    'max_parallel_searches': 3,
    'search_interval_seconds': 8,
    'search_timeout_seconds': 15,
    'search_max_wait_seconds': 60,
    'max_queries': 6,
    'enough_peers': 3,
    'peer_attempts_per_day': 10,
    'gui_max_pending_transfers': 64,
    'stall_minutes': 10,
    'queued_patience_minutes': 20,
    'check_fake_lossless': True,     # spectrum check of downloaded FLAC/AIFF/WAV/MP3 (needs ffmpeg)
    'fake_lossless_khz': 19.0,       # content ending below this looks like a low-bitrate MP3 source
    'output_format': 'original',     # 'original' or 'aiff' (FLAC/WAV converted for rekordbox/CDJ)
    'filename_template': 'original', # 'original' or 'artist_title' ("Artist - Title.ext" from the list)
    'rekordbox_xml': True,
    'library_dirs': [],              # folders already holding music; matches are copied, not downloaded
    'ffmpeg_path': '',
}
SPECTRUM_EXTENSIONS = {'.flac', '.wav', '.aif', '.aiff', '.mp3'}
LIBRARY_RESCAN = 6 * 3600
# Searches after a complete round without a usable source: 30 min, 2 h, 6 h, then every 12 h.
NOT_FOUND_BACKOFF = (1800, 7200, 21600, 43200)
SEARCHABLE = ('pending', 'not_found', 'review', 'retry_wait')
SCORING_REVISION = 'engine-v2'
DAY = 86400


class Engine:
    def __init__(self, store: Store, config: dict, api=None, validator=None):
        self.s = store
        self.config = dict(DEFAULTS, **config)
        self.config['prompt_for_web_credentials'] = False
        self.api = api or SlskdApi(self.config)
        if api is None:
            from slskd_runtime import SlskdRuntime
            self.runtime = SlskdRuntime(self.config)
        else:
            self.runtime = None
        self.validator = validator or validate_audio
        self.source_root = Path(os.path.expandvars(
            self.config.get('slskd_download_dir', r'%LOCALAPPDATA%\slskd\downloads'))).resolve()
        self.connected = False
        self.next_connect = self.next_poll = self.next_fill = self.next_collect = 0.0
        self.dirty_jobs: set[str] = set()
        self.reuse_failed: set[str] = set()
        self.ffmpeg = find_ffmpeg(str(self.config.get('ffmpeg_path') or ''))
        self.library_walk = None
        self.library_pass = 0.0
        self.next_library = self.next_quality = self.next_share_check = 0.0
        self.migrate()

    # ------------------------------------------------------------------ config
    def threshold(self) -> float:
        return float(self.config['auto_download_threshold'])

    # ------------------------------------------------------------------ main loop
    def tick(self):
        now = time.time()
        self.background(now)
        if not self.connected and not self.connect(now):
            return
        try:
            if now >= self.next_poll:
                self.poll_transfers()
                self.next_poll = time.time() + 4
            if now >= self.next_collect:
                self.collect_searches()
                self.next_collect = time.time() + 2
            if now >= self.next_fill:
                self.fill_slots()
                self.next_fill = time.time() + 3
            self.schedule_search()
            if now >= self.next_share_check:
                self.next_share_check = now + 600
                self.check_shares()
            self.finish_jobs()
            for job in list(self.dirty_jobs):
                self.export(job)
        except (ConnectionLost, PermissionError):
            self.connected = False
            self.next_connect = time.time() + 15
            self.s.set_setting('connection', 'slskd bağlantısı yenileniyor · işler korunuyor')
        except ApiError as error:
            self.s.set_setting('connection', f'slskd hata verdi (HTTP {error.status}) · tekrar denenecek')

    def connect(self, now) -> bool:
        if now < self.next_connect:
            return False
        if self.runtime is not None:
            ready, message = self.runtime.prepare()
            if not ready:
                self.next_connect = time.time() + 5
                self.s.set_setting('connection', message)
                return False
        try:
            self.s.set_setting('connection', 'slskd hazır · oturum açılıyor')
            self.api.authenticate()
            if not self.api.server_connected(self.api.server()):
                self.next_connect = time.time() + 10
                self.s.set_setting('connection', 'slskd hazır · Soulseek hesabının bağlanması bekleniyor')
                return False
        except PermissionError:
            self.next_connect = time.time() + 30
            self.s.set_setting('connection', 'slskd oturumu açılamadı · web giriş bilgilerini kontrol edin')
            return False
        except Exception:
            self.next_connect = time.time() + 20
            self.s.set_setting('connection', 'slskd bağlantı kontrolü başarısız · 20 sn sonra yeniden denenecek')
            return False
        self.connected = True
        self.s.set_setting('connection', 'slskd · Soulseek bağlı')
        self.check_shares()
        return True

    def check_shares(self):
        """Many peers refuse users who share nothing; warn once per connection."""
        application = getattr(self.api, 'application', None)
        if not application:
            return
        try:
            shares = get_ci(application(), 'shares', {}) or {}
        except Exception:
            return
        files = get_ci(shares, 'files')
        if isinstance(files, int) and not get_ci(shares, 'scanning', False):
            self.s.set_setting('share_warning', '1' if files == 0 else '0')

    # ------------------------------------------------------------------ helpers
    def active(self, track_id=None):
        marks = ','.join('?' * len(ACTIVE))
        if track_id:
            return self.s.rows(f'SELECT * FROM attempts WHERE status IN ({marks}) AND track=?', (*ACTIVE, track_id))
        return self.s.rows(f'SELECT * FROM attempts WHERE status IN ({marks})', ACTIVE)

    def track_of(self, row) -> Track:
        return Track(row['artist'], row['title'], row['position'], row.get('duration'), row.get('album') or '')

    def held(self, track_id) -> str | None:
        row = self.s.one('SELECT choice FROM track_choices WHERE track=?', (track_id,))
        return row['choice'] if row else None

    def approved(self, track_id):
        return self.s.one('SELECT candidate, created FROM approvals WHERE track=?', (track_id,))

    def attempt_window_start(self, job_id) -> float:
        """Peers tried after this moment count towards the daily limit ('Tekrar ara' resets it)."""
        return max(time.time() - DAY, float(self.s.setting('retry:' + job_id, '0')))

    def set_track(self, track, **values):
        self.s.update('tracks', track['id'], **values)
        self.dirty_jobs.add(track['job'])

    # ------------------------------------------------------------------ searching
    def schedule_search(self):
        now = time.time()
        running = self.s.one('SELECT COUNT(*) n FROM tracks WHERE search_id IS NOT NULL')['n']
        capacity = int(self.config['max_parallel_searches'])
        while running < capacity and now >= float(self.s.setting('next_search')):
            row = self.s.one(f"""
                SELECT t.* FROM tracks t JOIN jobs j ON j.id=t.job
                WHERE j.status='active'
                  AND (t.status IN ({','.join('?' * len(SEARCHABLE))}) OR (t.status='ready' AND t.variant>0))
                  AND t.next_search<=? AND t.search_id IS NULL AND (j.mode!='preview' OR t.rounds=0)
                  AND t.id NOT IN (SELECT track FROM approvals)
                  AND t.id NOT IN (SELECT track FROM track_choices)
                ORDER BY t.rounds, t.next_search, j.created, t.position LIMIT 1""", (*SEARCHABLE, now))
            if not row:
                return
            if self.library_match(row):
                continue
            track = self.track_of(row)
            plan = queries(track, int(self.config['max_queries']))
            query = plan[row['variant'] % len(plan)]
            ident = str(uuid.uuid4())
            # Persist intent before the network call; a lost reply is recovered by polling.
            self.set_track(row, search_id=ident, search_started=now, query=query,
                           search_deadline=now + float(self.config['search_max_wait_seconds']),
                           detail=f'Aranıyor ({row["variant"] % len(plan) + 1}/{len(plan)}): {query}')
            self.s.set_setting('next_search', now + float(self.config['search_interval_seconds']))
            self.s.event(row['job'], f'Aranıyor · {track.artist} — {track.title} · "{query}"')
            try:
                self.api.create_search(ident, query)
            except ApiError as error:
                self.set_track(row, search_id=None, search_deadline=0,
                               detail=f'Arama başlatılamadı (HTTP {error.status}); kısa süre sonra tekrar denenecek')
                self.s.set_setting('next_search', now + (60 if error.status == 429 else 15))
                return
            running += 1

    def collect_searches(self):
        now = time.time()
        for row in self.s.rows('SELECT * FROM tracks WHERE search_id IS NOT NULL'):
            if now - (row['search_started'] or 0) < 3:
                continue
            if now < (row['search_deadline'] or 0):
                try:
                    state = self.api.search_state(row['search_id'])
                except ApiError as error:
                    if error.status != 404:
                        continue
                    state = {'isComplete': True}
                if not get_ci(state, 'isComplete', False):
                    continue
            try:
                responses = self.api.search_responses(row['search_id'])
            except ApiError as error:
                if error.status != 404:
                    continue
                responses = []
            self.api.delete_search(row['search_id'])
            self.record_results(row, responses)

    def record_results(self, row, responses):
        """Score a finished search, keep the useful candidates and decide the track's next state."""
        track = self.track_of(row)
        spec = build_spec(track)
        scored = [score_candidate(spec, c, self.config) for c in extract_candidates(responses, row['search_id'])]
        scored.sort(key=lambda c: (c.eligible, c.score), reverse=True)
        keep = [c for c in scored if c.eligible][:150] + [c for c in scored if not c.eligible][:15]
        now = time.time()
        with self.s.db:
            for c in keep:
                self.s.db.execute('''INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(track,peer,filename) DO UPDATE SET data=excluded.data, score=excluded.score,
                    eligible=excluded.eligible, created=excluded.created''',
                    (str(uuid.uuid4()), row['id'], c.peer, c.filename, json.dumps(c.to_dict(), ensure_ascii=False),
                     c.score, int(c.eligible), now))

        fresh_peers = len(self.untried_peers(row))
        if row.get('search_kind') == 'manual':
            return self.record_manual_results(row, track, scored, fresh_peers)
        plan = queries(track, int(self.config['max_queries']))
        variant = row['variant'] + 1
        mode = self.s.one('SELECT mode FROM jobs WHERE id=?', (row['job'],))['mode']
        full_round = (fresh_peers >= int(self.config['enough_peers']) or variant >= len(plan)
                      or (mode == 'preview' and fresh_peers > 0))
        current = self.s.one('SELECT status FROM tracks WHERE id=?', (row['id'],))['status']
        reviewable = self.s.one('SELECT 1 FROM candidates WHERE track=? AND score>=?',
                                (row['id'], float(self.config['review_threshold'])))
        if current == 'done':
            status = 'done'
        elif self.held(row['id']):
            status = self.held(row['id'])
        elif self.active(row['id']):
            status = 'waiting'
        elif fresh_peers or self.approved(row['id']):
            status = 'ready'
        elif not full_round:
            status = 'pending'
        else:
            status = 'review' if reviewable else 'not_found'
        eligible = sum(c.eligible for c in scored)
        label = f'"{row["query"]}"' if row['query'] else 'Önceki sürümden kalan arama'
        detail = f'{label}: {len(scored)} ses dosyası · {eligible} güvenli eşleşme'
        if not full_round and status == 'pending':
            detail += ' · sıradaki sorgu denenecek'
        if spec.title_truncated or spec.version_prefix or spec.primary_truncated:
            detail += ' · TXT\'de ad kesilmiş; kesik kısım ön ek olarak eşleştirildi'
        delay = 0
        if full_round and status in ('review', 'not_found'):
            delay = NOT_FOUND_BACKOFF[min(row['rounds'], len(NOT_FOUND_BACKOFF) - 1)]
        elif full_round:
            delay = 1800        # refresh the peer list later if downloads keep failing
        extra = {}
        if row.get('upgrade') and full_round and status in ('review', 'not_found'):
            status, delay, extra = 'done', 0, {'upgrade': 0}
            detail = 'Daha iyi kaynak bulunamadı; mevcut dosya korundu · ' + detail
        self.set_track(row, search_id=None, search_deadline=0, status=status, detail=detail,
                       variant=0 if full_round else variant, rounds=row['rounds'] + int(full_round),
                       next_search=time.time() + delay, **extra)
        self.s.event(row['job'], f'{track.artist} — {track.title}: {detail}')

    def record_manual_results(self, row, track, scored, fresh_peers):
        """A search the user typed: report it without touching the automatic query rotation."""
        current = self.s.one('SELECT status FROM tracks WHERE id=?', (row['id'],))['status']
        if current in ('done', 'skipped', 'deferred') or self.active(row['id']):
            status = 'waiting' if current not in ('done', 'skipped', 'deferred') else current
        elif fresh_peers:
            status = 'ready'
        elif any(c.score >= float(self.config['review_threshold']) for c in scored):
            status = 'review'
        else:
            status = current
        eligible = sum(c.eligible for c in scored)
        detail = f'Elle arama "{row["query"]}": {len(scored)} ses dosyası · {eligible} güvenli eşleşme'
        self.set_track(row, search_id=None, search_deadline=0, search_kind='', status=status, detail=detail)
        self.s.event(row['job'], f'{track.artist} — {track.title}: {detail}')

    # ------------------------------------------------------------------ peers
    def untried_peers(self, track_row) -> list[Candidate]:
        """Fresh safe candidates (or the user's pick) from peers not tried in the current window."""
        approval = self.approved(track_row['id'])
        since = self.attempt_window_start(track_row['job'])
        if approval:
            rows = self.s.rows('SELECT * FROM candidates WHERE id=?', (approval['candidate'],))
            since = approval['created']      # the user's pick may be a peer that failed before
        else:
            rows = self.s.rows('SELECT * FROM candidates WHERE track=? AND eligible=1 AND score>=? AND created>?',
                               (track_row['id'], self.threshold(), time.time() - DAY))
        tried = {r['peer'].casefold() for r in self.s.rows(
            'SELECT peer FROM attempts WHERE track=? AND created>?', (track_row['id'], since))}
        candidates = [Candidate.from_dict(json.loads(r['data'])) for r in rows]
        if track_row.get('upgrade'):
            used = {(r['peer'].casefold(), r['filename']) for r in self.s.rows(
                "SELECT peer, filename FROM attempts WHERE track=? AND status='succeeded'", (track_row['id'],))}
            candidates = [c for c in candidates if (c.peer.casefold(), c.filename) not in used]
        return [c for c in candidates if c.peer.casefold() not in tried]

    def fill_slots(self):
        active_total = len(self.active())
        global_cap = max(2, int(self.config['gui_max_pending_transfers']))
        per_day = int(self.config['peer_attempts_per_day'])
        now = time.time()
        for t in self.s.rows("""SELECT t.*, j.mode FROM tracks t JOIN jobs j ON j.id=t.job
                WHERE j.status='active' AND j.mode!='preview' AND t.status NOT IN ('done','skipped','deferred')
                ORDER BY j.created, t.position"""):
            if self.reuse_finished_copy(t):
                continue
            # The same song in another playlist is already downloading: wait and copy it.
            if self.s.one(f"""SELECT a.id FROM attempts a JOIN tracks x ON x.id=a.track
                    WHERE x.key=? AND x.id!=? AND a.status IN ({','.join('?' * len(ACTIVE))})""",
                          (t['key'], t['id'], *ACTIVE)):
                continue
            active = self.active(t['id'])
            cap = 1 if t['mode'] == 'patient' else 2
            patience = float(self.config['queued_patience_minutes']) * 60
            if (t['mode'] == 'fast' and active and len(active) == cap
                    and all(a['status'] == 'queued' and now - a['created'] > patience for a in active)):
                cap += 1        # every peer is just queueing; ask one more, first finished wins
            if len(active) >= cap or active_total >= global_cap:
                continue
            approval = self.approved(t['id'])
            tried = self.s.one('SELECT COUNT(DISTINCT lower(peer)) n FROM attempts WHERE track=? AND created>?',
                               (t['id'], self.attempt_window_start(t['job'])))['n']
            available = self.untried_peers(t)
            if not available or (tried >= per_day and not approval):
                if not active and tried and t['status'] != 'retry_wait' and not t['search_id']:
                    self.set_track(t, status='retry_wait', next_search=min(t['next_search'] or now, now + 1800),
                                   detail=f'{tried} kaynak denendi; yeni kaynaklar için yeniden aranacak')
                continue
            failures = {r['peer'].casefold(): r['n'] for r in self.s.rows(
                "SELECT peer, COUNT(*) n FROM attempts WHERE track=? AND status='failed' GROUP BY peer", (t['id'],))}
            outliers = duration_outliers(available)
            reputation = self.peer_reputation()
            available.sort(key=lambda c: rank_key(c, self.config, failures.get(c.peer.casefold(), 0),
                                                  (c.peer, c.filename) in outliers,
                                                  reputation.get(c.peer.casefold(), 0)), reverse=True)
            for c in available:
                if len(active) >= cap or active_total >= global_cap or (tried >= per_day and not approval):
                    break
                if self.s.one(f"SELECT id FROM attempts WHERE peer=? AND filename=? AND status IN "
                              f"({','.join('?' * len(ACTIVE))})", (c.peer, c.filename, *ACTIVE)):
                    continue
                self.start_attempt(t, c, manual=bool(approval))
                active = self.active(t['id'])
                active_total = len(self.active())
                tried += 1

    def start_attempt(self, t, c: Candidate, manual=False):
        ident = str(uuid.uuid4())
        self.s.execute('''INSERT INTO attempts(id,track,peer,filename,size,data,status,stage,created,manual,progress_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (ident, t['id'], c.peer, c.filename, c.size, json.dumps(c.to_dict(), ensure_ascii=False), 'submitting',
             '_SoulseekAuto/' + ident, time.time(), int(manual), time.time()))
        self.s.event(t['job'], f'Peer isteği · {t["title"]} · {c.peer} · {c.extension.upper()} · %{c.score:.0f}')
        self.submit(self.s.one('SELECT * FROM attempts WHERE id=?', (ident,)))
        active = self.active(t['id'])
        self.set_track(t, status='waiting' if active else t['status'],
                       detail='Peer yanıtı / indirme bekleniyor' if active else t['detail'])

    def submit(self, a):
        """Send (or re-send with the same batch id) a download request; outcomes are recorded on the attempt."""
        try:
            result = self.api.enqueue(a['id'], a['peer'], a['filename'], a['size'], a['stage'])
        except ApiError as error:
            if error.status == 409:          # already created by an earlier, unanswered request
                return self.recover_submission(a)
            if error.status == 429:          # slskd handles one enqueue at a time; try again on next poll
                return
            reason = 'Peer çevrimdışı' if error.status == 404 else f'slskd isteği reddetti (HTTP {error.status})'
            self.s.update('attempts', a['id'], status='failed', detail=reason)
            return
        except ConnectionLost:
            return                            # uncertain; recover_submission checks the batch later
        self.apply_batch(a, result)

    def apply_batch(self, a, result):
        batch = get_ci(result, 'batch', result) or {}
        transfers = get_ci(batch, 'transfers', []) or []
        if transfers and get_ci(transfers[0], 'id'):
            self.s.update('attempts', a['id'], transfer_id=str(get_ci(transfers[0], 'id')), status='queued')
            return
        failures = get_ci(result, 'failures', []) or []
        if failures:
            message = str(get_ci(failures[0], 'message', '') or '')[:120]
            self.s.update('attempts', a['id'], status='failed', detail='Peer isteği reddetti' + (f': {message}' if message else ''))
        elif time.time() - a['created'] > 120:
            self.s.update('attempts', a['id'], status='failed', detail='Peer isteği kabul etmedi (boş yanıt)')

    def recover_submission(self, a):
        try:
            result = self.api.batch(a['id'])
        except ApiError as error:
            if error.status == 404:
                return self.submit(a)        # verified absent: repeat with the SAME batch id
            raise
        self.apply_batch(a, result)

    # ------------------------------------------------------------------ transfers
    def poll_transfers(self):
        attempts = self.active()
        if not attempts:
            return
        snapshot = self.api.downloads() if any(a['status'] != 'submitting' for a in attempts) else {}
        now = time.time()
        for a in attempts:
            t = self.s.one('SELECT * FROM tracks WHERE id=?', (a['track'],))
            job = self.s.one('SELECT * FROM jobs WHERE id=?', (t['job'],))
            unwanted = t['status'] == 'done' or job['status'] == 'cancelled'
            if a['status'] == 'submitting':
                if unwanted:
                    try:
                        self.apply_batch(a, self.api.batch(a['id']))
                        if self.s.one('SELECT status FROM attempts WHERE id=?', (a['id'],))['status'] == 'submitting':
                            self.s.update('attempts', a['id'], status='cancelled')
                    except ApiError as error:
                        if error.status != 404:
                            raise
                        self.s.update('attempts', a['id'], status='cancelled')
                else:
                    self.recover_submission(a)
                continue
            transfer = snapshot.get(str(a['transfer_id']))
            if transfer is None:
                try:
                    transfer = self.api.get_download(a['peer'], a['transfer_id'])
                except ApiError as error:
                    if error.status != 404:
                        raise
                    self.s.update('attempts', a['id'], status='failed', detail='Transfer slskd kayıtlarında yok')
                    self.dirty_jobs.add(t['job'])
                    continue
            state = str(get_ci(transfer, 'state', ''))
            received = int(get_ci(transfer, 'bytesTransferred', 0) or 0)
            if not a['size']:
                a['size'] = int(get_ci(transfer, 'size', 0) or 0)
            progress_at = now if received != a['bytes'] or not a['progress_at'] else a['progress_at']
            self.s.update('attempts', a['id'], size=a['size'], bytes=received, progress_at=progress_at,
                          speed=float(get_ci(transfer, 'averageSpeed', 0) or 0), detail=state)
            a.update(bytes=received, progress_at=progress_at, detail=state)
            if 'Completed' in state:
                if 'Succeeded' in state:
                    self.finalize(a, t, job)
                else:
                    self.s.update('attempts', a['id'], status='cancelled' if a['cancel_requested'] else 'failed')
                    if not a['cancel_requested']:
                        self.peer_result(a['peer'], ok=False)
                    self.forget_transfer(a)
                    if t['status'] != 'done':
                        self.set_track(t, status='waiting' if self.active(t['id']) else 'ready',
                                       detail='Peer tamamlayamadı; diğer kaynaklar deneniyor')
                    self.s.event(t['job'], f'Peer sonlandı · {t["title"]} · {a["peer"]} · {state}')
            elif unwanted:
                if not a['cancel_requested'] or now - a['created'] > 60:
                    self.api.cancel(a['peer'], a['transfer_id'])
                    self.s.update('attempts', a['id'], cancel_requested=1)
            elif 'InProgress' in state and received > 0:
                stall = float(self.config['stall_minutes']) * 60
                if now - progress_at > stall:
                    self.api.cancel(a['peer'], a['transfer_id'])
                    self.s.update('attempts', a['id'], status='failed', cancel_requested=1,
                                  detail=f'İndirme {int(stall // 60)} dakikadır ilerlemiyor')
                    self.set_track(t, status='ready', detail='İndirme durdu; başka kaynak deneniyor')
                    continue
                self.s.update('attempts', a['id'], status='downloading')
                self.set_track(t, status='downloading', detail=state)
            else:
                self.s.update('attempts', a['id'], status='queued')
                if t['status'] not in ('downloading',):
                    self.set_track(t, status='waiting', detail=state)

    def finalize(self, a, t, job):
        path = self.locate(a)
        if not path:
            if a['status'] == 'finishing' and time.time() - (a['progress_at'] or a['created']) > 600:
                self.s.update('attempts', a['id'], status='failed', detail='Tamamlandı, fakat dosya bulunamadı')
            else:
                self.s.update('attempts', a['id'], status='finishing', detail='Tamamlandı; dosya aranıyor')
            return
        data = json.loads(a['data'] or '{}')
        minimum = None if a['manual'] or data.get('extension') != 'mp3' else int(self.config['minimum_mp3_bitrate'])
        try:
            self.validator(path, a['size'], minimum)
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else 'Dosya doğrulanamadı'
            self.s.update('attempts', a['id'], status='failed', detail='Doğrulama başarısız: ' + message)
            self.s.event(t['job'], f'Doğrulama başarısız · {t["title"]} · {message} · dosya silinmedi')
            if t['status'] != 'done':
                self.set_track(t, status='ready', detail='İnen dosya doğrulanamadı; başka kaynak deneniyor')
            return
        self.peer_result(a['peer'], ok=True)
        if t['status'] == 'done' or job['status'] == 'cancelled':
            self.quarantine(a, path)
            self.s.update('attempts', a['id'], status='succeeded')
            self.forget_transfer(a)
            return
        note = ''
        problem = self.content_problem(t, path, manual=bool(a['manual']))
        if problem:
            reason, note = problem
            if reason:
                self.s.update('attempts', a['id'], status='failed', detail=reason)
                self.quarantine(a, path)
                self.forget_transfer(a)
                self.set_track(t, status='ready', detail=reason + ' · başka kaynak deneniyor')
                self.s.event(t['job'], f'{t["title"]} · {reason} · dosya Quarantine klasöründe')
                return
        self.deliver(t, path, digest(path), move=True, note=note)
        self.s.update('attempts', a['id'], status='succeeded')
        self.forget_transfer(a)

    def content_problem(self, t, path, manual=False):
        """(reason to reject or '', note to show) for a downloaded file, or None when it is fine.

        Suspicious spectra are rejected twice per track; after that the best available
        file is kept and flagged instead of searching forever.
        """
        ok, short = duration_ok(t.get('duration'), audio_length(path))
        if not ok and not manual:
            return f'İnen dosya beklenenden kısa: {short}', ''
        khz = self.spectrum(path)
        if khz is None:
            return None
        note = 'kesme yok' if khz >= NO_CUTOFF else f'{khz:g} kHz'
        threshold = float(self.config['fake_lossless_khz'])
        if t.get('upgrade') and not manual:
            old = self.cutoff_of(t)
            if khz >= threshold or (old is not None and khz >= old + 0.5):
                return '', note + ('' if khz >= threshold else ' · şüpheli')
            return f'Daha iyi değil: yeni dosya da {khz:g} kHz\'de kesiliyor', note
        if khz >= threshold:
            return '', note
        note += ' · şüpheli'
        earlier = self.s.one("SELECT COUNT(*) n FROM attempts WHERE track=? AND detail LIKE 'Şüpheli kalite%'",
                             (t['id'],))['n']
        if manual or earlier >= 2:
            return '', note + ' (daha iyisi bulunamadı)'
        return (f'Şüpheli kalite: ses {khz:g} kHz\'de kesiliyor (düşük bitrate MP3\'ten dönüştürülmüş olabilir)',
                note)

    @staticmethod
    def cutoff_of(t):
        try:
            return float(str(t.get('quality_note') or '').split(' ')[0])
        except ValueError:
            return None

    def spectrum(self, path):
        if not (self.ffmpeg and self.config.get('check_fake_lossless') and Path(path).suffix.lower() in SPECTRUM_EXTENSIONS):
            return None
        try:
            return spectral_cutoff(path, self.ffmpeg)
        except Exception:
            return None

    def peer_result(self, peer, ok):
        column = 'succeeded' if ok else 'failed'
        self.s.execute(f'''INSERT INTO peer_stats(peer, {column}, updated) VALUES(?, 1, ?)
            ON CONFLICT(peer) DO UPDATE SET {column}={column}+1, updated=excluded.updated''',
                       (peer.casefold(), time.time()))

    def peer_reputation(self) -> dict:
        return {r['peer']: r['succeeded'] - r['failed'] for r in self.s.rows('SELECT * FROM peer_stats')}

    def forget_transfer(self, a):
        """Remove our finished transfer from slskd's list so it does not pile up."""
        remove = getattr(self.api, 'remove_download', None)
        if remove and a.get('transfer_id'):
            try:
                remove(a['peer'], a['transfer_id'])
            except Exception:
                pass

    def locate(self, a):
        if not a['stage']:
            name = PureWindowsPath(a['filename']).name
            found = [p for p in self.source_root.rglob('*') if p.name == name and p.is_file()
                     and (not a['size'] or p.stat().st_size == a['size'])]
            return found[0] if len(found) == 1 else None
        folder = (self.source_root / a['stage']).resolve()
        if self.source_root not in folder.parents:
            raise ValueError('Geçersiz geçici klasör')
        if not folder.is_dir():
            return None
        found = [p for p in folder.iterdir() if p.is_file() and (not a['size'] or p.stat().st_size == a['size'])]
        return found[0] if len(found) == 1 else None

    def quarantine(self, a, path):
        if not a['stage']:
            return
        folder = self.s.root / 'Quarantine' / a['id']
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / path.name).exists():
            shutil.move(str(path), str(folder / path.name))

    def deliver(self, t, source: Path, sha: str, move: bool, note: str = ''):
        job = self.s.one('SELECT * FROM jobs WHERE id=?', (t['job'],))
        folder = Path(job['folder'])
        folder.mkdir(parents=True, exist_ok=True)
        convert = (self.config.get('output_format') == 'aiff' and self.ffmpeg
                   and source.suffix.lower() in ('.flac', '.wav'))
        suffix = '.aiff' if convert else source.suffix
        temporary = folder / ('.' + t['id'] + suffix)
        if convert:
            convert_to_aiff(source, temporary, self.ffmpeg)
        else:
            shutil.copyfile(source, temporary)
            if digest(temporary) != sha:
                temporary.unlink()
                raise ValueError('Dosya kopyası doğrulanamadı')
        tagged = write_track_number(temporary, t['position'])
        self.validator(temporary, 0, None)
        final_sha = digest(temporary)
        stem = safe_name(f'{t["artist"]} - {t["title"]}' if self.config.get('filename_template') == 'artist_title'
                         else source.stem)
        destination, n = folder / (stem + suffix), 2
        while destination.exists() and digest(destination) != final_sha:
            destination = folder / (stem + f' ({n})' + suffix)
            n += 1
        if destination.exists():
            temporary.unlink()
        else:
            temporary.replace(destination)
        detail = f'Tamamlandı · sıra etiketi: {t["position"]}' if tagged else 'Tamamlandı · sıra etiketi yazılamadı'
        if note:
            detail += f' · spektrum {note}'
        replaced = t.get('path') if t.get('upgrade') else ''
        if replaced and Path(replaced).is_file() and Path(replaced).resolve() != destination.resolve():
            keep = self.s.root / 'Quarantine' / ('replaced-' + t['id'])
            keep.mkdir(parents=True, exist_ok=True)
            shutil.move(replaced, str(keep / Path(replaced).name))
            detail += ' · eski dosya Quarantine klasörüne taşındı'
        self.s.update('tracks', t['id'], status='done', path=str(destination), hash=final_sha, detail=detail,
                      quality_note=note, upgrade=0)
        self.export(job['id'])
        if move and source.resolve() != destination.resolve() and self.source_root in source.resolve().parents:
            source.unlink()
            try:
                source.parent.rmdir()
            except OSError:
                pass
        self.s.event(job['id'], f'Tamamlandı · {t["artist"]} — {t["title"]}')

    def reuse_finished_copy(self, t) -> bool:
        """Copy a verified file already downloaded for another playlist instead of downloading again."""
        if t['id'] in self.reuse_failed or t.get('upgrade'):
            return False
        other = self.s.one("SELECT path,hash FROM tracks WHERE key=? AND status='done' AND path!='' AND id!=?",
                           (t['key'], t['id']))
        if not other or not Path(other['path']).is_file():
            return False
        try:
            if digest(other['path']) != other['hash']:
                raise ValueError
            self.deliver(t, Path(other['path']), other['hash'], move=False)
            return True
        except (OSError, ValueError):
            self.reuse_failed.add(t['id'])
            return False

    # ------------------------------------------------------------------ jobs & files
    def finish_jobs(self):
        for job in self.s.rows("SELECT * FROM jobs WHERE status='active'"):
            if job['mode'] == 'preview':
                if not self.s.one("SELECT id FROM tracks WHERE job=? AND status!='skipped' "
                                  "AND (rounds=0 OR search_id IS NOT NULL)", (job['id'],)):
                    self.s.update('jobs', job['id'], status='preview_complete')
                    self.s.event(job['id'], 'İndirmesiz arama testi tamamlandı.')
            elif not self.s.one("SELECT id FROM tracks WHERE job=? AND status NOT IN ('done','skipped')", (job['id'],)):
                self.s.update('jobs', job['id'], status='completed')
                self.s.event(job['id'], 'Playlist tamamlandı.')

    def export(self, job_id):
        self.dirty_jobs.discard(job_id)
        job = self.s.one('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not job:
            return
        folder = Path(job['folder'])
        folder.mkdir(parents=True, exist_ok=True)
        tracks = self.s.rows('SELECT * FROM tracks WHERE job=? ORDER BY position', (job_id,))
        present = [t for t in tracks if (t['status'] == 'done' or t.get('upgrade')) and t['path']
                   and Path(t['path']).is_file()]
        lines = ['#EXTM3U'] + [Path(t['path']).name for t in present]
        temporary = folder / '.playlist.tmp'
        temporary.write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
        temporary.replace(folder / (safe_name(job['name']) + '.m3u8'))
        if self.config.get('rekordbox_xml'):
            self.export_rekordbox(job, present)
        with (folder / 'rapor.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['sıra', 'sanatçı', 'şarkı', 'durum', 'dosya', 'açıklama'])
            for t in tracks:
                writer.writerow([t['position'], t['artist'], t['title'], t['status'], t['path'], t['detail']])

    def export_rekordbox(self, job, tracks):
        """rekordbox XML (File > Import > rekordbox xml) with the playlist in TXT order."""
        import xml.etree.ElementTree as ET
        from urllib.parse import quote
        kinds = {'.flac': 'FLAC File', '.mp3': 'MP3 File', '.aiff': 'AIFF File', '.aif': 'AIFF File',
                 '.wav': 'WAV File', '.m4a': 'M4A File'}
        root = ET.Element('DJ_PLAYLISTS', Version='1.0.0')
        ET.SubElement(root, 'PRODUCT', Name='Soulseek Playlists', Version='2', Company='')
        collection = ET.SubElement(root, 'COLLECTION', Entries=str(len(tracks)))
        for index, t in enumerate(tracks, 1):
            path = Path(t['path']).resolve()
            ET.SubElement(collection, 'TRACK', TrackID=str(index), Name=t['title'], Artist=t['artist'],
                          Album=t.get('album') or '', Kind=kinds.get(path.suffix.lower(), 'Audio File'),
                          TrackNumber=str(t['position']),
                          Location='file://localhost/' + quote(path.as_posix(), safe='/:'))
        playlists = ET.SubElement(root, 'PLAYLISTS')
        node = ET.SubElement(playlists, 'NODE', Type='0', Name='ROOT', Count='1')
        playlist = ET.SubElement(node, 'NODE', Name=job['name'], Type='1', KeyType='0', Entries=str(len(tracks)))
        for index in range(1, len(tracks) + 1):
            ET.SubElement(playlist, 'TRACK', Key=str(index))
        ET.indent(root)
        temporary = Path(job['folder']) / '.rekordbox.tmp'
        ET.ElementTree(root).write(temporary, encoding='utf-8', xml_declaration=True)
        temporary.replace(Path(job['folder']) / (safe_name(job['name']) + '.rekordbox.xml'))

    # ------------------------------------------------------------------ background work (no slskd needed)
    def background(self, now):
        if now >= self.next_quality:
            self.next_quality = now + 4
            self.scan_quality_step()
        if self.config.get('library_dirs') and (self.library_walk is not None or now >= self.next_library):
            self.scan_library_step()

    def scan_quality_step(self):
        """Measure the spectrum of one finished file that has not been measured yet."""
        if not (self.ffmpeg and self.config.get('check_fake_lossless')):
            return
        for t in self.s.rows("SELECT * FROM tracks WHERE status='done' AND path!='' AND "
                             "(quality_note IS NULL OR quality_note='') ORDER BY position LIMIT 20"):
            path = Path(t['path'])
            if path.suffix.lower() not in SPECTRUM_EXTENSIONS or not path.is_file():
                self.s.update('tracks', t['id'], quality_note='—')
                continue
            khz = self.spectrum(path)
            if khz is None:
                note = '—'
            elif khz >= NO_CUTOFF:
                note = 'kesme yok'
            else:
                note = f'{khz:g} kHz' + (' · şüpheli' if khz < float(self.config['fake_lossless_khz']) else '')
            self.s.update('tracks', t['id'], quality_note=note)
            return

    def library_roots(self):
        value = self.config.get('library_dirs') or []
        if isinstance(value, str):
            value = [v for v in value.split(';')]
        return [Path(os.path.expandvars(v.strip())) for v in value if v and v.strip()]

    def _walk_library(self):
        skip = {p.resolve() for p in (self.s.output_root, self.source_root, self.s.root) if p}
        for root in self.library_roots():
            for folder, dirs, files in os.walk(root):
                current = Path(folder).resolve()
                if current in skip:
                    dirs[:] = []
                    continue
                for name in files:
                    if Path(name).suffix.lower().lstrip('.') in AUDIO_EXTENSIONS:
                        yield current / name

    def scan_library_step(self, budget=0.08):
        """Index the user's music folders a little at a time so the worker never stalls."""
        if self.library_walk is None:
            self.library_walk = self._walk_library()
            self.library_pass = time.time()
        deadline = time.monotonic() + budget
        batch = []
        for path in self.library_walk:
            try:
                stat = path.stat()
            except OSError:
                continue
            batch.append((str(path), norm(path.parent.name + ' ' + path.stem), stat.st_size, stat.st_mtime,
                          self.library_pass))
            if time.monotonic() > deadline:
                break
        else:
            self.library_walk = None
            self.next_library = time.time() + LIBRARY_RESCAN
        with self.s.db:
            self.s.db.executemany('INSERT OR REPLACE INTO library VALUES(?,?,?,?,?)', batch)
            if self.library_walk is None:
                self.s.db.execute('DELETE FROM library WHERE seen<?', (self.library_pass,))

    def library_match(self, row) -> bool:
        """Copy a matching file the user already owns instead of searching Soulseek."""
        spec = build_spec(self.track_of(row))
        if not spec.core or not self.config.get('library_dirs'):
            return False
        track = self.track_of(row)
        for item in self.s.rows('SELECT * FROM library WHERE name LIKE ? LIMIT 60', ('%' + spec.core + '%',)):
            path = Path(item['path'])
            c = Candidate('Kütüphane', str(path), item['size'], None, path.suffix.lower().lstrip('.'), 0, 0, True)
            if not score_candidate(spec, c, self.config).eligible or not path.is_file():
                continue
            try:
                minimum = int(self.config['minimum_mp3_bitrate']) if c.extension == 'mp3' else None
                self.validator(path, 0, minimum)
                if not duration_ok(track.duration, audio_length(path))[0]:
                    continue
                self.deliver(row, path, digest(path), move=False, note='')
            except (OSError, ValueError):
                continue
            self.s.event(row['job'], f'Kütüphanede bulundu · {track.artist} — {track.title} · {path.name}')
            return True
        return False

    # ------------------------------------------------------------------ user commands
    def command(self, action, payload):
        if action == 'import':
            return self.s.add_job(**payload)
        if action in ('skip_track', 'defer_track', 'restore_track'):
            return self._track_choice(action, payload)
        if action == 'approve':
            return self._approve(payload)
        if action == 'manual_search':
            return self._manual_search(payload['track'], payload['query'])
        if action == 'edit_track':
            return self._edit_track(payload['track'], payload['artist'], payload['title'])
        if action == 'rename_job':
            return self._rename_job(payload['job'], payload['name'])
        if action == 'relayout':
            return self._relayout(payload['job'], payload['layout'])
        if action == 'remove_job':
            return self._remove_job(payload)
        if action == 'upgrade_track':
            return self._upgrade([payload])
        if action == 'upgrade_suspicious':
            rows = self.s.rows("SELECT id FROM tracks WHERE status='done' AND quality_note LIKE '%şüpheli%'"
                               + (' AND job=?' if payload else ''), (payload,) if payload else ())
            return self._upgrade([r['id'] for r in rows])
        job = self.s.one('SELECT * FROM jobs WHERE id=?', (payload,))
        if not job:
            return
        if action in ('start', 'pause', 'cancel'):
            self.s.update('jobs', payload, status={'start': 'active', 'pause': 'paused', 'cancel': 'cancelled'}[action])
            self.s.event(payload, {'start': 'İş sürdürülüyor.',
                                   'pause': 'Yeni aramalar duraklatıldı. Mevcut transferler izleniyor.',
                                   'cancel': 'İş iptal edildi; aktif istekler iptal edilecek. Dosyalar korunur.'}[action])
        elif action == 'retry':
            # Search again now with every query and allow previously tried peers again.
            self.s.set_setting('retry:' + payload, time.time())
            self.s.execute(f"""UPDATE tracks SET next_search=0, variant=0, rounds=0,
                status=CASE WHEN status IN ('not_found','review','retry_wait') THEN 'pending' ELSE status END
                WHERE job=? AND status NOT IN ('done','skipped','deferred') AND search_id IS NULL""", (payload,))
            self.s.update('jobs', payload, status='active')
            self.s.event(payload, 'Tamamlanmayan parçalar yeniden aranacak; daha önce denenen peer\'lar tekrar denenebilir.')

    def _manual_search(self, track_id, query):
        query = ' '.join(str(query or '').split())
        t = self.s.one('SELECT * FROM tracks WHERE id=?', (track_id,))
        if not t or not query:
            raise ValueError('Aranacak metni yazın.')
        if t['search_id']:
            raise ValueError('Bu parça için bir arama zaten sürüyor; birkaç saniye bekleyin.')
        ident, now = str(uuid.uuid4()), time.time()
        self.set_track(t, search_id=ident, search_started=now, search_kind='manual', query=query,
                       search_deadline=now + float(self.config['search_max_wait_seconds']),
                       detail=f'Elle aranıyor: {query}')
        self.s.event(t['job'], f'Elle arama · {t["artist"]} — {t["title"]} · "{query}"')
        try:
            self.api.create_search(ident, query)
        except (ApiError, ConnectionLost):
            self.set_track(t, search_id=None, search_kind='', detail='Elle arama başlatılamadı; slskd bağlantısını kontrol edin')
            raise ValueError('Arama başlatılamadı; slskd bağlantısını kontrol edin.') from None

    def _edit_track(self, track_id, artist, title):
        artist, title = ' '.join(str(artist).split()), ' '.join(str(title).split())
        t = self.s.one('SELECT * FROM tracks WHERE id=?', (track_id,))
        if not t or not artist or not title:
            raise ValueError('Sanatçı ve şarkı adı boş olamaz.')
        if t['status'] == 'done' or self.active(track_id):
            raise ValueError('Tamamlanan veya inen parçanın adı değiştirilemez.')
        key = Track(artist, title).key
        if self.s.one('SELECT id FROM tracks WHERE job=? AND key=? AND id!=?', (t['job'], key, track_id)):
            raise ValueError('Bu playlistte aynı parça zaten var.')
        with self.s.db:
            for table in ('approvals', 'track_choices', 'candidates'):
                self.s.db.execute(f'DELETE FROM {table} WHERE track=?', (track_id,))
            self.s.db.execute("""UPDATE tracks SET artist=?, title=?, key=?, status='pending', rounds=0, variant=0,
                next_search=0, search_id=NULL, search_kind='', detail='Ad düzeltildi; yeniden aranacak' WHERE id=?""",
                              (artist, title, key, track_id))
        self.dirty_jobs.add(t['job'])
        self.s.event(t['job'], f'Ad düzeltildi · {t["artist"]} — {t["title"]} → {artist} — {title}')

    def _rename_job(self, job_id, name):
        name = ' '.join(str(name).split())
        job = self.s.one('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not job or not name:
            raise ValueError('Playlist adı boş olamaz.')
        for suffix in ('.m3u8', '.rekordbox.xml'):
            old = Path(job['folder']) / (safe_name(job['name']) + suffix)
            if old.is_file():
                old.unlink()
        self.s.update('jobs', job_id, name=name)
        self.export(job_id)
        self.s.event(job_id, f'Playlist yeniden adlandırıldı: {name}')

    def _relayout(self, job_id, layout):
        if self.s.one(f"SELECT a.id FROM attempts a JOIN tracks t ON t.id=a.track WHERE t.job=? AND a.status IN "
                      f"({','.join('?' * len(ACTIVE))})", (job_id, *ACTIVE)):
            raise ValueError('Aktif indirmesi olan playlistin sütun sırası değiştirilemez; önce duraklatıp bekleyin.')
        if not self.s.relayout_job(job_id, layout):
            raise ValueError('Liste yeniden okunamadı.')
        self.dirty_jobs.add(job_id)
        self.s.event(job_id, 'Sütun sırası değiştirildi; tamamlanmayan parçalar yeniden aranacak.')

    def _upgrade(self, track_ids):
        """Search a better source for finished tracks; the current file stays until one is verified."""
        count = 0
        for track_id in track_ids:
            t = self.s.one('SELECT * FROM tracks WHERE id=?', (track_id,))
            if not t or t['status'] != 'done' or not t['path'] or self.active(track_id):
                continue
            with self.s.db:
                self.s.db.execute('DELETE FROM approvals WHERE track=?', (track_id,))
                self.s.db.execute('DELETE FROM track_choices WHERE track=?', (track_id,))
                self.s.db.execute("""UPDATE tracks SET upgrade=1, status='pending', rounds=0, variant=0, next_search=0,
                    detail='Daha iyi kaynak aranıyor; mevcut dosya yerinde kalıyor' WHERE id=?""", (track_id,))
                self.s.db.execute("UPDATE jobs SET status='active' WHERE id=? AND status='completed'", (t['job'],))
            self.dirty_jobs.add(t['job'])
            self.s.event(t['job'], f'Daha iyisi aranıyor · {t["artist"]} — {t["title"]}')
            count += 1
        if not count:
            raise ValueError('Daha iyi kaynak aranacak tamamlanmış parça yok (aktif indirmesi olanlar atlanır).')
        return count

    def _remove_job(self, job_id):
        for a in self.s.rows(f"SELECT a.* FROM attempts a JOIN tracks t ON t.id=a.track WHERE t.job=? AND "
                             f"a.status IN ({','.join('?' * len(ACTIVE))})", (job_id, *ACTIVE)):
            if a['transfer_id']:
                try:
                    self.api.cancel(a['peer'], a['transfer_id'])
                except Exception:
                    pass
        self.dirty_jobs.discard(job_id)
        self.s.remove_job(job_id)

    def _track_choice(self, action, track_id):
        t = self.s.one('SELECT * FROM tracks WHERE id=?', (track_id,))
        if not t or t['status'] == 'done':
            raise ValueError('Tamamlanan parça bu işlemle değiştirilemez.')
        if self.active(track_id):
            raise ValueError('Bu parçanın aktif transferi var; transfer bittikten sonra seçebilirsiniz.')
        with self.s.db:
            self.s.db.execute('DELETE FROM approvals WHERE track=?', (track_id,))
            if action == 'restore_track':
                self.s.db.execute('DELETE FROM track_choices WHERE track=?', (track_id,))
                self.s.db.execute("UPDATE tracks SET status='pending', next_search=0, variant=0, "
                                  "detail='Tekrar işleme alındı' WHERE id=?", (track_id,))
                self.s.db.execute("UPDATE jobs SET status='active' WHERE id=? AND status='completed'", (t['job'],))
            else:
                choice = 'skipped' if action == 'skip_track' else 'deferred'
                self.s.db.execute('INSERT OR REPLACE INTO track_choices VALUES(?,?,?,?)',
                                  (track_id, choice, t['status'], time.time()))
                self.s.db.execute('UPDATE tracks SET status=?, detail=? WHERE id=?',
                                  (choice, 'Kullanıcı kararı; adaylar korundu', track_id))
        self.dirty_jobs.add(t['job'])
        self.s.event(t['job'], f"{t['title']} · " + {'skip_track': 'Atlandı (Pass)', 'defer_track': 'Seçim sonraya bırakıldı',
                                                    'restore_track': 'Tekrar işleme alındı'}[action])

    def _approve(self, payload):
        row = self.s.one('SELECT * FROM candidates WHERE id=? AND track=?', (payload['candidate'], payload['track']))
        t = self.s.one('SELECT * FROM tracks WHERE id=?', (payload['track'],))
        if not row or not t or t['status'] == 'done':
            raise ValueError('Aday artık seçilemiyor; listeyi yenileyin.')
        if self.active(t['id']):
            raise ValueError('Bu parçanın aktif transferi var; seçim için tamamlanmasını bekleyin.')
        job = self.s.one('SELECT * FROM jobs WHERE id=?', (t['job'],))
        if job['mode'] == 'preview' or job['status'] == 'cancelled':
            raise ValueError('Arama testi / iptal edilen iş için indirme seçilemez.')
        if not quality_allowed(Candidate.from_dict(json.loads(row['data'])), manual=True):
            raise ValueError('Seçilen dosya bir ses dosyası değil.')
        with self.s.db:
            self.s.db.execute('INSERT OR REPLACE INTO approvals VALUES(?,?,?)', (t['id'], row['id'], time.time()))
            self.s.db.execute('DELETE FROM track_choices WHERE track=?', (t['id'],))
            self.s.db.execute("UPDATE jobs SET status='active' WHERE id=? AND status='completed'", (t['job'],))
            self.s.db.execute("UPDATE tracks SET status='ready', detail='Seçtiğiniz aday sıraya alındı; iş "
                              "duraklatılmışsa sürdürün.' WHERE id=?", (t['id'],))
        self.dirty_jobs.add(t['job'])
        self.s.event(t['job'], f"Kullanıcı seçimi · {t['title']} · {row['peer']}")

    # ------------------------------------------------------------------ upgrades of existing data
    def migrate(self):
        if self.s.setting('spectrum_revision') != 'cliff-1':
            # The first spectrum measure flagged naturally dark masters; measure everything again.
            self.s.execute("UPDATE tracks SET quality_note='' WHERE status='done'")
            self.s.set_setting('spectrum_revision', 'cliff-1')
        if self.s.setting('scoring_revision') != SCORING_REVISION:
            self._fix_layouts()
            self._rescore()
            self._recover_rejected_files()
            self.s.set_setting('scoring_revision', SCORING_REVISION)

    def _fix_layouts(self):
        """Jobs imported with the wrong column order are re-read; finished files stay."""
        from matching import detect_layout
        for job in self.s.rows('SELECT * FROM jobs'):
            try:
                stored = self.s.stored_layout(job)
                detected = detect_layout(job['source'])
            except (OSError, ValueError):
                continue
            if stored and stored != detected and not self.s.one(
                    f"SELECT a.id FROM attempts a JOIN tracks t ON t.id=a.track WHERE t.job=? AND a.status IN "
                    f"({','.join('?' * len(ACTIVE))})", (job['id'], *ACTIVE)):
                changed = self.s.relayout_job(job['id'], detected)
                if changed:
                    self.s.event(job['id'], f'Sütun sırası otomatik düzeltildi ({changed} parça yeniden okunacak).')
                    continue
            if stored and not job.get('layout'):
                self.s.update('jobs', job['id'], layout=stored)

    def _rescore(self):
        """Re-evaluate stored candidates with the current matcher and search unfinished tracks again."""
        with self.s.db:
            for r in self.s.rows("""SELECT c.id, c.data, t.artist, t.title, t.position FROM candidates c
                                    JOIN tracks t ON t.id=c.track WHERE t.status!='done'"""):
                c = Candidate.from_dict(json.loads(r['data']))
                if not quality_allowed(c, manual=True):
                    self.s.db.execute('DELETE FROM candidates WHERE id=? AND id NOT IN (SELECT candidate FROM approvals)',
                                      (r['id'],))
                    continue
                c = score_candidate(Track(r['artist'], r['title'], r['position']), c, self.config)
                self.s.db.execute('UPDATE candidates SET data=?, score=?, eligible=? WHERE id=?',
                                  (json.dumps(c.to_dict(), ensure_ascii=False), c.score, int(c.eligible), r['id']))
            self.s.db.execute("""UPDATE tracks SET status='pending', rounds=0, variant=0, next_search=0,
                detail='Yeni eşleştirme ile yeniden aranacak' WHERE status IN ('not_found','review','retry_wait')
                AND search_id IS NULL""")

    def _recover_rejected_files(self):
        """Files rejected by the old validator (e.g. FLAC without a length) are checked again."""
        for a in self.s.rows("SELECT * FROM attempts WHERE status='failed' AND detail LIKE 'Dosya doğrulaması%'"):
            t = self.s.one('SELECT * FROM tracks WHERE id=?', (a['track'],))
            if not t or t['status'] == 'done':
                continue
            try:
                path = self.locate(a)
                if not path:
                    continue
                self.validator(path, a['size'], None)
                self.deliver(t, path, digest(path), move=True)
                self.s.update('attempts', a['id'], status='succeeded', detail='Yeniden doğrulandı')
            except (OSError, ValueError):
                continue
