import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_setup import data_root, defaults, load_settings, save_settings
from playlist_core import Store


class DistributionTests(unittest.TestCase):
    def test_frozen_data_is_not_extraction_directory(self):
        with patch('sys.frozen', True, create=True), patch.dict('os.environ', {'LOCALAPPDATA': 'C:/example-user/Local'}):
            self.assertEqual(data_root(), Path('C:/example-user/Local/SoulseekPlaylists'))

    def test_existing_config_preserves_historical_output_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {'slskd_url': 'http://localhost:5030', 'playlist_output_dir': 'legacy-cli-folder'}
            save_settings(root, original)
            loaded = load_settings(root)
            self.assertEqual(loaded['gui_playlist_output_dir'], str(root / 'Playlists'))
            self.assertEqual(loaded['playlist_output_dir'], 'legacy-cli-folder')
            self.assertEqual(json.loads((root/'config.json').read_text()), original)

    def test_failed_atomic_save_keeps_previous_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_settings(root, {'old': True})
            with patch('app_setup.os.replace', side_effect=OSError):
                with self.assertRaises(OSError):save_settings(root, {'new': True})
            self.assertEqual(json.loads((root/'config.json').read_text()), {'old': True})
            self.assertEqual(list(root.glob('*.tmp')), [])

    def test_output_change_affects_only_new_jobs_and_resume_preserves_old(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            txt = root/'Set.txt'
            txt.write_text('Artist - Title', encoding='utf-8')
            first = Store(root, root/'output-one')
            job = first.add_job(txt, 'artist_title')
            old = first.one('SELECT * FROM jobs WHERE id=?', (job,))['folder']
            first.db.close()
            second = Store(root, root/'output-two')
            second.add_job(txt, 'artist_title')
            self.assertEqual(second.one('SELECT * FROM jobs WHERE id=?', (job,))['folder'], old)
            self.assertEqual(len(second.rows('SELECT * FROM tracks')), 2)
            self.assertTrue((root/'output-two'/'Set').is_dir())
            second.db.close()

    def test_release_allowlist_excludes_personal_files(self):
        from build_release import SOURCE_FILES
        forbidden = {'config.json','ui.ini','songs.txt','state.json','playlists.sqlite3','slskd.yml'}
        self.assertFalse(forbidden.intersection(SOURCE_FILES))
        self.assertTrue(all(Path(name).name == name for name in SOURCE_FILES))

    def test_build_does_not_search_unrelated_host_tools(self):
        from build_release import clean_environment
        with patch.dict('os.environ', {'PATH': 'C:/unrelated/poppler', 'PYTHONPATH':'C:/unrelated/python', 'QT_PLUGIN_PATH':'C:/unrelated/qt'}):
            env = clean_environment()
        self.assertNotIn('unrelated', env['PATH'])
        self.assertNotIn('PYTHONPATH', env)
        self.assertNotIn('QT_PLUGIN_PATH', env)

    def test_frozen_external_process_does_not_inherit_bundle_path(self):
        from slskd_runtime import external_environment
        import os
        with patch('sys._MEIPASS', 'C:/bundle/_internal', create=True), patch.dict('os.environ',
                {'PATH':os.pathsep.join(['C:/bundle/_internal/PySide6','C:/Windows/System32'])}):
            env=external_environment()
        self.assertNotIn('PySide6', env['PATH'])
        self.assertIn('System32', env['PATH'])


if __name__ == '__main__':unittest.main()
