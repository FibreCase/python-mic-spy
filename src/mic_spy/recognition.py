"""Streaming speech recognition. Owns its sample clock, but performs no I/O."""
from pathlib import Path
from .contracts import Transcript


class SherpaRecognizer:
    def __init__(self, model: Path, tokens: Path, threads=2, max_utterance=20, sample_rate=48000):
        import numpy as np
        import sherpa_onnx
        self.np = np
        self.sample_rate = sample_rate
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(
            model=str(model), tokens=str(tokens), num_threads=threads,
            sample_rate=16000, enable_endpoint_detection=True,
            rule3_min_utterance_length=max_utterance)
        self.stream = self.recognizer.create_stream()
        self.start = self.position = 0

    def _decode(self):
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)

    def _result(self, reason):
        text = self.recognizer.get_result(self.stream).strip()
        if text and self.position > self.start:
            return [Transcript(self.start, self.position, text, reason)]
        return []

    def accept(self, pcm: bytes) -> list[Transcript]:
        if len(pcm) % 2:
            raise ValueError('PCM must contain complete int16 samples')
        self.stream.accept_waveform(self.sample_rate,
            self.np.frombuffer(pcm, dtype='<i2').astype(self.np.float32) / 32768)
        self.position += len(pcm) // 2
        self._decode()
        if self.recognizer.is_endpoint(self.stream):
            result = self._result('endpoint')
            self.start = self.position
            # Reset decoding state while preserving the feature extractor/resampler.
            self.recognizer.reset(self.stream)
            return result
        return []

    def finish(self, reason: str) -> list[Transcript]:
        # Decoder padding does not advance the recorded sample clock.
        self.stream.accept_waveform(self.sample_rate,
                                   self.np.zeros(self.sample_rate // 2, dtype=self.np.float32))
        self.stream.input_finished()
        self._decode()
        return self._result(reason)
