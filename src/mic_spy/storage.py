"""Sample-indexed audio archive; each capture restart gets a new timeline."""
import os
import sqlite3
import time
import uuid
import wave
from pathlib import Path
from .retention import prune_archive

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, started_ns INTEGER NOT NULL, sample_rate INTEGER NOT NULL,
 ended_ns INTEGER, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recordings (
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 path TEXT NOT NULL UNIQUE, start_sample INTEGER NOT NULL, end_sample INTEGER NOT NULL,
 closed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS transcripts (
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 start_sample INTEGER NOT NULL, end_sample INTEGER NOT NULL,
 start_ns INTEGER NOT NULL, end_ns INTEGER NOT NULL, text TEXT NOT NULL,
 final_reason TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS transcript_time ON transcripts(start_ns);
CREATE INDEX IF NOT EXISTS recording_samples ON recordings(session_id,start_sample,end_sample);
"""

class Archive:
    def __init__(self, root: Path, rate=16000, segment_seconds=300, retention_days=7, flush_seconds=60):
        if flush_seconds <= 0 or rate <= 0 or segment_seconds <= 0:
            raise ValueError('Flush interval, sample rate and segment duration must be positive')
        self.flush_seconds = flush_seconds
        self.pending_pcm = bytearray()
        self.pending_transcripts = []
        self.flush_failed = False
        self.next_flush = time.monotonic() + flush_seconds
        if retention_days <= 0:
            raise ValueError('retention_days must be positive')
        self.retention_days = retention_days
        self.next_cleanup = 0.0
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'archive.sqlite3')
        self.db.executescript(SCHEMA)
        # WAV headers are updated after every batch. Recover an interrupted segment.
        for rid, path, start in self.db.execute('SELECT id,path,start_sample FROM recordings WHERE closed=0').fetchall():
            with wave.open(str(self.root / path), 'rb') as f:
                count = f.getnframes()
            self.db.execute('UPDATE recordings SET end_sample=?,closed=1 WHERE id=?', (start+count,rid))
        self.db.execute("UPDATE sessions SET status='interrupted' WHERE status='running'")
        self.db.commit()
        prune_archive(self.db, self.root, self.retention_days, time.time_ns())
        self.next_cleanup = time.monotonic() + 3600
        self.rate, self.limit = rate, int(rate * segment_seconds)
        self.session = uuid.uuid4().hex
        self.started = time.time_ns()
        self.position = 0
        self.disk_position = 0
        self.wav = None
        self.db.execute('INSERT INTO sessions VALUES (?,?,?,NULL,?)', (self.session,self.started,rate,'running'))
        self.db.commit()

    def maintenance(self):
        if time.monotonic() >= self.next_flush:
            self.flush()
        if time.monotonic() >= self.next_cleanup:
            prune_archive(self.db, self.root, self.retention_days, time.time_ns())
            self.next_cleanup = time.monotonic() + 3600

    def anchor_time(self):
        """Anchor decoded network audio to receipt time (not sender capture time)."""
        if self.position:
            raise ValueError('Cannot change time after recording audio')
        self.started = time.time_ns()
        # Persist the updated anchor in the next scheduled batch.

    def append(self, pcm: bytes):
        if len(pcm) % 2:
            raise ValueError('PCM must contain complete int16 samples')
        self.pending_pcm.extend(pcm)
        self.position += len(pcm) // 2

    def flush(self):
        """Persist one audio/text batch. Called by maintenance or normal shutdown."""
        if self.flush_failed:
            raise RuntimeError('Previous archive flush failed')
        if not self.pending_pcm and not self.pending_transcripts:
            self.next_flush = time.monotonic() + self.flush_seconds
            return
        try:
            self._write_pcm(bytes(self.pending_pcm))
            if self.wav:
                self.wav._file.flush()
                os.fsync(self.wav._file.fileno())
            self.db.execute('UPDATE sessions SET started_ns=? WHERE id=?', (self.started, self.session))
            self.db.executemany(
                'INSERT INTO transcripts(session_id,start_sample,end_sample,start_ns,end_ns,text,final_reason) VALUES (?,?,?,?,?,?,?)',
                [(self.session,start,end,self.started+start*10**9//self.rate,
                  self.started+end*10**9//self.rate,text,reason)
                 for start,end,text,reason in self.pending_transcripts])
            self.db.commit()
        except BaseException:
            self.flush_failed = True
            self.db.rollback()
            raise
        self.pending_pcm.clear()
        self.pending_transcripts.clear()
        self.next_flush = time.monotonic() + self.flush_seconds

    def _write_pcm(self, pcm: bytes):
        while pcm:
            if self.wav is None:
                path = Path('audio') / self.session / f'{self.disk_position:012d}.wav'
                (self.root / path).parent.mkdir(parents=True,exist_ok=True)
                self.wav = wave.open(str(self.root / path),'wb')
                self.wav.setparams((1,2,self.rate,0,'NONE','not compressed'))
                self.wav.writeframes(b'')
                self.segment_start = self.disk_position
                self.rid = self.db.execute('INSERT INTO recordings(session_id,path,start_sample,end_sample) VALUES (?,?,?,?)', (self.session,str(path),self.disk_position,self.disk_position)).lastrowid
            count = min(len(pcm)//2,self.limit-(self.disk_position-self.segment_start))
            self.wav.writeframes(pcm[:count*2])
            pcm = pcm[count*2:]
            self.disk_position += count
            self.db.execute('UPDATE recordings SET end_sample=? WHERE id=?',(self.disk_position,self.rid))
            if self.disk_position-self.segment_start == self.limit:
                self.close_segment()

    def close_segment(self):
        if self.wav:
            self.wav._file.flush()
            os.fsync(self.wav._file.fileno())
            self.wav.close()
            self.wav = None
            self.db.execute('UPDATE recordings SET closed=1 WHERE id=?',(self.rid,))

    def transcript(self,start,end,text,reason):
        if text.strip() and end > start:
            self.pending_transcripts.append((start,end,text.strip(),reason))

    def close(self,status='stopped'):
        try:
            if not self.flush_failed:
                self.flush()
                self.close_segment()
                self.db.execute('UPDATE sessions SET ended_ns=?,status=? WHERE id=?',
                                (self.started+self.disk_position*10**9//self.rate,status,self.session))
                self.db.commit()
            elif self.wav:
                self.wav.close()
                self.wav = None
        finally:
            if self.wav:
                self.wav.close()
                self.wav = None
            self.db.close()
