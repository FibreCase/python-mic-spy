from pathlib import Path
from mic_spy.storage import Archive
from mic_spy.retention import DAY_NS, prune_archive
from mic_spy.query import search_transcripts, export_clip


def test_long_running_session_prunes_old_segments_and_crossing_text(tmp_path):
    archive = Archive(tmp_path, rate=10, segment_seconds=1)
    archive.append(b'\0\0' * 25)  # two closed segments and one active
    archive.transcript(0, 5, '旧文字', 'endpoint')
    archive.transcript(8, 13, '跨边界', 'endpoint')
    archive.transcript(15, 23, '保留', 'endpoint')
    archive.flush()
    now = archive.started + 7 * DAY_NS + 1_500_000_000
    assert prune_archive(archive.db, tmp_path, 7, now) == 1
    assert [row['text'] for row in search_transcripts(tmp_path)] == ['保留']
    paths = archive.db.execute('SELECT path FROM recordings').fetchall()
    assert len(paths) == 2
    assert all((tmp_path / row[0]).exists() for row in paths)
    row = search_transcripts(tmp_path)[0]
    export_clip(tmp_path, row['id'], tmp_path/'clip.wav')
    # Even far in the future, the current open file must survive.
    prune_archive(archive.db, tmp_path, 7, now + DAY_NS)
    assert archive.db.execute('SELECT closed FROM recordings').fetchall() == [(0,)]
    assert archive.db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 1
    archive.close()


def test_end_time_boundary_and_empty_session_removal(tmp_path):
    archive = Archive(tmp_path, rate=10, segment_seconds=1)
    archive.append(b'\0\0' * 10)
    started = archive.started
    session = archive.session
    archive.close()
    current = Archive(tmp_path)
    # Exactly seven days old is retained; older than seven days is deleted.
    assert prune_archive(current.db, tmp_path, 7, started + 7*DAY_NS + 10**9) == 0
    assert prune_archive(current.db, tmp_path, 7, started + 7*DAY_NS + 10**9 + 1) == 1
    assert not (tmp_path/'audio'/session).exists()
    assert current.db.execute('SELECT id FROM sessions').fetchall() == [(current.session,)]
    current.close()


def test_missing_file_can_be_retried_and_permission_failure_keeps_metadata(tmp_path, monkeypatch):
    archive = Archive(tmp_path, rate=10, segment_seconds=1)
    archive.append(b'\0\0' * 20)
    archive.flush()
    paths = [tmp_path / row[0] for row in archive.db.execute('SELECT path FROM recordings')]
    paths[0].unlink()
    original = Path.unlink
    def unlink(path, **kwargs):
        if path == paths[1]:
            raise PermissionError('read-only')
        return original(path, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    assert prune_archive(archive.db, tmp_path, 7, archive.started + 8*DAY_NS) == 1
    assert archive.db.execute('SELECT count(*) FROM recordings').fetchone()[0] == 1
    assert paths[1].exists()
    archive.close()
