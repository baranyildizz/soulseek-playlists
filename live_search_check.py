"""Download-free search test against the running slskd.

    python live_search_check.py "Artist - Title" ["Artist - Title" ...]

Runs the real engine in preview mode on a temporary database and prints, per
track, the queries used and the best candidates. Nothing is ever downloaded.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

from engine import Engine
from store import Store

ROOT = Path(__file__).resolve().parent


def main(lines):
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='playlist_search_test_') as folder:
        root = Path(folder)
        txt = root / 'Arama testi.txt'
        txt.write_text('\n'.join(lines or ["Prospa - Don't Stop"]) + '\n', encoding='utf-8')
        store = Store(root)
        job = store.add_job(txt, 'auto', 'preview')
        store.update('jobs', job, status='active')
        try:
            engine = Engine(store, config)
            deadline = time.time() + 120 + 90 * len(lines)
            while time.time() < deadline:
                engine.tick()
                if store.one('SELECT status FROM jobs WHERE id=?', (job,))['status'] == 'preview_complete':
                    break
                time.sleep(0.5)
            assert not store.rows('SELECT id FROM attempts'), 'Preview queued a download!'
            for t in store.rows('SELECT * FROM tracks ORDER BY position'):
                print(f"\n{t['artist']} — {t['title']}  [{t['status']}]")
                for e in store.rows("SELECT message FROM events WHERE message LIKE ? ORDER BY id",
                                    (f"{t['artist']} — {t['title']}:%",)):
                    print('   ', e['message'].split(': ', 1)[1])
                for c in store.rows('SELECT * FROM candidates WHERE track=? ORDER BY eligible DESC, score DESC LIMIT 3',
                                    (t['id'],)):
                    data = json.loads(c['data'])
                    mark = 'OTOMATİK' if c['eligible'] else 'inceleme'
                    print(f"    {mark:8} {c['score']:5.1f} {data['extension']:4} {c['peer'][:14]:14} …{c['filename'][-70:]}")
            print('\nconnection:', store.setting('connection', ''))
        finally:
            store.db.close()


if __name__ == '__main__':
    main(sys.argv[1:])
