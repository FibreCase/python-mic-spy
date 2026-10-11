"""ALSA audio input. No recognition or persistence dependencies."""
import subprocess


class AlsaSource:
    def __init__(self, device='plughw:0,0', sample_rate=48000):
        self.sample_rate = sample_rate
        self.process = subprocess.Popen(
            ['arecord', '-D', device, '-t', 'raw', '-f', 'S16_LE',
             '-r', str(self.sample_rate), '-c', '1'], stdout=subprocess.PIPE)

    def read(self) -> bytes:
        return self.process.stdout.read(self.sample_rate // 10 * 2)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()
