"""Delete expired closed audio segments and their associated transcript rows."""
import logging
from pathlib import Path

log = logging.getLogger('mic-spy')
DAY_NS = 86_400 * 1_000_000_000


def prune_archive(db, root: Path, retention_days: float, now_ns: int):
    cutoff = now_ns - int(retention_days * DAY_NS)
    root = root.resolve()
    # Compare segment end time, so segments straddling the cutoff stay playable.
    rows = db.execute('''
        SELECT r.id,r.path,r.session_id,r.start_sample,r.end_sample
        FROM recordings r JOIN sessions s ON s.id=r.session_id
        WHERE r.closed=1
          AND s.started_ns + (r.end_sample * 1000000000 / s.sample_rate) < ?
    ''', (cutoff,)).fetchall()
    removed = 0
    for rid, relative, session, start, end in rows:
        path = (root / relative).resolve()
        if not path.is_relative_to(root / 'audio'):
            log.error('Refusing to remove audio outside archive: %s', relative)
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.exception('Could not remove expired recording: %s', relative)
            continue
        # File first: after interruption a missing file can be retried next time.
        # Remove overlapping transcripts, including a sentence crossing a segment boundary.
        with db:
            db.execute('DELETE FROM transcripts WHERE session_id=? AND start_sample<? AND end_sample>?',
                       (session, end, start))
            db.execute('DELETE FROM recordings WHERE id=?', (rid,))
        removed += 1
        try:
            path.parent.rmdir()  # Only removes an empty session directory.
        except OSError:
            pass
    with db:
        db.execute('''DELETE FROM sessions WHERE status != 'running' AND started_ns < ?
            AND NOT EXISTS (SELECT 1 FROM recordings WHERE session_id=sessions.id)
            AND NOT EXISTS (SELECT 1 FROM transcripts WHERE session_id=sessions.id)''', (cutoff,))
    if removed:
        log.info('Retention cleanup removed %s audio segments (keep %s days)', removed, retention_days)
    return removed
