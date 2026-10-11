import struct
import wave
from mic_spy.storage import Archive
from mic_spy.query import export_clip


def test_cross_segment_replay_and_time(tmp_path):
    archive = Archive(tmp_path,rate=10,segment_seconds=1)
    samples = struct.pack('<25h',*range(25))
    archive.append(samples)
    archive.transcript(8,23,'测试','endpoint')
    archive.flush()
    row = archive.db.execute('SELECT id,start_ns,end_ns FROM transcripts').fetchone()
    assert row[1] == archive.started + 800_000_000
    assert row[2] == archive.started + 2_300_000_000
    archive.close()
    export_clip(tmp_path,row[0],tmp_path/'clip.wav')
    with wave.open(str(tmp_path/'clip.wav')) as clip:
        assert clip.getframerate() == 10
        assert clip.readframes(15) == samples[16:46]


def test_recover_interrupted_segment(tmp_path):
    archive = Archive(tmp_path,rate=10,segment_seconds=1)
    archive.append(b'\0\0'*7)
    archive.flush()
    archive.wav.close()
    archive.db.close()
    restarted = Archive(tmp_path,rate=10,segment_seconds=1)
    assert restarted.db.execute('SELECT end_sample,closed FROM recordings').fetchone() == (7,1)
    assert restarted.db.execute('SELECT status FROM sessions WHERE id=?',(archive.session,)).fetchone()[0] == 'interrupted'
    restarted.close()
