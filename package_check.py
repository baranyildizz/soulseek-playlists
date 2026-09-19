"""Network-free checks run inside the frozen executable, against temporary data only."""
import json
import tempfile
from pathlib import Path


def delivery_check(sample, output):
    """Verify packaged audio parsing/tagging/delivery on a temporary copy."""
    import shutil
    from playlist_core import Store, Engine, digest, audio_metadata
    result = {'passed': False}
    store = None
    try:
        original = Path(sample)
        original_hash = digest(original)
        metadata = audio_metadata(original)
        with tempfile.TemporaryDirectory(prefix='playlist-delivery-check-') as directory:
            root = Path(directory)
            source = root/'Fixture.txt'
            source.write_text('Fixture Artist - Fixture Title\n', encoding='utf-8')
            store = Store(root, root/'chosen-output')
            job = store.add_job(source, 'artist_title')
            track = store.one('SELECT * FROM tracks WHERE job=?', (job,))
            incoming = root/original.name
            shutil.copy2(original, incoming)
            engine = Engine(store, {'slskd_url':'http://127.0.0.1:9'}, api=object())
            engine.deliver(track, incoming, digest(incoming), False)
            finished = store.one('SELECT * FROM tracks WHERE id=?', (track['id'],))
            target = Path(finished['path'])
            saved = audio_metadata(target)
            assert finished['status'] == 'done'
            assert target.parent == root/'chosen-output'/'Fixture'
            assert target.name == original.name
            assert finished['hash'] == digest(target)
            assert saved['tracknumber'] == '1'
            assert saved['album'] == metadata['album'] and saved['genre'] == metadata['genre']
            assert (target.parent/'Fixture.m3u8').is_file()
            assert original_hash == digest(original)
            store.db.close()
            store = None
            result = {'passed': True, 'checks':['real audio parsing', 'flat playlist delivery',
                'filename preserved', 'tracknumber tag', 'album/genre preserved',
                'final hash', 'm3u8 export', 'original untouched']}
    except Exception as exc:
        result = {'passed': False, 'error_type':type(exc).__name__}
    finally:
        if store is not None:
            store.db.close()
    Path(output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if result['passed'] else 1


def connection_check(config_path, output):
    """Read-only real API probe. Never serialize credentials or response bodies."""
    from playlist_core import Api
    import logging
    api = None
    result = {'passed': False, 'authenticated': False, 'connected': False}
    try:
        config = json.loads(Path(config_path).read_text(encoding='utf-8'))
        config['prompt_for_web_credentials'] = False
        api = Api(config, logging.getLogger('package-connection'))
        api.authenticate()
        result['authenticated'] = True
        result['connected'] = api.server_connected(api.server())
        result['passed'] = result['connected']
    except Exception as exc:
        result['error_type'] = type(exc).__name__
    finally:
        if api is not None:
            api.session.close()
    Path(output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if result['passed'] else 1


def run(app, result_path):
    from app_setup import defaults, save_settings, load_settings, SetupDialog
    from playlist_app import Window
    from playlist_core import Store, Engine, Candidate
    from dataclasses import asdict
    import time
    result = {'passed': False}
    try:
        with tempfile.TemporaryDirectory(prefix='playlist-package-check-') as directory:
            root = Path(directory)
            config = defaults(root)
            config['gui_playlist_output_dir'] = str(root / 'separate-output')
            config['setup_complete'] = True
            save_settings(root, config)
            assert load_settings(root) == config
            setup = SetupDialog(root, load_settings(root))
            assert setup.fields['gui_playlist_output_dir'].text() == config['gui_playlist_output_dir']
            setup.close()
            source = root / 'Test.txt'
            source.write_text('Test Artist - Test Song\n', encoding='utf-8')
            store = Store(root, config['gui_playlist_output_dir'])
            job = store.add_job(source, 'artist_title')
            store.update('jobs', job, status='active')
            track = store.one('SELECT * FROM tracks')
            assert Path(store.one('SELECT folder FROM jobs')['folder']).parent == Path(config['gui_playlist_output_dir'])
            candidate = Candidate('test-peer', r'Test Artist\Test Song.flac', 8000000, None, 'flac', 0, 1, True, score=100, eligible=True)
            store.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?)',
                ('c',track['id'],candidate.peer,candidate.filename,json.dumps(asdict(candidate)),100,1,time.time()))
            engine = Engine(store, config, api=object())
            engine.command('defer_track', track['id'])
            store.db.close()
            reopened = Store(root, config['gui_playlist_output_dir'])
            assert reopened.one('SELECT * FROM tracks')['status'] == 'deferred'
            assert reopened.one('SELECT * FROM candidates')['peer'] == 'test-peer'
            window = Window(root)
            window.receive(reopened.snapshot())
            window.show()
            app.processEvents()
            assert window.table.rowCount() == 1
            assert not window.windowIcon().isNull()
            # Exercise the frozen external-process boundary without starting slskd.
            from slskd_runtime import external_library_search, external_environment
            import subprocess
            with external_library_search():
                child = subprocess.run(['cmd.exe', '/d', '/c', 'exit', '0'],
                    env=external_environment(), creationflags=subprocess.CREATE_NO_WINDOW,
                    capture_output=True, timeout=10)
            assert child.returncode == 0
            window.quitting = True
            window.tray.hide()
            window.close()
            reopened.db.close()
            result = {'passed': True, 'checks': ['settings roundtrip', 'first-run dialog',
                'separate output directory', 'playlist import', 'restart resume', 'candidate persistence',
                'Qt window', 'external process launch']}
    except Exception as exc:
        result = {'passed': False, 'error_type': type(exc).__name__}
    Path(result_path).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if result['passed'] else 1
