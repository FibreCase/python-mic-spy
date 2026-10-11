import sys
import threading
from types import SimpleNamespace
import numpy as np
from mic_spy.recognition import SherpaRecognizer
from mic_spy.transport import collector_command, receiver_command
from mic_spy.cli import run_full


def test_48k_input_keeps_model_at_16k_and_archive_sample_positions(monkeypatch):
    streams = []
    config = {}
    class Stream:
        def __init__(self):
            self.calls = []
        def accept_waveform(self, rate, samples):
            self.calls.append((rate,len(samples)))
        def input_finished(self):
            pass
    class Engine:
        def create_stream(self):
            stream = Stream()
            streams.append(stream)
            return stream
        def is_ready(self, stream):
            return False
        def is_endpoint(self, stream):
            return True
        def get_result(self, stream):
            return '采样位置'
        def reset(self, stream):
            assert stream is streams[0]
            self.resets = getattr(self, 'resets', 0) + 1
    def factory(**kwargs):
        config.update(kwargs)
        return Engine()
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace(
        OnlineRecognizer=SimpleNamespace(from_zipformer2_ctc=factory)))
    recognizer = SherpaRecognizer('model', 'tokens', sample_rate=48000)
    result = recognizer.accept(np.zeros(4800,dtype='<i2').tobytes())[0]
    assert config['sample_rate'] == 16000
    assert streams[0].calls == [(48000,4800)]
    assert result.end_sample == 4800
    second = recognizer.accept(np.zeros(4800,dtype='<i2').tobytes())[0]
    assert (second.start_sample, second.end_sample) == (4800,9600)
    recognizer.finish('shutdown')
    assert len(streams) == 1
    assert streams[0].calls == [(48000,4800),(48000,4800),(48000,24000)]
    assert recognizer.recognizer.resets == 2
    assert recognizer.position == 9600


def test_transport_defaults_48k():
    command = collector_command('plughw:0,0','udp://host:5000')
    assert command[command.index('-sample_rate')+1] == '48000'
    command = receiver_command('udp://0.0.0.0:5000')
    assert command[command.index('-ar')+1] == '48000'


def test_full_shuts_web_down_with_recording(tmp_path,monkeypatch):
    class Process:
        done = False
        def poll(self):
            return 0 if self.done else None
        def terminate(self):
            self.done = True
        def wait(self,timeout=None):
            return 0
    process = Process()
    commands = []
    def launch(command):
        commands.append(command)
        return process
    monkeypatch.setattr('mic_spy.cli.subprocess.Popen',launch)
    stop = threading.Event()
    args = SimpleNamespace(data=tmp_path,host='127.0.0.1',port=8000,web_dist=tmp_path/'web')
    run_full(lambda:stop.set(),stop,args)
    assert process.done
    assert '-m' in commands[0] and 'serve' in commands[0]
