import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import shutil
import subprocess
import xml.etree.ElementTree as ET

from audio import NO_CUTOFF, digest, find_ffmpeg, read_track_number, spectral_cutoff, validate_audio
from engine import Engine
from matching import (Candidate, Track, build_spec, detect_layout, extract_candidates, mismatch_html, parse_tracks,
                      quality, queries, score_candidate)
from slskd_api import ApiError
from store import Store

MB = 1048576


def candidate(peer='alpha', filename=r'Prospa\Album\01 - Don’t Stop.flac', ext='flac', bitrate=None, size=30 * MB,
              free=True, queue=0, length=None):
    return Candidate(peer, filename, size, bitrate, ext, queue, 1000000, free, length=length, score=100, eligible=True)


def ok(artist, title, filename, **kwargs):
    ext = filename.rsplit('.', 1)[-1].lower()
    kwargs.setdefault('bitrate', 320 if ext == 'mp3' else None)
    kwargs.setdefault('size', 12 * MB if ext == 'mp3' else 30 * MB)
    return score_candidate(Track(artist, title), candidate(filename=filename, ext=ext, **kwargs))


class MatchingTests(unittest.TestCase):
    def assertMatch(self, artist, title, filename, expected=True, **kwargs):
        c = ok(artist, title, filename, **kwargs)
        self.assertEqual(c.eligible, expected, f'{filename}: {c.reason} ({c.score})')
        if expected:
            self.assertGreaterEqual(c.score, 92)
        return c

    def test_identity_and_wrong_title(self):
        self.assertMatch('Prospa', 'Don’t Stop', r'Prospa\Album\01 - Don’t Stop.flac')
        self.assertMatch('Prospa', 'Don’t Stop', r'Tea-chi\Don’t Stop The Footworks.flac', False)
        self.assertMatch('Prospa', 'Don’t Stop', r'Prospa\Don’t Stop The Footworks.flac', False)
        self.assertMatch('Oden & Fatzo', 'Fly Away', r'Oden\Fly Away.flac', False)

    def test_original_and_extended_mix_are_the_default_version(self):
        # Seen in the database: these were rejected before.
        self.assertMatch('Brooklyn Baby', 'Love Overdose', r'x\Brooklyn Baby - Love Overdose (Original Mix).mp3')
        self.assertMatch('Efenkei', 'Sexual - Original Mix', r'music\Efenkei\2014 - Efenkei\02 - Sexual.mp3')
        self.assertMatch('Prospa', 'Don’t Stop', r'Prospa\Don’t Stop (Extended Mix).flac')
        self.assertMatch('Sem Jacobs, Tagmann', 'DC Ten Thousand Dreams Extended Mix',
                         r'x\Sem Jacobs, Tagmann - DC Ten Thousand Dreams (Extended Mix).mp3')

    def test_different_versions_are_not_automatic(self):
        self.assertMatch('Prospa', 'Don’t Stop', r'Prospa\Live\Don’t Stop (Live).flac', False)
        self.assertMatch('antraum, Anton, Traumer', 'Hachi - Short Edit',
                         r'x\Traumer, Anton, antraum - Hachi (Original Mix).mp3', False)
        self.assertMatch('Tim Deluxe', "Tryin' Find A Way", r"x\Tim Deluxe - Tryin' Find A Way (Dub).flac", False)
        self.assertMatch('Thierry Tomas', 'Why Why', r'x\88. Thierry Tomas - Why Why (Fred Everything Remix).mp3', False)

    def test_named_remixes(self):
        t = ('Borai & Denham Audio, Franky Rizardo', 'Make Me - Franky Rizardo Remix')
        self.assertMatch(*t, r'Chart\89. Borai & Denham Audio - Make Me (Franky Rizardo Extended Remix).flac')
        for name in ['Other Artist Extended Remix', 'Extended Mix', 'Franky Rizardo Live Remix']:
            self.assertMatch(*t, 'Borai & Denham Audio - Make Me (' + name + ').flac', False)
        self.assertMatch('Akabu, Andre Lodderman', 'Another World - Andre Lodderman Mix',
                         r'x\akabu - another world (andre lodderman remix).mp3')
        self.assertMatch('Johannes Volk', 'Inferno - Unbalance Remix',
                         r'x\04-inferno_(unbalance_remix)-eithel.mp3', False)          # artist missing
        self.assertMatch('Johannes Volk', 'Inferno - Unbalance Remix',
                         r'Johannes Volk\Second Resistance\04-inferno_(unbalance_remix)-eithel.mp3')

    def test_real_world_names(self):
        self.assertMatch('UVB', 'Stop Motion - Original Mix', r'Shared\UVB\[2014] EP\05 - Stop Motion (digi only).mp3')
        self.assertMatch('Somatic Responses', 'Open Window', r'KERN005CD\118-somatic_responses-open_window.flac')
        self.assertMatch('Oden & Fatzo, Camden Cox', 'Lady Love',
                         r'Top 100\70. Camden Cox, Oden & Fatzo - Lady Love (Extended Mix).mp3')
        self.assertMatch('Oden and Fatzo', 'Lady Love', r'x\Oden & Fatzo - Lady Love.mp3')
        self.assertMatch('Soufflé Caramel', 'Club de Jazz',
                         r'x\Soufflé Caramel - Club de Jazz [www.DJdownloadMe.com].mp3')
        self.assertMatch('Max Sàni', 'Waiting', r'x\Max Sani - Waiting.mp3')
        self.assertMatch('Manni Dee, Ewa Justka', "London Isn't England",
                         r"x\01 - Manni Dee - London Isn't England feat. Ewa Justka.mp3")
        self.assertMatch('ANOTR', '24 (Turn It Up)', r'x\64. ANOTR - 24 (Turn It Up) (+6).mp3')
        self.assertMatch('Kamasutra', 'Kamasutra Express (Train Version)',
                         r'x\Kamasutra - Kamasutra Express Train Version.mp3')
        self.assertMatch('Hypnoskull', "Let's (Don't Go)", r"x\01-Hypnoskull-Let's (Don't Go).flac")

    def test_scene_and_disc_numbered_names(self):
        # Carried over from the GitHub test suite (25 Sep fixes).
        self.assertMatch('Solenoid', 'Modular Brain', r'VA-MOD010\08-solenoid-modular_brain.flac')
        self.assertMatch('Solenoid', 'Modular Brain', r'VA-MOD010\08-solenoid-modular_brain_remix.flac', False)
        self.assertMatch('Joss Moog', 'Secret Garden', r'VA - KM5 Ibiza-11. Joss Moog - Secret Garden.flac')
        self.assertMatch('Joss Moog', 'Secret Garden', r'VA - KM5 Ibiza-11. Joss Moog - Secret Garden (Remix).flac', False)

    def test_truncated_txt_names_match_as_prefix(self):
        self.assertMatch('Saison, Miss Yankey', 'Making Shapes - Scott Dia...',
                         r'x\Saison, Miss Yankey - Making Shapes (Scott Diaz Remix).mp3')
        self.assertMatch('Saison, Miss Yankey', 'Making Shapes - Scott Dia...',
                         r'x\Saison, Miss Yankey - Making Shapes (Original Mix).mp3', False)
        self.assertMatch('Billy Cobham, Motor City Dru...', 'Shining Sun - Motor City D...',
                         r'Billy Cobham\03 Shining Sun (Motor City Drum Ensemble Winter Sun Remix).flac')
        self.assertMatch('Felipe Gordon', 'The Semimodular Bird of J...',
                         r'x\Felipe Gordon - The Semimodular Bird of Jupiter.mp3')

    def test_quality_rules(self):
        self.assertTrue(ok('ANOTR', 'Helping Hand', r'x\ANOTR - Helping Hand.aif').eligible)
        self.assertFalse(ok('Devoye', 'Ok', r'x\Devoye - Ok.mp3', bitrate=128).eligible)
        self.assertTrue(ok('Kolter', 'Be Real', r'x\Kolter - Be Real.mp3', bitrate=None).eligible)
        # Unknown bitrate but size/length reveals ~128 kbps.
        self.assertFalse(ok('Kolter', 'Be Real', r'x\Kolter - Be Real.mp3', bitrate=None, size=6 * MB,
                            length=390).eligible)
        self.assertFalse(ok('Kolter', 'Be Real', r'x\Kolter - Be Real.m4a').eligible)
        lossless = quality(candidate())
        self.assertGreater(lossless.tier, quality(candidate(ext='mp3', bitrate=320, size=12 * MB)).tier)
        self.assertFalse(quality(candidate(ext='wav', size=40 * MB), {'accept_wav_aiff': False}).automatic)

    def test_queries_drop_version_words_split_apostrophes_and_keep_accents(self):
        self.assertEqual(queries(Track('UVB', 'Stop Motion - Original Mix'))[0], 'UVB Stop Motion')
        self.assertEqual(queries(Track('Prospa', 'Don’t Stop'))[0], 'Prospa Don Stop')
        self.assertEqual(queries(Track('Tim Deluxe', "Tryin' Find A Way - Club Mix"))[0], 'Tim Deluxe Tryin Find Way')
        plan = queries(Track('Soufflé Caramel', 'Club de Jazz'))
        self.assertEqual(plan[0], 'Soufflé Caramel Club Jazz')
        self.assertIn('Souffle Caramel Club Jazz', plan)
        self.assertIn('Inferno Unbalance', queries(Track('Johannes Volk', 'Inferno - Unbalance Remix')))
        self.assertEqual(queries(Track('Felipe Gordon', 'The Semimodular Bird of J...'))[0], 'Felipe Gordon Semimodular Bird')
        self.assertNotIn('Tim Deluxe', queries(Track('Tim Deluxe', 'JAS')))     # no artist-only floods

    def test_spec_parts(self):
        spec = build_spec(Track('Prospa, RAHH', 'This Rhythm (feat. RAHH)'))
        self.assertEqual((spec.core, spec.version, spec.credits), ('this rhythm', None, ('prospa', 'rahh')))
        self.assertFalse(build_spec(Track('Devoye', 'Ok...')).title_truncated)    # the real title
        spec = build_spec(Track('Motor City Drum Ensemble', 'L.O.V.E. (DJ-Kicks) - Exten...'))
        self.assertEqual((spec.version, spec.version_prefix), (None, ''))

    def test_layout_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'list.txt'
            path.write_text('Disco Party Baby (Original Mix) - Vampire Sex\nJiggy Jerry - Sem Jacobs, Tagmann\n'
                            'Tell Me - Marius Acke\n', encoding='utf-8')
            self.assertEqual(detect_layout(path), 'title_artist')
            self.assertEqual(parse_tracks(path)[1].artist, 'Sem Jacobs, Tagmann')
            path.write_text('Niki Istrefi - Red Armor\nManni Dee, Ewa Justka - London Isn\'t England\n'
                            'Prospa - Don\'t Stop (Extended Mix)\n', encoding='utf-8')
            self.assertEqual(detect_layout(path), 'artist_title')
            path.write_text('01. Prospa – Don’t Stop\n# comment\n', encoding='utf-8')
            self.assertEqual(parse_tracks(path, 'artist_title')[0].artist, 'Prospa')

    def test_csv_import_with_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'spotify.csv'
            path.write_text('"Track URI","Track Name","Artist Name(s)","Album Name","Duration (ms)"\n'
                            '"spotify:track:1","Don\'t Stop","Prospa","Don\'t Stop","185000"\n'
                            '"spotify:track:2","Lady Love - Extended Mix","Oden & Fatzo, Camden Cox","Lady Love","372000"\n',
                            encoding='utf-8')
            self.assertEqual(detect_layout(path), 'csv')
            tracks = parse_tracks(path)
            self.assertEqual([(t.artist, t.title, t.duration, t.album) for t in tracks][1],
                             ('Oden & Fatzo, Camden Cox', 'Lady Love - Extended Mix', 372.0, 'Lady Love'))

    def test_known_duration_rejects_short_files_only(self):
        track = Track('Prospa', 'Don’t Stop', 1, 185.0)
        short = score_candidate(track, candidate(filename=r'Prospa\Don’t Stop.flac', length=60))
        self.assertFalse(short.eligible)
        self.assertIn('Süre kısa', short.reason)
        self.assertTrue(score_candidate(track, candidate(filename=r'Prospa\Don’t Stop.flac', length=372)).eligible)
        self.assertTrue(score_candidate(track, candidate(filename=r'Prospa\Don’t Stop.flac')).eligible)

    def test_mismatch_highlight(self):
        html = mismatch_html(Track('Tim Deluxe', 'JAS - Club Mix'), r'x\Tim Deluxe - JAS (Dub) [WEB].flac')
        self.assertIn('<b style="color:#d9534f">(Dub)</b>', html)
        self.assertIn('[WEB]', html)
        self.assertNotIn('<b', mismatch_html(Track('Tim Deluxe', 'JAS - Club Mix'), r'x\JAS (Club Mix).flac'))

    def test_extract_skips_non_audio(self):
        responses = [{'username': 'p', 'hasFreeUploadSlot': True, 'queueLength': 0, 'uploadSpeed': 10,
                      'files': [{'filename': r'a\cover.jpg', 'size': 1}, {'filename': r'a\x.sfv', 'size': 1},
                                {'filename': r'a\Song.flac', 'size': 30 * MB, 'extension': ''}]}]
        self.assertEqual([c.filename for c in extract_candidates(responses, 's')], [r'a\Song.flac'])


class AudioTests(unittest.TestCase):
    def test_streamed_flac_without_length_is_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'a.flac'
            path.write_bytes(b'x')
            flac = SimpleNamespace(info=SimpleNamespace(length=0.0, sample_rate=44100, channels=2))
            with patch('audio.mutagen.File', return_value=flac):
                validate_audio(path, 1)
            with patch('audio.mutagen.File', return_value=SimpleNamespace(info=SimpleNamespace(length=0, sample_rate=0, channels=0))):
                with self.assertRaises(ValueError):
                    validate_audio(path, 1)
            mp3 = path.with_suffix('.mp3')
            mp3.write_bytes(b'x')
            with patch('audio.mutagen.File', return_value=SimpleNamespace(info=SimpleNamespace(length=200, bitrate=192000))):
                validate_audio(mp3, 1)
                with self.assertRaises(ValueError):
                    validate_audio(mp3, 1, 320)


@unittest.skipUnless(find_ffmpeg(), 'ffmpeg yok')
class SpectrumTests(unittest.TestCase):
    def noise(self, folder, name, chain):
        path = Path(folder) / name
        subprocess.run([find_ffmpeg(), '-v', 'error', '-f', 'lavfi', '-i', 'anoisesrc=d=40:c=pink:r=44100:a=0.3',
                        *chain, str(path)], check=True)
        return path

    def test_lowpass_wall_is_found_but_natural_rolloff_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            full = self.noise(folder, 'full.flac', [])
            self.assertEqual(spectral_cutoff(full, find_ffmpeg()), NO_CUTOFF)
            # Dark but genuine: gentle roll-off, no wall.
            dark = self.noise(folder, 'dark.flac', ['-af', 'treble=g=-12:f=12000'])
            self.assertEqual(spectral_cutoff(dark, find_ffmpeg()), NO_CUTOFF)
            # A 128 kbps MP3 rewrapped as FLAC.
            mp3 = self.noise(folder, 'low.mp3', ['-ac', '2', '-b:a', '128k'])
            fake = Path(folder) / 'fake.flac'
            subprocess.run([find_ffmpeg(), '-v', 'error', '-i', str(mp3), str(fake)], check=True)
            self.assertLess(spectral_cutoff(fake, find_ffmpeg()), 19.0)


class FakeApi:
    def __init__(self):
        self.batches, self.transfers, self.cancellations, self.searches = {}, {}, [], []
        self.search_results, self.deleted, self.queues = {}, [], 0
        self.enqueue_error = None

    def authenticate(self): return 'test'
    def server(self): return {'isLoggedIn': True}
    def server_connected(self, s): return True

    def create_search(self, ident, text): self.searches.append((text, ident))
    def search_state(self, ident): return {'isComplete': ident in self.search_results}
    def search_responses(self, ident): return self.search_results.get(ident, [])
    def delete_search(self, ident): self.deleted.append(ident)

    def enqueue(self, batch_id, peer, filename, size, destination):
        self.queues += 1
        if self.enqueue_error:
            raise ApiError(self.enqueue_error)
        ident = 'transfer-' + batch_id
        self.batches[batch_id] = {'transfers': [{'id': ident}]}
        self.transfers[ident] = {'state': 'Queued, Remotely', 'bytesTransferred': 0, 'size': size}
        return {'batch': self.batches[batch_id]}

    def batch(self, ident):
        if ident not in self.batches:
            raise ApiError(404)
        return self.batches[ident]

    def downloads(self): return dict(self.transfers)
    def get_download(self, peer, ident): return self.transfers[ident]

    def cancel(self, peer, ident):
        self.cancellations.append(ident)
        self.transfers[ident]['state'] = 'Completed, Cancelled'


def responses(*names, peer='alpha', free=True):
    return [{'username': peer, 'hasFreeUploadSlot': free, 'queueLength': 0, 'uploadSpeed': 1000000,
             'files': [{'filename': n, 'size': 30 * MB} for n in names]}]


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'list.txt'
        self.source.write_text('Prospa - Don’t Stop\n', encoding='utf-8')
        self.store = Store(self.root)
        self.job = self.store.add_job(self.source, 'artist_title')
        self.store.update('jobs', self.job, status='active')
        self.track = self.store.one('SELECT * FROM tracks')
        self.api = FakeApi()
        self.engine = self.make_engine()

    def make_engine(self, **config):
        base = {'slskd_url': 'http://localhost:5030', 'slskd_download_dir': str(self.root / 'incoming'),
                'search_interval_seconds': 0, 'check_fake_lossless': False}
        return Engine(self.store, dict(base, **config), self.api, lambda path, size, minimum=None: None)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def add_candidate(self, c, eligible=True):
        self.store.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)',
                           (c.peer, self.track['id'], c.peer, c.filename, json.dumps(c.to_dict()), c.score,
                            int(eligible), time.time()))

    def status(self):
        return self.store.one('SELECT status FROM tracks')['status']

    def complete(self, attempt, content=b'a'):
        folder = self.engine.source_root / attempt['stage']
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'Don’t Stop.flac').write_bytes(content * attempt['size'])
        self.api.transfers[attempt['transfer_id']]['state'] = 'Completed, Succeeded'

    # -- searching ------------------------------------------------------------
    def test_search_collects_scores_and_cleans_up(self):
        self.engine.schedule_search()
        query, ident = self.api.searches[0]
        self.assertEqual(query, 'Prospa Don Stop')
        self.store.update('tracks', self.track['id'], search_started=0)
        self.api.search_results[ident] = responses(r'Prospa\Don’t Stop.flac', r'Prospa\cover.jpg')
        self.engine.collect_searches()
        self.assertEqual(self.api.deleted, [ident])
        self.assertEqual(self.status(), 'ready')
        self.assertEqual(self.store.one('SELECT COUNT(*) n FROM candidates')['n'], 1)
        self.engine.fill_slots()
        self.assertEqual(self.api.queues, 1)

    def test_parallel_searches_respect_capacity(self):
        self.source.write_text('\n'.join(f'Artist{i} - Song{i}' for i in range(5)), encoding='utf-8')
        job = self.store.add_job(self.source, 'artist_title')
        self.store.update('jobs', job, status='active')
        engine = self.make_engine(max_parallel_searches=3)
        engine.schedule_search()
        self.assertEqual(len(self.api.searches), 3)

    def test_unfinished_search_waits_then_times_out(self):
        self.source.write_text('Oden & Fatzo, Camden Cox - Lady Love\n', encoding='utf-8')
        self.store.execute('DELETE FROM tracks')
        self.job = self.store.add_job(self.source, 'artist_title')
        self.store.update('jobs', self.job, status='active')
        self.track = self.store.one('SELECT * FROM tracks')
        self.engine.schedule_search()
        self.store.update('tracks', self.track['id'], search_started=0)
        self.engine.collect_searches()
        self.assertIsNotNone(self.store.one('SELECT search_id FROM tracks')['search_id'])
        self.store.update('tracks', self.track['id'], search_deadline=0)
        self.engine.collect_searches()
        row = self.store.one('SELECT * FROM tracks')
        self.assertIsNone(row['search_id'])
        self.assertEqual((row['status'], row['variant']), ('pending', 1))   # next query is next

    def test_all_queries_without_result_back_off(self):
        plan = queries(Track('Prospa', 'Don’t Stop'))
        for _ in plan:
            self.store.update('tracks', self.track['id'], next_search=0)
            self.engine.schedule_search()
            self.store.update('tracks', self.track['id'], search_started=0, search_deadline=0)
            self.engine.collect_searches()
        row = self.store.one('SELECT * FROM tracks')
        self.assertEqual((row['status'], row['rounds'], row['variant']), ('not_found', 1, 0))
        self.assertGreater(row['next_search'], time.time() + 1000)

    # -- downloads --------------------------------------------------------------
    def test_peer_ranking_prefers_free_slot_then_quality(self):
        self.add_candidate(candidate('queued-flac', free=False, queue=40))
        self.add_candidate(candidate('free-mp3', r'Prospa\Don’t Stop.mp3', 'mp3', 320, 12 * MB))
        self.add_candidate(candidate('free-flac'))
        self.store.update('jobs', self.job, mode='patient')
        self.engine.fill_slots()
        self.assertEqual(self.engine.active()[0]['peer'], 'free-flac')

    def test_failed_peer_falls_back_and_offline_peer_is_skipped(self):
        for peer in ['alpha', 'beta', 'gamma']:
            self.add_candidate(candidate(peer))
        self.engine.fill_slots()
        self.assertEqual(self.api.queues, 2)
        a = self.engine.active()[0]
        self.api.transfers[a['transfer_id']]['state'] = 'Completed, Errored'
        self.engine.poll_transfers()
        self.engine.fill_slots()
        self.assertEqual(self.api.queues, 3)
        self.assertEqual(len(self.engine.active()), 2)

    def test_offline_peer_fails_immediately(self):
        self.add_candidate(candidate())
        self.api.enqueue_error = 404
        self.engine.fill_slots()
        attempt = self.store.one('SELECT * FROM attempts')
        self.assertEqual((attempt['status'], attempt['detail']), ('failed', 'Peer çevrimdışı'))
        self.engine.fill_slots()
        self.assertEqual(self.status(), 'retry_wait')

    def test_busy_slskd_is_retried_with_same_batch(self):
        self.add_candidate(candidate())
        self.api.enqueue_error = 429
        self.engine.fill_slots()
        self.assertEqual(self.store.one('SELECT status FROM attempts')['status'], 'submitting')
        self.api.enqueue_error = None
        self.engine.poll_transfers()
        attempt = self.store.one('SELECT * FROM attempts')
        self.assertEqual(attempt['status'], 'queued')
        self.assertEqual(attempt['transfer_id'], 'transfer-' + attempt['id'])

    def test_uncertain_submit_recovers_without_duplicate(self):
        self.add_candidate(candidate())
        self.engine.fill_slots()
        a = self.engine.active()[0]
        self.store.update('attempts', a['id'], status='submitting', transfer_id=None)
        self.engine.poll_transfers()
        self.assertEqual(self.api.queues, 1)
        self.assertEqual(self.engine.active()[0]['transfer_id'], a['transfer_id'])

    def test_daily_peer_limit(self):
        for i in range(5):
            self.add_candidate(candidate(f'peer{i}'))
        engine = self.make_engine(peer_attempts_per_day=3)
        self.api.enqueue_error = 404
        for _ in range(4):
            engine.fill_slots()
        self.assertEqual(self.api.queues, 3)
        engine.command('retry', self.job)               # user asks again: the window restarts
        self.api.enqueue_error = None
        engine.fill_slots()
        self.assertEqual(self.api.queues, 5)

    def test_long_remote_queue_asks_one_more_peer(self):
        for peer in ['alpha', 'beta', 'gamma']:
            self.add_candidate(candidate(peer))
        self.engine.fill_slots()
        self.assertEqual(self.api.queues, 2)
        self.engine.poll_transfers()
        self.store.execute('UPDATE attempts SET created=?', (time.time() - 3600,))
        self.engine.fill_slots()
        self.assertEqual(self.api.queues, 3)
        self.assertEqual(self.api.cancellations, [])

    def test_stalled_download_is_cancelled(self):
        self.add_candidate(candidate())
        self.store.update('jobs', self.job, mode='patient')
        self.engine.fill_slots()
        a = self.engine.active()[0]
        self.api.transfers[a['transfer_id']].update(state='InProgress', bytesTransferred=10)
        self.engine.poll_transfers()
        self.assertEqual(self.status(), 'downloading')
        self.store.execute('UPDATE attempts SET progress_at=?', (time.time() - 3600,))
        self.engine.poll_transfers()
        self.assertEqual(self.store.one('SELECT status FROM attempts')['status'], 'failed')
        self.assertIn(a['transfer_id'], self.api.cancellations)

    def test_winner_delivered_then_loser_cancelled(self):
        for peer in ['alpha', 'beta']:
            self.add_candidate(candidate(peer))
        self.engine.fill_slots()
        a, b = self.engine.active()
        self.complete(a)
        self.engine.poll_transfers()
        self.engine.poll_transfers()
        t = self.store.one('SELECT * FROM tracks')
        self.assertEqual(t['status'], 'done')
        self.assertTrue(Path(t['path']).exists())
        self.assertFalse((self.engine.source_root / a['stage']).exists())
        self.assertIn(b['transfer_id'], self.api.cancellations)
        self.assertTrue((Path(t['path']).parent / 'list.m3u8').exists())

    def test_rejected_file_tries_next_peer(self):
        for peer in ['alpha', 'beta', 'gamma']:
            self.add_candidate(candidate(peer))
        engine = Engine(self.store, {'slskd_download_dir': str(self.root / 'incoming'), 'check_fake_lossless': False},
                        self.api, lambda path, size, minimum=None: (_ for _ in ()).throw(ValueError('bozuk')))
        engine.fill_slots()
        a = engine.active()[0]
        self.complete(a)
        engine.poll_transfers()
        self.assertIn('bozuk', self.store.one('SELECT detail FROM attempts WHERE id=?', (a['id'],))['detail'])
        engine.fill_slots()
        self.assertEqual(self.api.queues, 3)

    def test_same_size_collision_does_not_overwrite(self):
        source = self.root / 'Song.flac'
        source.write_bytes(b'AAA')
        self.engine.deliver(self.track, source, digest(source), False)
        source.write_bytes(b'BBB')
        self.engine.deliver(self.track, source, digest(source), False)
        folder = Path(self.store.one('SELECT folder FROM jobs')['folder'])
        self.assertEqual((folder / 'Song.flac').read_bytes(), b'AAA')
        self.assertEqual((folder / 'Song (2).flac').read_bytes(), b'BBB')

    def test_same_song_in_two_playlists_downloads_once(self):
        source = self.root / 'Song.flac'
        source.write_bytes(b'AAA')
        self.engine.deliver(self.track, source, digest(source), False)
        job2 = self.store.add_job(self.source, 'artist_title')
        self.store.update('jobs', job2, status='active')
        self.engine.fill_slots()
        self.assertEqual(self.api.queues, 0)
        self.assertEqual(self.store.one("SELECT COUNT(*) n FROM tracks WHERE status='done'")['n'], 2)

    # -- user choices -------------------------------------------------------
    def test_manual_selection_queues_only_selected_even_after_failure(self):
        c = candidate('chosen', ext='mp3', bitrate=192, size=8 * MB)
        self.add_candidate(c, eligible=False)
        self.store.execute("INSERT INTO attempts(id,track,peer,filename,size,data,status,created) VALUES"
                           "('old',?,'chosen','x',1,'{}','failed',?)", (self.track['id'], time.time()))
        self.engine.command('approve', {'track': self.track['id'], 'candidate': 'chosen'})
        self.assertEqual(self.api.queues, 0)
        self.engine.fill_slots()
        self.engine.fill_slots()
        self.assertEqual(self.api.queues, 1)
        attempt = self.engine.active()[0]
        self.assertEqual((attempt['peer'], attempt['manual']), ('chosen', 1))

    def test_manual_selection_preview_and_pause(self):
        self.add_candidate(candidate())
        self.store.update('jobs', self.job, mode='preview')
        with self.assertRaises(ValueError):
            self.engine.command('approve', {'track': self.track['id'], 'candidate': 'alpha'})
        self.store.update('jobs', self.job, mode='fast', status='paused')
        self.engine.command('approve', {'track': self.track['id'], 'candidate': 'alpha'})
        self.engine.fill_slots()
        self.assertEqual(self.api.queues, 0)
        self.engine.command('start', self.job)
        self.engine.fill_slots()
        self.assertEqual(self.api.queues, 1)

    def test_skip_and_defer_block_automatic_work(self):
        self.add_candidate(candidate())
        for action, status in [('skip_track', 'skipped'), ('defer_track', 'deferred')]:
            self.engine.command(action, self.track['id'])
            self.engine.command('retry', self.job)
            self.engine.schedule_search()
            self.engine.fill_slots()
            self.assertEqual((self.api.queues, self.api.searches), (0, []))
            self.assertEqual(self.status(), status)

    def test_late_search_cannot_clear_deferred_choice(self):
        self.store.update('tracks', self.track['id'], search_id='old-search', search_deadline=0)
        self.engine.command('defer_track', self.track['id'])
        self.engine.collect_searches()
        self.assertEqual(self.status(), 'deferred')

    def test_active_transfer_blocks_choices(self):
        self.add_candidate(candidate())
        self.engine.fill_slots()
        for action in ('skip_track', 'defer_track', 'restore_track'):
            with self.assertRaises(ValueError):
                self.engine.command(action, self.track['id'])

    def test_pause_and_cancel(self):
        self.add_candidate(candidate())
        self.engine.command('pause', self.job)
        self.engine.fill_slots()
        self.engine.schedule_search()
        self.assertEqual((self.api.queues, self.api.searches), (0, []))
        self.engine.command('start', self.job)
        self.engine.fill_slots()
        a = self.engine.active()[0]
        self.engine.command('cancel', self.job)
        self.engine.poll_transfers()
        self.assertIn(a['transfer_id'], self.api.cancellations)

    def test_preview_never_downloads_and_completes(self):
        self.store.update('jobs', self.job, mode='preview')
        self.engine.schedule_search()
        query, ident = self.api.searches[0]
        self.store.update('tracks', self.track['id'], search_started=0)
        self.api.search_results[ident] = responses(r'Prospa\Don’t Stop.flac')
        self.engine.collect_searches()
        self.engine.fill_slots()
        self.engine.finish_jobs()
        self.assertEqual(self.api.queues, 0)
        self.assertEqual(self.store.one('SELECT status FROM jobs')['status'], 'preview_complete')

    def test_crash_resume_keeps_running_search(self):
        self.engine.schedule_search()
        resumed = self.make_engine()
        resumed.schedule_search()
        self.assertEqual(len(self.api.searches), 1)

    # -- new tools -------------------------------------------------------------
    def test_manual_search_keeps_query_rotation(self):
        self.engine.command('manual_search', {'track': self.track['id'], 'query': '  Prospa   Dont Stop VIP '})
        query, ident = self.api.searches[0]
        self.assertEqual(query, 'Prospa Dont Stop VIP')
        with self.assertRaises(ValueError):
            self.engine.command('manual_search', {'track': self.track['id'], 'query': 'again'})
        self.store.update('tracks', self.track['id'], search_started=0)
        self.api.search_results[ident] = responses(r'Prospa\Don’t Stop.flac')
        self.engine.collect_searches()
        row = self.store.one('SELECT * FROM tracks')
        self.assertEqual((row['status'], row['variant'], row['rounds'], row['search_kind']), ('ready', 0, 0, ''))
        self.assertIn('Elle arama', row['detail'])

    def test_edit_track_restarts_search(self):
        self.add_candidate(candidate())
        self.engine.command('edit_track', {'track': self.track['id'], 'artist': 'Prospa', 'title': 'Don’t Stop (VIP)'})
        row = self.store.one('SELECT * FROM tracks')
        self.assertEqual((row['title'], row['status']), ('Don’t Stop (VIP)', 'pending'))
        self.assertEqual(self.store.one('SELECT COUNT(*) n FROM candidates')['n'], 0)

    def test_rename_and_remove_job(self):
        source = self.root / 'Song.flac'
        source.write_bytes(b'AAA')
        self.engine.deliver(self.track, source, digest(source), False)
        folder = Path(self.store.one('SELECT folder FROM jobs')['folder'])
        self.engine.command('rename_job', {'job': self.job, 'name': 'Akşam'})
        self.assertTrue((folder / 'Akşam.m3u8').exists())
        self.assertFalse((folder / 'list.m3u8').exists())
        self.engine.command('remove_job', self.job)
        self.assertIsNone(self.store.one('SELECT id FROM jobs'))
        self.assertTrue((folder / 'Song.flac').exists())          # files stay on disk

    def test_rekordbox_xml_lists_tracks_in_order(self):
        source = self.root / 'Song.flac'
        source.write_bytes(b'AAA')
        self.engine.deliver(self.track, source, digest(source), False)
        folder = Path(self.store.one('SELECT folder FROM jobs')['folder'])
        root = ET.parse(folder / 'list.rekordbox.xml').getroot()
        track = root.find('COLLECTION/TRACK')
        self.assertEqual((track.get('Artist'), track.get('Kind')), ('Prospa', 'FLAC File'))
        self.assertTrue(track.get('Location').startswith('file://localhost/'))
        self.assertEqual(root.find('PLAYLISTS/NODE/NODE').get('Name'), 'list')

    def test_unreliable_peer_is_tried_last(self):
        for peer in ['flaky', 'steady']:
            self.add_candidate(candidate(peer))
        for _ in range(4):
            self.engine.peer_result('flaky', ok=False)
        self.store.update('jobs', self.job, mode='patient')
        self.engine.fill_slots()
        self.assertEqual(self.engine.active()[0]['peer'], 'steady')

    def test_suspicious_spectrum_rejected_twice_then_kept(self):
        for peer in ['a', 'b', 'c']:
            self.add_candidate(candidate(peer))
        self.store.update('jobs', self.job, mode='patient')
        self.engine.spectrum = lambda path: 16.0
        for round_ in range(3):
            self.engine.fill_slots()
            a = self.engine.active()[0]
            self.complete(a)
            self.engine.poll_transfers()
        self.assertEqual(self.store.one("SELECT COUNT(*) n FROM attempts WHERE detail LIKE 'Şüpheli%'")['n'], 2)
        row = self.store.one('SELECT * FROM tracks')
        self.assertEqual(row['status'], 'done')
        self.assertIn('şüpheli', row['quality_note'])
        self.assertTrue(any((self.root / 'Quarantine').iterdir()))

    def test_library_copy_instead_of_search(self):
        library = self.root / 'Music' / 'Prospa'
        library.mkdir(parents=True)
        (library / 'Prospa - Don’t Stop.flac').write_bytes(b'x' * 30 * MB)
        engine = self.make_engine(library_dirs=[str(self.root / 'Music')])
        while True:
            engine.scan_library_step()
            if engine.library_walk is None:
                break
        engine.schedule_search()
        self.assertEqual(self.api.searches, [])
        self.assertEqual(self.status(), 'done')

    def test_share_warning(self):
        self.api.application = lambda: {'shares': {'files': 0, 'scanning': False}}
        self.engine.connect(0)
        self.assertEqual(self.store.snapshot()['share_warning'], True)

    @unittest.skipUnless(find_ffmpeg(), 'ffmpeg yok')
    def test_aiff_output_keeps_tags(self):
        source = self.root / 'tone.flac'
        subprocess.run([find_ffmpeg(), '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
                        '-metadata', 'artist=Prospa', str(source)], check=True)
        engine = Engine(self.store, {'slskd_download_dir': str(self.root / 'incoming'), 'output_format': 'aiff',
                                     'filename_template': 'artist_title', 'check_fake_lossless': False}, self.api)
        engine.deliver(self.track, source, digest(source), False)
        path = Path(self.store.one('SELECT path FROM tracks')['path'])
        self.assertEqual(path.name, 'Prospa - Don’t Stop.aiff')
        self.assertEqual(read_track_number(path), '1')
        validate_audio(path)

    # -- better source for a finished track ------------------------------------
    def finished_suspicious(self):
        source = self.root / 'Old.flac'
        source.write_bytes(b'OLD')
        self.engine.deliver(self.track, source, digest(source), False, note='16.5 kHz · şüpheli')
        return Path(self.store.one('SELECT path FROM tracks')['path'])

    def test_upgrade_keeps_file_until_better_one_arrives(self):
        old = self.finished_suspicious()
        self.engine.command('upgrade_suspicious', self.job)
        row = self.store.one('SELECT * FROM tracks')
        self.assertEqual((row['status'], row['upgrade']), ('pending', 1))
        self.engine.export(self.job)
        self.assertIn('Old.flac', (old.parent / 'list.m3u8').read_text(encoding='utf-8-sig'))
        self.add_candidate(candidate('better'))
        self.store.update('jobs', self.job, mode='patient')
        self.engine.spectrum = lambda path: 21.0
        self.engine.fill_slots()
        self.complete(self.engine.active()[0])
        self.engine.poll_transfers()
        row = self.store.one('SELECT * FROM tracks')
        self.assertEqual((row['status'], row['upgrade'], row['quality_note']), ('done', 0, '21 kHz'))
        self.assertNotEqual(Path(row['path']), old)
        self.assertFalse(old.exists())
        self.assertTrue((self.root / 'Quarantine' / ('replaced-' + self.track['id']) / 'Old.flac').exists())

    def test_upgrade_rejects_files_that_are_not_better(self):
        self.finished_suspicious()
        self.engine.command('upgrade_track', self.track['id'])
        self.add_candidate(candidate('same-again'))
        self.store.update('jobs', self.job, mode='patient')
        self.engine.spectrum = lambda path: 16.6
        self.engine.fill_slots()
        self.complete(self.engine.active()[0])
        self.engine.poll_transfers()
        attempt = self.store.one('SELECT * FROM attempts')
        self.assertEqual(attempt['status'], 'failed')
        self.assertIn('Daha iyi değil', attempt['detail'])
        self.assertTrue(Path(self.store.one('SELECT path FROM tracks')['path']).exists())

    def test_fruitless_upgrade_restores_finished_state(self):
        old = self.finished_suspicious()
        self.engine.command('upgrade_track', self.track['id'])
        for _ in queries(Track('Prospa', 'Don’t Stop')):
            self.store.update('tracks', self.track['id'], next_search=0)
            self.engine.schedule_search()
            self.store.update('tracks', self.track['id'], search_started=0, search_deadline=0)
            self.engine.collect_searches()
        row = self.store.one('SELECT * FROM tracks')
        self.assertEqual((row['status'], row['upgrade'], Path(row['path'])), ('done', 0, old))
        self.assertIn('mevcut dosya korundu', row['detail'])

    def test_bulk_upgrade_only_touches_suspicious_files(self):
        with self.assertRaises(ValueError):
            self.engine.command('upgrade_suspicious', self.job)
        self.finished_suspicious()
        self.store.update('tracks', self.track['id'], quality_note='21 kHz')
        with self.assertRaises(ValueError):
            self.engine.command('upgrade_suspicious', None)

    # -- upgrades -----------------------------------------------------------
    def test_wrong_layout_is_fixed_on_upgrade(self):
        self.source.write_text('Disco Party Baby (Original Mix) - Vampire Sex\nJiggy Jerry - Sem Jacobs, Tagmann\n',
                               encoding='utf-8')
        job = self.store.add_job(self.source, 'artist_title')
        self.store.update('jobs', job, layout='')
        self.store.set_setting('scoring_revision', 'old')
        self.make_engine()
        row = self.store.one('SELECT * FROM tracks WHERE job=? ORDER BY position', (job,))
        self.assertEqual((row['artist'], row['title']), ('Vampire Sex', 'Disco Party Baby (Original Mix)'))
        self.assertEqual(self.store.one('SELECT layout FROM jobs WHERE id=?', (job,))['layout'], 'title_artist')

    def test_upgrade_rescores_and_requeues_stuck_tracks(self):
        c = candidate(filename=r'Prospa\Don’t Stop (Original Mix).flac')
        self.add_candidate(c, eligible=False)
        self.add_candidate(candidate('junk', r'Prospa\cover.jpg', 'jpg'), eligible=False)
        self.store.update('tracks', self.track['id'], status='not_found', rounds=4)
        self.store.set_setting('scoring_revision', 'old')
        self.make_engine()
        self.assertEqual(self.store.one('SELECT eligible FROM candidates WHERE peer=?', ('alpha',))['eligible'], 1)
        self.assertIsNone(self.store.one("SELECT id FROM candidates WHERE peer='junk'"))
        self.assertEqual((self.status(), self.store.one('SELECT rounds FROM tracks')['rounds']), ('pending', 0))


if __name__ == '__main__':
    unittest.main(verbosity=2)
