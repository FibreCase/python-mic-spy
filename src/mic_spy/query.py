"""Read-only archive access and playback export for CLI or future web panels."""
import sqlite3
import wave
from pathlib import Path


def connect(root: Path):
    return sqlite3.connect(f'{(root / "archive.sqlite3").resolve().as_uri()}?mode=ro', uri=True)


def search_transcripts(root: Path, text='', limit=50):
    if limit <= 0:
        raise ValueError('limit must be positive')
    db = connect(root)
    try:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            'SELECT * FROM transcripts WHERE instr(text,?)>0 ORDER BY start_ns DESC LIMIT ?',
            (text, limit))]
    finally:
        db.close()


def export_clip(root: Path, transcript_id: int, output: Path):
    root = root.resolve()
    db = connect(root)
    try:
        row = db.execute('SELECT session_id,start_sample,end_sample FROM transcripts WHERE id=?',(transcript_id,)).fetchone()
        if row is None:
            raise ValueError('Transcript not found')
        session,start,end = row
        rate = db.execute('SELECT sample_rate FROM sessions WHERE id=?',(session,)).fetchone()[0]
        files = db.execute('SELECT path,start_sample,end_sample FROM recordings WHERE session_id=? AND start_sample<? AND end_sample>? ORDER BY start_sample',(session,end,start)).fetchall()
    finally:
        db.close()
    chunks = []
    cursor = start
    for path,a,b in files:
        lo,hi = max(start,a),min(end,b)
        if lo != cursor:
            raise ValueError('Audio archive contains a gap')
        with wave.open(str(root/path),'rb') as f:
            f.setpos(lo-a)
            data = f.readframes(hi-lo)
        if len(data) != (hi-lo)*2:
            raise ValueError('Audio file is incomplete')
        chunks.append(data)
        cursor = hi
    if cursor != end:
        raise ValueError('Audio archive is incomplete')
    with wave.open(str(output) if isinstance(output, Path) else output,'wb') as f:
        f.setparams((1,2,rate,0,'NONE','not compressed'))
        for data in chunks:
            f.writeframes(data)
