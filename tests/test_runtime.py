import threading
import pytest
from mic_spy.contracts import Transcript
from mic_spy.runtime import run_session
from mic_spy.storage import Archive
from mic_spy.query import search_transcripts


class Source:
    sample_rate = 10
    closed = False
    def __init__(self):
        self.chunks = iter([b'\0\0' * 10])
    def read(self):
        return next(self.chunks, b'')
    def close(self):
        self.closed = True


class Recognizer:
    def accept(self, pcm):
        return [Transcript(0, len(pcm)//2, '独立组件', 'endpoint')]
    def finish(self, reason):
        self.reason = reason
        return []


def test_components_connect_and_cleanup_on_source_end(tmp_path):
    source, recognizer = Source(), Recognizer()
    store = Archive(tmp_path, rate=10, segment_seconds=1)
    with pytest.raises(RuntimeError, match='Audio source stopped'):
        run_session(source, recognizer, store, threading.Event())
    assert source.closed
    assert recognizer.reason == 'capture_error'
    rows = search_transcripts(tmp_path, '组件')
    assert len(rows) == 1
    assert rows[0]['end_sample'] == 10


def test_query_does_not_create_database(tmp_path):
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        search_transcripts(tmp_path)
    assert not (tmp_path / 'archive.sqlite3').exists()
