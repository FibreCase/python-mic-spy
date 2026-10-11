"""Coordinate interchangeable source, recognizer and store implementations."""
import queue
import threading
from .contracts import AudioSource, SpeechRecognizer, RecordingStore


def run_session(source: AudioSource, recognizer: SpeechRecognizer,
                store: RecordingStore, stop: threading.Event, *, anchor_on_first_audio=False):
    packets = queue.Queue(maxsize=100)
    ended = threading.Event()
    errors = []

    def reader():
        try:
            while not stop.is_set():
                pcm = source.read()
                if not pcm:
                    break
                try:
                    packets.put(pcm, timeout=1)
                except queue.Full:
                    raise RuntimeError('Recognition queue overflowed')
        except Exception as exc:
            errors.append(exc)
        finally:
            ended.set()

    def save(results):
        for result in results:
            store.transcript(result.start_sample, result.end_sample,
                             result.text, result.reason)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    status = 'stopped'
    first_audio = True
    try:
        while not stop.is_set():
            store.maintenance()
            try:
                pcm = packets.get(timeout=0.2)
            except queue.Empty:
                if ended.is_set():
                    if errors:
                        raise errors[0]
                    raise RuntimeError('Audio source stopped')
                continue
            if first_audio and anchor_on_first_audio:
                store.anchor_time()
            first_audio = False
            store.append(pcm)
            save(recognizer.accept(pcm))
    except BaseException:
        status = 'failed'
        raise
    finally:
        try:
            source.close()
        finally:
            thread.join(timeout=3)
            try:
                save(recognizer.finish('shutdown' if stop.is_set() else 'capture_error'))
            finally:
                store.close(status)
