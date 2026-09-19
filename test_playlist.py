import json
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from dataclasses import asdict
from pathlib import Path

from playlist_core import Store, Engine, Candidate, Track, score_candidate, parse_tracks, digest, queries, audio_metadata, write_track_number


def candidate(peer='alpha', filename=r'Prospa\Album\01 - Don’t Stop.flac'):
    return Candidate(peer,filename,8*1048576,None,'flac',0,1000000,True,score=100,eligible=True)


class FakeApi:
    def __init__(self):self.batches={};self.transfers={};self.cancellations=[];self.queues=0;self.searches=[]
    def authenticate(self):return 'test'
    def server(self):return {'isConnected':True}
    def server_connected(self,s):return s['isConnected']
    def batch(self,ident):
        if ident not in self.batches:
            e=RuntimeError('404');e.status=404;raise e
        return self.batches[ident]
    def enqueue(self,a):
        self.queues+=1;ident='transfer-'+a['id'];self.batches[a['id']]={'transfers':[{'id':ident}]}
        self.transfers[ident]={'state':'Queued, Remotely','bytesTransferred':0,'size':a['size']}
        return {'batch':self.batches[a['id']]}
    def get_download(self,peer,ident):return self.transfers[ident]
    def cancel(self,peer,ident):
        self.cancellations.append(ident);self.transfers[ident]['state']='Completed, Cancelled'
    def create_search(self,q,ident):self.searches.append((q,ident))
    def search_responses(self,ident):return []


class PlaylistTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'list.txt';self.source.write_text('Prospa - Don’t Stop\n',encoding='utf-8')
        self.store=Store(self.root);self.job=self.store.add_job(self.source,'artist_title')
        self.store.update('jobs',self.job,status='active')
        self.track=self.store.one('SELECT * FROM tracks')
        self.api=FakeApi();self.engine=Engine(self.store,{'slskd_url':'http://localhost:5030','slskd_download_dir':str(self.root/'incoming')},self.api,lambda p,s:None)
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def add_candidate(self,c):
        self.store.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)',(c.peer,self.track['id'],c.peer,c.filename,json.dumps(asdict(c)),c.score,1,time.time()))

    def test_identity_and_wrong_title(self):
        t=Track('Prospa','Don’t Stop',1)
        self.assertTrue(score_candidate(t,candidate()).eligible)
        self.assertFalse(score_candidate(t,candidate(filename=r'Tea-chi\Don’t Stop The Footworks.flac')).eligible)
        self.assertFalse(score_candidate(t,candidate(filename=r'Prospa\Don’t Stop The Footworks.flac')).eligible)
        self.assertTrue(score_candidate(t,candidate(filename=r'Prospa\Don’t Stop (Extended Mix).flac')).eligible)
        self.assertFalse(score_candidate(Track('Oden & Fatzo','Fly Away',1),candidate(filename=r'Oden\Fly Away.flac')).eligible)

    def test_album_does_not_change_track_version(self):
        self.assertTrue(score_candidate(Track('Prospa','Don’t Stop',1),candidate(filename=r'Prospa\Live Collection\01 - Don’t Stop.flac')).eligible)

    def test_named_remixer_and_extended(self):
        t=Track('Borai & Denham Audio, Franky Rizardo','Make Me - Franky Rizardo Remix',1)
        good=score_candidate(t,candidate(filename=r'Chart\89. Borai & Denham Audio - Make Me (Franky Rizardo Extended Remix).flac'))
        self.assertTrue(good.eligible);self.assertGreaterEqual(good.score,92)
        for name in ['Other Artist Extended Remix','Extended Mix','Franky Rizardo Live Remix']:
            self.assertFalse(score_candidate(t,candidate(filename='Borai & Denham Audio - Make Me ('+name+').flac')).eligible)

    def test_manual_selection_persists_and_only_queues_selected(self):
        c=candidate('chosen');c.eligible=False;c.score=80
        self.add_candidate(c)
        self.store.execute('UPDATE candidates SET eligible=0')
        self.store.update('tracks',self.track['id'],status='review')
        self.engine.command('approve',{'track':self.track['id'],'candidate':'chosen'})
        self.assertEqual(self.api.queues,0)
        other=Store(self.root)
        self.assertEqual(other.one('SELECT candidate FROM approvals')['candidate'],'chosen');other.db.close()
        self.engine.fill_slots();self.engine.fill_slots()
        self.assertEqual(self.api.queues,1)
        self.assertEqual(self.engine.active()[0]['peer'],'chosen')

    def test_manual_selection_quality_preview_and_pause(self):
        c=candidate();c.extension='mp3';c.bitrate=192;self.add_candidate(c)
        with self.assertRaises(ValueError):self.engine.command('approve',{'track':self.track['id'],'candidate':'alpha'})
        self.store.execute('DELETE FROM candidates');self.add_candidate(candidate())
        self.store.update('jobs',self.job,mode='preview')
        with self.assertRaises(ValueError):self.engine.command('approve',{'track':self.track['id'],'candidate':'alpha'})
        self.store.update('jobs',self.job,mode='fast',status='paused')
        self.engine.command('approve',{'track':self.track['id'],'candidate':'alpha'})
        self.engine.fill_slots();self.assertEqual(self.api.queues,0)
        self.engine.command('start',self.job);self.engine.fill_slots();self.assertEqual(self.api.queues,1)

    def test_skip_and_defer_persist_and_block_automatic_work(self):
        self.add_candidate(candidate())
        for action,status in [('skip_track','skipped'),('defer_track','deferred')]:
            self.engine.command(action,self.track['id'])
            reopened=Store(self.root)
            self.assertEqual(reopened.one('SELECT choice FROM track_choices')['choice'],status);reopened.db.close()
            self.engine.command('retry',self.job)
            self.engine.schedule_search();self.engine.fill_slots()
            self.assertEqual(self.api.queues,0);self.assertEqual(self.api.searches,[])
            self.assertEqual(self.store.one('SELECT status FROM tracks')['status'],status)

    def test_late_search_cannot_clear_deferred_choice(self):
        self.store.update('tracks',self.track['id'],search_id='old-search',search_deadline=0)
        self.engine.command('defer_track',self.track['id'])
        self.engine.collect_searches()
        self.assertEqual(self.store.one('SELECT status FROM tracks')['status'],'deferred')

    def test_deferred_selection_and_restore_work(self):
        self.add_candidate(candidate())
        self.engine.command('defer_track',self.track['id'])
        self.engine.command('approve',{'track':self.track['id'],'candidate':'alpha'})
        self.assertFalse(self.store.rows('SELECT * FROM track_choices'))
        self.engine.command('restore_track',self.track['id'])
        self.assertFalse(self.store.rows('SELECT * FROM approvals'))
        self.engine.schedule_search();self.assertEqual(len(self.api.searches),1)

    def test_pass_does_not_disrupt_active_transfer(self):
        self.add_candidate(candidate());self.engine.fill_slots()
        for action in ('skip_track','defer_track','restore_track'):
            with self.assertRaises(ValueError):self.engine.command(action,self.track['id'])
        with self.assertRaises(ValueError):self.engine.command('approve',{'track':self.track['id'],'candidate':'alpha'})
        self.assertEqual(len(self.engine.active()),1)

    def test_search_preserves_apostrophe(self):
        self.assertEqual(queries(Track('Prospa','Don’t Stop',1))[0],"prospa don't stop")

    def test_numbering_does_not_flip_artist(self):
        self.source.write_text('01. Prospa – Don’t Stop\n# comment\n',encoding='utf-8')
        self.assertEqual(parse_tracks(self.source)[0].artist,'Prospa')
        self.source.write_text('1. Don’t Stop - Prospa',encoding='utf-8')
        self.assertEqual(parse_tracks(self.source,'title_artist')[0].artist,'Prospa')

    def test_two_peers_wait_without_expiry(self):
        for peer in ['alpha','beta','gamma']:self.add_candidate(candidate(peer))
        self.engine.fill_slots();self.assertEqual(self.api.queues,2)
        self.store.execute('UPDATE attempts SET created=?',(time.time()-36000,))
        self.engine.poll_transfers();self.assertEqual(self.api.cancellations,[])
        self.assertEqual(len(self.engine.active()),2)

    def test_patient_and_preview(self):
        for peer in ['alpha','beta']:self.add_candidate(candidate(peer))
        self.store.update('jobs',self.job,mode='patient');self.engine.fill_slots();self.assertEqual(self.api.queues,1)
        self.store.update('jobs',self.job,mode='preview');self.engine.fill_slots();self.assertEqual(self.api.queues,1)

    def test_failed_peer_falls_back(self):
        for peer in ['alpha','beta','gamma']:self.add_candidate(candidate(peer))
        self.engine.fill_slots();a=self.engine.active()[0]
        self.api.transfers[a['transfer_id']]['state']='Completed, Errored'
        self.engine.poll_transfers();self.engine.fill_slots()
        self.assertEqual(self.api.queues,3);self.assertEqual(len(self.engine.active()),2)

    def test_uncertain_submit_recovers_without_duplicate(self):
        self.add_candidate(candidate());self.engine.fill_slots()
        a=self.engine.active()[0];self.store.update('attempts',a['id'],status='submitting',transfer_id=None)
        self.engine.poll_transfers();self.assertEqual(self.api.queues,1)
        self.assertEqual(self.engine.active()[0]['transfer_id'],a['transfer_id'])

    def test_winner_delivered_then_loser_cancelled(self):
        for peer in ['alpha','beta']:self.add_candidate(candidate(peer))
        self.engine.fill_slots();a,b=self.engine.active()
        folder=self.engine.source_root/a['stage'];folder.mkdir(parents=True)
        path=folder/'Don’t Stop.flac';path.write_bytes(b'a'*a['size'])
        self.api.transfers[a['transfer_id']]['state']='Completed, Succeeded'
        self.engine.poll_transfers();self.engine.poll_transfers()
        t=self.store.one('SELECT * FROM tracks')
        self.assertEqual(t['status'],'done');self.assertTrue(Path(t['path']).exists())
        self.assertIn(b['transfer_id'],self.api.cancellations)
        self.assertTrue((Path(t['path']).parent/'list.m3u8').exists())

    def test_same_size_collision_does_not_overwrite(self):
        source=self.root/'Song.flac';source.write_bytes(b'AAA')
        self.engine.deliver(self.track,source,digest(source),False)
        source.write_bytes(b'BBB');self.engine.deliver(self.track,source,digest(source),False)
        folder=Path(self.store.one('SELECT folder FROM jobs')['folder'])
        self.assertEqual((folder/'Song.flac').read_bytes(),b'AAA')
        self.assertEqual((folder/'Song (2).flac').read_bytes(),b'BBB')

    def test_pause_no_new_requests(self):
        self.add_candidate(candidate());self.engine.command('pause',self.job)
        self.engine.fill_slots();self.engine.schedule_search()
        self.assertEqual(self.api.queues,0);self.assertFalse(self.api.searches)

    def test_exhausted_peer_is_not_shown_as_download_waiting(self):
        self.add_candidate(candidate());self.engine.fill_slots()
        a=self.engine.active()[0]
        self.api.transfers[a['transfer_id']]['state']='Completed, Errored'
        self.engine.poll_transfers();self.engine.fill_slots()
        self.assertEqual(self.store.one('SELECT status FROM tracks')['status'],'retry_wait')

    def test_cancel_intent_does_not_enqueue(self):
        self.add_candidate(candidate());self.engine.fill_slots();a=self.engine.active()[0]
        self.api.batches.clear();self.store.update('attempts',a['id'],status='submitting',transfer_id=None)
        self.engine.command('cancel',self.job);self.engine.poll_transfers();self.assertEqual(self.api.queues,1)

    def test_job_names_and_snapshot_independent(self):
        j2=self.store.add_job(self.source,'artist_title')
        self.assertNotEqual(self.store.one('SELECT folder FROM jobs WHERE id=?',(self.job,))['folder'],self.store.one('SELECT folder FROM jobs WHERE id=?',(j2,))['folder'])
        snap=self.store.one('SELECT source FROM jobs WHERE id=?',(j2,))['source']
        self.source.write_text('Changed - Song',encoding='utf-8');self.assertIn('Prospa',Path(snap).read_text(encoding='utf-8'))

    def test_crash_resume_search_state(self):
        self.engine.schedule_search();before=self.store.one('SELECT * FROM tracks')
        resumed=Engine(self.store,self.engine.config,self.api)
        resumed.schedule_search();self.assertEqual(len(self.api.searches),1)
        self.assertEqual(before['search_id'],self.store.one('SELECT * FROM tracks')['search_id'])

    def test_legacy_migration_is_idempotent(self):
        self.store.set_setting('legacy_migrated','already')
        self.assertIsNone(self.engine.migrate_legacy())

    def test_audio_without_tags_is_valid_but_zero_duration_is_not(self):
        class Audio(dict):
            info=SimpleNamespace(length=120)
        with patch('playlist_core.mutagen.File',return_value=Audio()):
            Engine.validate_audio(self.source,0)
        with patch('playlist_core.mutagen.File',return_value=SimpleNamespace(info=SimpleNamespace(length=0))):
            with self.assertRaises(ValueError):Engine.validate_audio(self.source,0)

    def test_track_number_preserves_existing_metadata(self):
        class Audio(dict):
            info=SimpleNamespace(length=185,bitrate=320000,sample_rate=44100,bits_per_sample=16)
            @property
            def tags(self):return self
            def save(self):self.saved=True
        audio=Audio(album=['Album'],genre=['House'],artist=['Artist'])
        with patch('playlist_core.mutagen.File',return_value=audio):
            self.assertTrue(write_track_number(self.source,7))
            meta=audio_metadata(self.source)
        self.assertEqual(audio['tracknumber'],['7'])
        self.assertEqual(audio['album'],['Album']);self.assertEqual(audio['genre'],['House']);self.assertEqual(audio['artist'],['Artist'])
        self.assertEqual(meta['length'],185);self.assertEqual(meta['bitrate'],320)

    def test_delivery_hash_is_after_track_tag(self):
        source=self.root/'Tagged.flac';source.write_bytes(b'AUDIO')
        def tag(path,position):
            with Path(path).open('ab') as stream:stream.write(b'TRACK='+str(position).encode())
            return True
        with patch('playlist_core.write_track_number',side_effect=tag):
            self.engine.deliver(self.track,source,digest(source),False)
        saved=self.store.one('SELECT * FROM tracks')
        self.assertEqual(saved['hash'],digest(saved['path']))
        self.assertIn('sıra etiketi: 1',saved['detail'])

    def test_existing_track_tagging_keeps_original_backup(self):
        source=self.root/'Original.flac';source.write_bytes(b'AUDIO')
        self.engine.deliver(self.track,source,digest(source),False)
        before=self.store.one('SELECT * FROM tracks');original=Path(before['path']).read_bytes()
        self.store.set_setting('tracknumber_revision','')
        def tag(path,position):
            with Path(path).open('ab') as stream:stream.write(b'TRACK='+str(position).encode())
            return True
        with patch('playlist_core.write_track_number',side_effect=tag),patch('playlist_core.audio_metadata',return_value={'tracknumber':''}),patch.object(self.engine,'validate_audio',return_value=None):
            self.engine.backfill_track_numbers()
        after=self.store.one('SELECT * FROM tracks')
        backup=self.root/'backups'/'playlist-order-originals'/'list'/'Original.flac'
        self.assertEqual(backup.read_bytes(),original)
        self.assertEqual(after['hash'],digest(after['path']))
        self.assertEqual(self.store.setting('tracknumber_revision'),'playlist-position-1')


if __name__=='__main__':unittest.main(verbosity=2)
