from datetime import datetime, timezone
from fastapi.testclient import TestClient
from mic_spy.api import create_app
from mic_spy.storage import Archive


def archive_client(tmp_path):
    archive = Archive(tmp_path, rate=10, segment_seconds=1)
    archive.append(b'\1\0' * 25)
    archive.transcript(8, 23, '测试跨文件', 'endpoint')
    archive.transcript(0, 5, '另一条', 'endpoint')
    archive.close()
    return TestClient(create_app(tmp_path))


def test_search_filter_page_and_stats(tmp_path):
    client = archive_client(tmp_path)
    result = client.get('/api/transcripts', params={'text': '测试', 'limit': 1}).json()
    assert result['total'] == 1
    row = result['items'][0]
    assert row['text'] == '测试跨文件'
    assert row['duration_seconds'] == 1.5
    assert isinstance(row['start_ns'], str)
    assert client.get('/api/transcripts', params={'start': row['start_time'], 'end': row['end_time']}).json()['total'] == 1
    assert client.get('/api/transcripts?limit=0').status_code == 422
    assert client.get('/api/transcripts?start=2026-01-01T00:00:00').status_code == 422
    assert client.get('/api/transcripts', params={'start': row['end_time'], 'end': row['start_time']}).status_code == 422
    assert len(client.get('/api/transcripts?limit=1&offset=1').json()['items']) == 1
    stats = client.get('/api/stats').json()
    assert stats['transcripts'] == 2 and stats['audio_seconds'] == 2.5


def test_audio_range_and_missing_file(tmp_path):
    client = archive_client(tmp_path)
    row = client.get('/api/transcripts').json()['items'][0]
    response = client.get(row['audio_url'])
    assert response.status_code == 200
    assert response.content[:4] == b'RIFF'
    assert response.content[44:] == b'\1\0' * 15
    part = client.get(row['audio_url'], headers={'Range': 'bytes=44-53'})
    assert part.status_code == 206 and part.content == response.content[44:54]
    assert client.get(row['audio_url'], headers={'Range': 'bytes=99999-'}).status_code == 416
    assert client.get('/api/transcripts/9999/audio').status_code == 404
    for path in (tmp_path/'audio').rglob('*.wav'):
        path.unlink()
    assert client.get(row['audio_url']).status_code == 410


def test_empty_archive_and_static_ui(tmp_path):
    dist = tmp_path/'web'
    dist.mkdir()
    (dist/'index.html').write_text('<html>Mic Spy</html>')
    client = TestClient(create_app(tmp_path/'absent',dist))
    assert client.get('/api/health').json()['archive_available'] is False
    assert client.get('/api/transcripts').status_code == 503
    assert client.get('/').status_code == 200
    assert client.get('/api/unknown').status_code == 404
    assert not (tmp_path/'absent').exists()
