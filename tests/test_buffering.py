import sqlite3
import wave
from mic_spy.storage import Archive
from mic_spy.query import search_transcripts, export_clip


def test_audio_and_text_stay_in_memory_until_interval(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('mic_spy.storage.time.monotonic', lambda: clock[0])
    archive = Archive(tmp_path, rate=10, segment_seconds=300)
    archive.append(b'\1\0' * 10)
    archive.transcript(0, 10, '缓存', 'endpoint')
    observer = sqlite3.connect(tmp_path / 'archive.sqlite3')
    changes = archive.db.total_changes
    clock[0] = 159.9
    archive.maintenance()
    assert not (tmp_path / 'audio').exists()
    assert observer.execute('SELECT count(*) FROM transcripts').fetchone()[0] == 0
    assert archive.db.total_changes == changes
    clock[0] = 160.0
    archive.maintenance()
    assert observer.execute('SELECT count(*) FROM transcripts').fetchone()[0] == 1
    assert archive.position == archive.disk_position == 10
    assert not archive.pending_pcm
    archive.append(b'\2\0' * 5)
    archive.transcript(10, 15, '停止保存', 'shutdown')
    archive.close()
    rows = search_transcripts(tmp_path)
    assert len(rows) == 2
    export_clip(tmp_path, rows[0]['id'], tmp_path / 'clip.wav')
    with wave.open(str(tmp_path / 'clip.wav')) as clip:
        assert clip.readframes(5) == b'\2\0' * 5
    observer.close()


def test_failed_flush_is_not_replayed(tmp_path, monkeypatch):
    import pytest
    archive = Archive(tmp_path, rate=10)
    archive.append(b'\0\0' * 10)
    def fail(_):
        raise OSError('disk full')
    monkeypatch.setattr(archive, '_write_pcm', fail)
    with pytest.raises(OSError):
        archive.flush()
    assert archive.flush_failed
    archive.close('failed')
