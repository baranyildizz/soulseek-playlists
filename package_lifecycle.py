"""Drive the real first-run/save/restart flow using an explicit isolated test root."""
import json
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def attach(app, root, phase, output):
    from app_setup import SetupDialog, load_settings
    root, output = Path(root), Path(output)
    timer = QTimer(app)
    timer.setInterval(100)
    state = {'started': time.monotonic(), 'setup_seen': False, 'import_sent': False}

    def finish(passed, reason, window=None):
        timer.stop()
        output.write_text(json.dumps({'passed': passed, 'phase': phase,
            'setup_seen': state['setup_seen'], 'reason': reason}, indent=2), encoding='utf-8')
        if window is not None:
            window.quit_app()
        else:
            app.exit(1)

    def step():
        window = None
        try:
            widgets = QApplication.topLevelWidgets()
            setup = next((w for w in widgets if isinstance(w, SetupDialog) and w.isVisible()), None)
            # The entry-point class belongs to __main__, whereas a normal import
            # would create a second playlist_app.Window class in a frozen build.
            window = next((w for w in widgets if hasattr(w, 'worker') and hasattr(w, 'data') and w.isVisible()), None)
            if time.monotonic()-state['started'] > 25:
                finish(False, 'startup timeout', window)
                return
            if setup is not None:
                state['setup_seen'] = True
                if phase != 'first':
                    setup.reject()
                    finish(False, 'setup unexpectedly reopened')
                    return
                for key, value in {
                    'slskd_url': 'http://127.0.0.1:9', 'slskd_executable': '',
                    'slskd_config_path': '', 'slskd_download_dir': str(root/'incoming'),
                    'gui_playlist_output_dir': str(root/'chosen-output'),
                }.items():
                    setup.fields[key].setText(value)
                setup.save()
                return
            if window is None:
                return
            jobs = window.data.get('jobs', [])
            if phase == 'first' and not state['import_sent']:
                source = root/'Fixture.txt'
                source.write_text('Fixture Artist - Fixture Track\n', encoding='utf-8')
                window.worker.commands.put(('import', {'source':str(source), 'layout':'artist_title', 'mode':'preview'}))
                state['import_sent'] = True
                return
            if len(jobs) != 1:
                return
            config = load_settings(root)
            assert config['setup_complete']
            assert Path(jobs[0]['folder']).parent == root/'chosen-output'
            assert len(window.data['tracks']) == 1
            assert jobs[0]['status'] == 'paused'
            if phase == 'first':
                assert state['setup_seen']
                if not window.dark:
                    window.toggle_theme()
            else:
                assert not state['setup_seen']
                assert window.dark
            finish(True, 'first-run saved' if phase == 'first' else 'settings, theme and playlist restored', window)
        except Exception as exc:
            finish(False, type(exc).__name__, window)

    timer.timeout.connect(step)
    timer.start()
    return timer
