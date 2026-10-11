import argparse
import fcntl
import json
import logging
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from .storage import Archive
from .source import AlsaSource
from .recognition import SherpaRecognizer
from .runtime import run_session
from .query import export_clip, search_transcripts
from .transport import FFmpegUdpSource, transmit, udp_url, terminate

log = logging.getLogger('mic-spy')


def main():
    parser = argparse.ArgumentParser(description='Continuous ALSA recording and Chinese streaming ASR')
    parser.add_argument('--data',type=Path,default=Path('data'))
    commands = parser.add_subparsers(dest='command',required=True)
    collector = commands.add_parser('collector', help='Capture ALSA and send Opus over UDP')
    collector.add_argument('--device', default='plughw:0,0')
    collector.add_argument('--target', required=True, help='udp://processor-host:5000')
    collector.add_argument('--bitrate', type=int, default=24000, help='Opus bits per second')
    processor = commands.add_parser('processor', help='Receive UDP, recognize and archive')
    processor.add_argument('--listen', default='udp://0.0.0.0:5000')
    processor.add_argument('--receive-timeout', type=float, default=15, help='Seconds without packets before restart')
    run = commands.add_parser('run', help='Local ALSA recognition and archive')
    full = commands.add_parser('full', help='Recording, recognition, storage and web in one command')
    full.add_argument('--source', choices=('local', 'udp'), default='local')
    full.add_argument('--device', default='plughw:0,0')
    full.add_argument('--listen', default='udp://0.0.0.0:5000')
    full.add_argument('--receive-timeout', type=float, default=15)
    model_dir = Path('sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01')
    for command in (run, processor, full):
        command.add_argument('--model',type=Path,default=model_dir/'model.int8.onnx')
        command.add_argument('--tokens',type=Path,default=model_dir/'tokens.txt')
        command.add_argument('--threads',type=int,default=2)
        command.add_argument('--segment-seconds',type=int,default=300)
        command.add_argument('--flush-seconds', type=int, default=60, help='Batch audio and text writes at this interval')
        command.add_argument('--retention-days',type=int,default=7, help='Delete closed recordings older than this many days')
        command.add_argument('--max-utterance',type=float,default=20)
    for command in (collector, run, processor, full):
        command.add_argument('--sample-rate', type=int, choices=(8000, 12000, 16000, 24000, 48000), default=48000,
                             help='Capture/archive sample rate (default: 48000)')
    run.add_argument('--device',default='plughw:0,0')
    serve = commands.add_parser('serve', help='Start HTTP API and built web UI')
    for command in (serve, full):
        command.add_argument('--host', default='127.0.0.1')
        command.add_argument('--port', type=int, default=8000)
        command.add_argument('--web-dist', type=Path, default=Path('web/dist'))
    query = commands.add_parser('query')
    query.add_argument('--text',default='')
    query.add_argument('--limit',type=int,default=50)
    export = commands.add_parser('export')
    export.add_argument('id',type=int)
    export.add_argument('output',type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    if args.command == 'serve':
        import uvicorn
        from .api import create_app
        if not 1 <= args.port <= 65535:
            parser.error('Port must be between 1 and 65535')
        uvicorn.run(create_app(args.data, args.web_dist), host=args.host, port=args.port)
    elif args.command == 'export':
        export_clip(args.data,args.id,args.output)
    elif args.command == 'query':
        for row in search_transcripts(args.data, args.text, args.limit):
            print(json.dumps(row, ensure_ascii=False))
    elif args.command == 'collector':
        if not shutil.which('ffmpeg'):
            parser.error('Install ffmpeg with ALSA and libopus support')
        if not 500 <= args.bitrate <= 512000:
            parser.error('Opus bitrate must be between 500 and 512000 bits/s')
        try:
            target = udp_url(args.target)
        except ValueError as exc:
            parser.error(str(exc))
        stop = stop_event()
        supervise(lambda: transmit(args.device, target, str(args.bitrate), stop, args.sample_rate), stop)
    else:
        if args.segment_seconds <= 0 or args.max_utterance <= 0 or args.threads <= 0 or args.retention_days <= 0 or args.flush_seconds <= 0:
            parser.error('Durations, retention days and threads must be positive')
        network = args.command == 'processor' or (args.command == 'full' and args.source == 'udp')
        executable = 'ffmpeg' if network else 'arecord'
        if not shutil.which(executable):
            parser.error(f'Missing executable: {executable}')
        if network:
            if args.receive_timeout <= 0:
                parser.error('Receive timeout must be positive')
            try:
                args.listen = udp_url(args.listen, receive=True, timeout=args.receive_timeout)
            except ValueError as exc:
                parser.error(str(exc))
        if args.command == 'full' and not 1 <= args.port <= 65535:
            parser.error('Port must be between 1 and 65535')
        for path in (args.model,args.tokens):
            if not path.is_file():
                parser.error(f'Missing file: {path}')
        args.data.mkdir(parents=True, exist_ok=True)
        lock = (args.data / 'capture.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Another capture process is already using this data directory')
        stop = stop_event()
        def attempt():
            recognizer = SherpaRecognizer(args.model, args.tokens, args.threads, args.max_utterance, sample_rate=args.sample_rate)
            source = FFmpegUdpSource(args.listen, args.sample_rate) if network else AlsaSource(args.device, args.sample_rate)
            try:
                # Anchor a network session at its first decoded audio, not listener startup.
                archive = Archive(args.data, rate=source.sample_rate, segment_seconds=args.segment_seconds, retention_days=args.retention_days, flush_seconds=args.flush_seconds)
            except BaseException:
                source.close()
                raise
            run_session(source, recognizer, archive, stop, anchor_on_first_audio=network)
        try:
            if args.command == 'full':
                run_full(attempt, stop, args)
            else:
                supervise(attempt, stop)
        finally:
            lock.close()


def stop_event():
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    return stop


def supervise(attempt, stop):
    while not stop.is_set():
        try:
            attempt()
        except Exception:
            log.exception('Session failed; retrying in 5 seconds')
            stop.wait(5)


def run_full(attempt, stop, args):
    """Supervise web as a child; shut down both components together."""
    web = subprocess.Popen([
        sys.executable, '-m', 'mic_spy', '--data', str(args.data.resolve()),
        'serve', '--host', args.host, '--port', str(args.port),
        '--web-dist', str(args.web_dist.resolve())])
    web_failed = threading.Event()

    def monitor():
        while not stop.wait(0.2):
            if web.poll() is not None:
                log.error('Web service exited; stopping full mode')
                web_failed.set()
                stop.set()
                return

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        supervise(attempt, stop)
        if web_failed.is_set():
            raise SystemExit(1)
    finally:
        stop.set()
        terminate(web)
        watcher.join(timeout=1)
