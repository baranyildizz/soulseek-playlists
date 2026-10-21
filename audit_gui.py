"""Read-only audit of completed files and durable transfer outcomes."""
import json
import sqlite3
import sys
from pathlib import Path
from audio import digest, validate_audio

root=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).parent
db=sqlite3.connect((root/'playlists.sqlite3').as_uri()+'?mode=ro',uri=True)
db.row_factory=sqlite3.Row
tracks=[dict(r) for r in db.execute("SELECT * FROM tracks WHERE status='done'")]
problems=[]
for t in tracks:
    path=Path(t['path'])
    try:
        validate_audio(path,0)
        if digest(path)!=t['hash']:problems.append({'title':t['title'],'problem':'hash mismatch'})
    except Exception as e:
        problems.append({'title':t['title'],'problem':type(e).__name__})
jobs=[]
for j in db.execute('SELECT * FROM jobs'):
    folder=Path(j['folder'])
    playlists=list(folder.glob('*.m3u8'))
    entries=[]
    for p in playlists:entries.extend(line for line in p.read_text(encoding='utf-8-sig').splitlines() if line and not line.startswith('#'))
    jobs.append({'name':j['name'],'status':j['status'],'folder':str(folder),'audio_files':len([p for p in folder.iterdir() if p.suffix.lower() in ('.flac','.mp3')]),'playlist_entries':len(entries)})
print(json.dumps({'verified_completed':len(tracks),'problems':problems,'jobs':jobs,
    'attempt_outcomes':[dict(r) for r in db.execute('SELECT status,count(*) AS count FROM attempts GROUP BY status')],
    'cancel_requests':db.execute('SELECT count(*) FROM attempts WHERE cancel_requested=1').fetchone()[0],
    'pending_details':[dict(r) for r in db.execute("SELECT artist,title,status,detail FROM tracks WHERE status IN ('ready','waiting')")]
    },ensure_ascii=True,indent=2))
db.close()
raise SystemExit(bool(problems))
