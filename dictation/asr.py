"""ASR engines behind one small interface, so models can be swapped without touching the rest.

- WhisperEngine: faster-whisper / CTranslate2 (default: ivrit-ai whisper-large-v3-turbo, int8 on CPU)
- VAD: Silero (bundled with faster-whisper) trims silence and detects "no speech", which also
  prevents Whisper's classic hallucinations on silent input.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

SR = 16000

# Whisper is known to emit these on near-silent input (subtitle credits seen in training data).
HALLUCINATIONS = {"תודה רבה.", "תודה רבה", "תודה.", "תודה", "כתוביות", "thank you.", "thanks for watching!",
                  "תודה שצפיתם", "תודה שצפיתם!", "שלום", "you", "."}


@dataclass
class Result:
    text: str
    speech_s: float      # seconds of detected speech
    audio_s: float
    decode_s: float
    no_speech: bool = False


class VAD:
    def __init__(self):
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        self._get, self._opts = get_speech_timestamps, VadOptions(
            threshold=0.45, min_speech_duration_ms=150, min_silence_duration_ms=600, speech_pad_ms=250)

    def speech(self, audio: np.ndarray) -> tuple[np.ndarray, float]:
        """Return audio with long silences removed, plus seconds of speech."""
        ts = self._get(audio, self._opts)
        if not ts:
            return np.zeros(0, np.float32), 0.0
        parts = [audio[t["start"]:t["end"]] for t in ts]
        speech_s = sum(len(p) for p in parts) / SR
        gap = np.zeros(int(0.15 * SR), np.float32)  # keep a short pause so sentences stay separated
        out = []
        for p in parts:
            out += [p, gap]
        return np.concatenate(out[:-1]), speech_s


class _Base:
    def __init__(self):
        self.vad = VAD()
        self.load_s = 0.0

    def _decode(self, speech: np.ndarray, prompt: str | None) -> str:
        raise NotImplementedError

    def warmup(self):
        self._decode(np.random.default_rng(0).normal(0, 0.01, SR * 2).astype(np.float32), None)

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> Result:
        t0 = time.perf_counter()
        audio_s = len(audio) / SR
        speech, speech_s = self.vad.speech(audio)
        if speech_s < 0.3:
            return Result("", speech_s, audio_s, time.perf_counter() - t0, no_speech=True)
        text = self._decode(speech, prompt).strip()
        if speech_s < 2.0 and text.lower() in HALLUCINATIONS:
            return Result("", speech_s, audio_s, time.perf_counter() - t0, no_speech=True)
        return Result(text, speech_s, audio_s, time.perf_counter() - t0)


class WhisperEngine(_Base):
    """faster-whisper / CTranslate2 on CPU."""

    def __init__(self, model: str, threads: int = 8, beam: int = 1, compute: str = "int8"):
        super().__init__()
        self.model_id, self.threads, self.beam, self.compute = model, threads, beam, compute
        self.name = f"ct2-cpu:{model.split('/')[-1]}"

    def load(self):
        from faster_whisper import WhisperModel
        t0 = time.perf_counter()
        self.m = WhisperModel(self.model_id, device="cpu", compute_type=self.compute, cpu_threads=self.threads)
        self.load_s = time.perf_counter() - t0
        self.warmup()

    def _decode(self, speech, prompt):
        segs, _ = self.m.transcribe(speech, language="he", beam_size=self.beam, vad_filter=False,
                                    condition_on_previous_text=False, without_timestamps=True,
                                    initial_prompt=prompt or None, no_speech_threshold=0.6)
        return " ".join(s.text.strip() for s in segs)


class OpenVINOEngine(_Base):
    """OpenVINO GenAI WhisperPipeline on Intel Arc GPU (or NPU / CPU). model_dir = optimum-cli export."""

    def __init__(self, model_dir: str, device: str = "GPU", beam: int = 1):
        super().__init__()
        self.model_dir, self.device, self.beam = model_dir, device, beam
        from pathlib import Path
        self.name = f"ov-{device.lower()}:{Path(model_dir).name}"

    def load(self):
        import openvino_genai as ov_genai
        from pathlib import Path
        t0 = time.perf_counter()
        cache = Path(self.model_dir) / "ov_cache"   # compiled-kernel cache: makes 2nd+ startup much faster
        self.pipe = ov_genai.WhisperPipeline(self.model_dir, self.device, CACHE_DIR=str(cache))
        self.cfg = self.pipe.get_generation_config()
        self.cfg.language, self.cfg.task, self.cfg.return_timestamps = "<|he|>", "transcribe", False
        self.cfg.num_beams = self.beam
        self.cfg.max_new_tokens = 440
        self.load_s = time.perf_counter() - t0
        self.warmup()

    def _decode(self, speech, prompt):
        cfg = self.cfg
        if prompt is not None:
            try:
                cfg.initial_prompt = prompt
            except Exception:
                pass
        out = self.pipe.generate(speech.tolist() if self.device == "NPU" else speech, cfg)
        if prompt is not None:
            try:
                cfg.initial_prompt = None
            except Exception:
                pass
        return str(out.texts[0]) if out.texts else ""


class StreamingPreview:
    """Live partial text while speaking (ivrit-ai Zipformer2, true streaming, CPU int8, ~180MB).
    Display only: the final inserted text always comes from the Whisper engine."""

    REPO = "ivrit-ai/zipformer2-66m-streaming-he"

    def __init__(self, on_partial, min_interval=0.3):
        self.on_partial, self.min_interval = on_partial, min_interval
        self.r = None
        self._q = None
        self._t = None

    def load(self):
        import sherpa_onnx
        from huggingface_hub import snapshot_download
        from pathlib import Path
        d = Path(snapshot_download(self.REPO, allow_patterns=["sherpa-onnx/*"])) / "sherpa-onnx"
        self.r = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"), encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"),
            joiner=str(d / "joiner.int8.onnx"), model_type="zipformer2", num_threads=2)

    @property
    def ready(self):
        return self.r is not None

    def start(self):
        import queue
        import threading
        if not self.ready:
            return
        self._q = queue.Queue()
        self._t = threading.Thread(target=self._run, args=(self._q,), daemon=True, name="preview")
        self._t.start()

    def feed(self, chunk: np.ndarray):  # called from the audio callback: must not block
        if self._q is not None:
            self._q.put(chunk)

    def stop(self):
        if self._q is not None:
            self._q.put(None)
        self._q = None

    def _run(self, q):
        s = self.r.create_stream()
        last, last_t = "", 0.0
        while True:
            c = q.get()
            if c is None:
                return
            s.accept_waveform(SR, c)
            while self.r.is_ready(s):
                self.r.decode_stream(s)
            now = time.perf_counter()
            if now - last_t >= self.min_interval:
                text = self.r.get_result(s).strip()
                if text and text != last:
                    last, last_t = text, now
                    self.on_partial(text)


def make_engine(spec: dict) -> _Base:
    if spec.get("backend") == "openvino":
        return OpenVINOEngine(spec["model_dir"], spec.get("device", "GPU"), spec.get("beam", 1))
    return WhisperEngine(spec["model"], spec.get("threads", 8), spec.get("beam", 1))
