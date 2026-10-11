import shutil
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from mic_spy.transport import udp_url, collector_command, FFmpegUdpSource, terminate, transmit


def test_transport_options_and_validation():
    assert 'pkt_size=1316' in udp_url('udp://host:5000')
    url = udp_url('udp://0.0.0.0:5000', receive=True, timeout=2)
    assert 'timeout=2000000' in url
    assert 'overrun_nonfatal=0' in url
    command = collector_command('plughw:0,0', 'udp://host:5000')
    assert command[command.index('-i')+1] == 'plughw:0,0'
    assert 'libopus' in command and 'mpegts' in command
    for value in ('http://host:5000', 'udp://host', 'udp://host:0', 'udp://host:5000/path'):
        with pytest.raises(ValueError):
            udp_url(value)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg not installed')
def test_real_opus_udp_decodes_pcm_and_stops():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    source = FFmpegUdpSource(udp_url(f'udp://127.0.0.1:{port}', receive=True, timeout=3))
    sender = subprocess.Popen([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-re',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000',
        '-ac', '1', '-c:a', 'libopus', '-b:a', '24k', '-flush_packets', '1',
        '-f', 'mpegts', udp_url(f'udp://127.0.0.1:{port}')])
    try:
        with ThreadPoolExecutor() as executor:
            data = executor.submit(source.read).result(timeout=12)
        assert len(data) == 3200
        assert any(data)
    finally:
        terminate(sender)
        source.close()
    assert source.process.poll() is not None


def test_collector_stop_terminates_child(monkeypatch):
    class Process:
        done = False
        def poll(self):
            return 0 if self.done else None
        def terminate(self):
            self.done = True
        def wait(self, timeout=None):
            return 0
    process = Process()
    monkeypatch.setattr(subprocess, 'Popen', lambda command: process)
    stop = threading.Event()
    stop.set()
    transmit('plughw:0,0', 'udp://host:5000', '24000', stop)
    assert process.done
