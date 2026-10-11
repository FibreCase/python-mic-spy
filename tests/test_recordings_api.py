from datetime import datetime, timezone
from fastapi.testclient import TestClient
from mic_spy.api import create_app
from mic_spy.storage import Archive


def setup(tmp_path):
    archive = Archive(tmp_path, rate=10, segment_seconds=1)
    archive.append(b'\1\0'*25)
    archive.close()
    return TestClient(create_app(tmp_path))


def test_silent_recordings_and_arbitrary_time_without_text(tmp_path):
    client = setup(tmp_path)
    assert client.get('/api/transcripts').json()['total'] == 0
    result = client.get('/api/recordings').json()
    assert result['total'] == 3
    first = result['items'][-1]
    assert 'path' not in first
    at = datetime.fromtimestamp((int(first['start_ns'])+500_000_000)/1e9,timezone.utc).isoformat()
    match = client.get('/api/recordings',params={'at':at}).json()['items']
    assert len(match) == 1 and match[0]['id'] == first['id']
    assert abs(match[0]['seek_seconds']-0.5) < 0.00001
    boundary = client.get('/api/recordings',params={'at':first['end_time']}).json()['items']
    assert len(boundary) == 1 and boundary[0]['id'] != first['id']
    assert client.get('/api/recordings',params={'at':'2000-01-01T00:00:00Z'}).json()['total'] == 0
    filtered = client.get('/api/recordings',params={'start':first['start_time'],'end':first['end_time']}).json()
    assert filtered['total'] == 1
    assert len(client.get('/api/recordings?limit=1&offset=1').json()['items']) == 1
    assert client.get('/api/recordings?at=2026-01-01T00:00:00').status_code == 422


def test_recording_audio_range_and_deleted_file(tmp_path):
    client = setup(tmp_path)
    row = client.get('/api/recordings').json()['items'][-1]
    full = client.get(row['audio_url'])
    assert full.status_code == 200
    assert full.content[:4] == b'RIFF'
    assert full.content[44:] == b'\1\0'*10
    part = client.get(row['audio_url'],headers={'Range':'bytes=44-53'})
    assert part.status_code == 206 and part.content == full.content[44:54]
    for path in (tmp_path/'audio').rglob('*.wav'):
        path.unlink()
    assert client.get(row['audio_url']).status_code == 410
    assert client.get('/api/recordings/999/audio').status_code == 404


def test_open_segment_is_available_after_batch_save(tmp_path):
    archive = Archive(tmp_path, rate=10, segment_seconds=300)
    archive.append(b'\0\0'*10)
    client = TestClient(create_app(tmp_path))
    assert client.get('/api/recordings').json()['items'] == []
    archive.flush()
    item = client.get('/api/recordings').json()['items'][0]
    assert item['closed'] is False
    assert client.get(item['audio_url']).content[44:] == b'\0\0'*10
    archive.close()
