"""FFmpeg Opus/MPEG-TS transport over UDP."""
import subprocess
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit


def udp_url(value: str, *, receive=False, timeout=15):
    parsed = urlsplit(value)
    if parsed.scheme != 'udp' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Expected udp://host:port')
    if parsed.port is None or not 1 <= parsed.port <= 65535 or parsed.path not in ('', '/') or parsed.fragment:
        raise ValueError('Expected udp://host:port with a valid port')
    options = dict(parse_qsl(parsed.query))
    if receive:
        options.setdefault('fifo_size', '8192')
        # Treat overflow as failure rather than silently continuing across a gap.
        options.setdefault('overrun_nonfatal', '0')
        options['timeout'] = str(int(timeout * 1_000_000))
    else:
        options.setdefault('pkt_size', '1316')
    return urlunsplit((parsed.scheme, parsed.netloc, '', urlencode(options), ''))


def collector_command(device, target, bitrate='24k', sample_rate=48000):
    return ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin',
            '-f', 'alsa', '-sample_rate', str(sample_rate), '-channels', '1', '-i', device,
            '-c:a', 'libopus', '-application', 'voip', '-b:a', bitrate,
            '-frame_duration', '20', '-flush_packets', '1', '-f', 'mpegts', target]


def receiver_command(listen, sample_rate=48000):
    return ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin',
            '-f', 'mpegts', '-i', listen, '-map', '0:a:0',
            '-ac', '1', '-ar', str(sample_rate), '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1']


def terminate(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def transmit(device, target, bitrate, stop, sample_rate=48000):
    """Run one sender attempt; the CLI supervises restarts."""
    process = subprocess.Popen(collector_command(device, target, bitrate, sample_rate))
    try:
        while not stop.wait(0.2):
            code = process.poll()
            if code is not None:
                raise RuntimeError(f'FFmpeg collector exited with code {code}')
    finally:
        terminate(process)


class FFmpegUdpSource:
    def __init__(self, listen, sample_rate=48000):
        self.sample_rate = sample_rate
        self.process = subprocess.Popen(receiver_command(listen, sample_rate), stdout=subprocess.PIPE)

    def read(self):
        return self.process.stdout.read(self.sample_rate // 10 * 2)

    def close(self):
        terminate(self.process)
        self.process.stdout.close()
