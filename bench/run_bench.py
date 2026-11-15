"""ASR benchmark for Local Dictation on the real target machine.

Measures per engine:
  - load time, RAM delta after load and peak during decode
  - WER / CER (Hebrew-normalized), per duration bucket (short <6s, mid 6-15s, long >15s)
  - latency = time from "speech ended" to final text
      * offline engines (Whisper): full transcription time of the clip
      * streaming engines (Zipformer): time to finalize after the last audio chunk,
        audio fed incrementally in real-time-sized chunks (compute not throttled)
  - RTF = decode compute time / audio duration
  - CPU-seconds per audio-second (proxy for power draw; Windows has no per-process power API)

Usage:
  python bench/run_bench.py --engines ivrit-turbo,zipformer --limit 60
  python bench/run_bench.py --dataset bench/data/mine --engines all
  python bench/run_bench.py --stress 4   # same, with 4 background busy processes (simulates IDE+browser load)

Dataset layout: either FLEURS (dev.tsv + dev/*.wav) or a folder with *.wav + *.txt
(same stem; the reference transcript, UTF-8) such as the one produced by record_testset.py.
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import multiprocessing as mp
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

ROOT = Path(__file__).resolve().parent
PROC = psutil.Process()

# ---------------------------------------------------------------- normalization
_NIKUD = re.compile(r"[֑-ׇ]")
_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)


def norm(text: str) -> str:
    t = _NIKUD.sub("", text)
    t = t.replace("׳", "'").replace("״", '"').replace("`", "'")
    t = t.replace("-", " ")
    t = _PUNCT.sub(" ", t.lower())
    t = t.replace("'", "")
    return re.sub(r"\s+", " ", t).strip()


def is_reliable(ref: str) -> bool:
    """FLEURS-he readers read sentences with Latin/digits/parentheses from a bidi-broken prompt,
    so the audio has clauses in a different order than the reference. Only pure-Hebrew refs are reliable."""
    return not re.search(r"[A-Za-z0-9()\[\]\"]", ref)


# ---------------------------------------------------------------- dataset
@dataclass
class Clip:
    path: Path
    ref: str
    audio: np.ndarray = field(repr=False, default=None)
    dur: float = 0.0


def load_dataset(ds: Path, limit: int) -> list[Clip]:
    clips: list[Clip] = []
    tsv = ds / "dev.tsv"
    if tsv.exists():  # FLEURS layout
        with open(tsv, encoding="utf-8") as f:
            for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                wav = ds / "dev" / row[1]
                if wav.exists():
                    clips.append(Clip(wav, row[2]))
    else:
        for wav in sorted(ds.glob("*.wav")):
            txt = wav.with_suffix(".txt")
            if txt.exists():
                clips.append(Clip(wav, txt.read_text(encoding="utf-8").strip()))
    if limit:
        # spread across the set, deterministic
        step = max(1, len(clips) // limit)
        clips = clips[::step][:limit]
    for c in clips:
        a, sr = sf.read(c.path, dtype="float32", always_2d=True)
        a = a.mean(axis=1)
        if sr != 16000:
            idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr))
            a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
        c.audio, c.dur = a, len(a) / 16000
    return clips


# ---------------------------------------------------------------- engines
class Engine:
    name = "?"

    def load(self): ...
    def transcribe(self, audio: np.ndarray) -> str: ...


class FasterWhisper(Engine):
    def __init__(self, name, model, beam=1, compute="int8", threads=0):
        self.name, self.model_id, self.beam, self.compute, self.threads = name, model, beam, compute, threads

    def load(self):
        from faster_whisper import WhisperModel
        self.m = WhisperModel(self.model_id, device="cpu", compute_type=self.compute,
                              cpu_threads=self.threads or 0)

    def transcribe(self, audio):
        segs, _ = self.m.transcribe(audio, language="he", beam_size=self.beam,
                                    vad_filter=False, condition_on_previous_text=False,
                                    without_timestamps=True)
        return " ".join(s.text.strip() for s in segs)


class Zipformer(Engine):
    name = "zipformer"
    CHUNK = 0.32  # seconds of audio pushed per call, like a live mic callback

    def __init__(self, threads=4):
        self.threads = threads

    def load(self):
        import sherpa_onnx
        from huggingface_hub import snapshot_download
        d = Path(snapshot_download("ivrit-ai/zipformer2-66m-streaming-he",
                                   allow_patterns=["sherpa-onnx/*"])) / "sherpa-onnx"
        self.r = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"), encoder=str(d / "encoder.int8.onnx"),
            decoder=str(d / "decoder.int8.onnx"), joiner=str(d / "joiner.int8.onnx"),
            model_type="zipformer2", num_threads=self.threads)

    def transcribe(self, audio):
        s = self.r.create_stream()
        n = int(self.CHUNK * 16000)
        for i in range(0, len(audio), n):
            s.accept_waveform(16000, audio[i:i + n])
            while self.r.is_ready(s):
                self.r.decode_stream(s)
        # ---- speech ended: only this part counts as user-visible latency
        self._t_end = time.perf_counter()
        s.accept_waveform(16000, np.zeros(int(1.5 * 16000), dtype=np.float32))
        s.input_finished()
        while self.r.is_ready(s):
            self.r.decode_stream(s)
        return self.r.get_result(s)


class OpenVINO(Engine):
    def __init__(self, name, model_dir, device):
        self.name, self.model_dir, self.device = name, model_dir, device

    def load(self):
        sys.path.insert(0, str(ROOT.parent))
        from dictation.asr import OpenVINOEngine
        self.e = OpenVINOEngine(self.model_dir, self.device)
        self.e.load()

    def transcribe(self, audio):
        return self.e._decode(audio, None)  # no VAD here: same input as the other engines


OV_DIR = Path(os.environ.get("LOCALDICTATION_MODELS", ROOT.parent / "models"))

ENGINES = {
    "ov-gpu-ivrit-turbo": lambda: OpenVINO("ov-gpu-ivrit-turbo", str(OV_DIR / "ivrit-turbo-ov-fp16"), "GPU"),
    "ov-gpu-ivrit-turbo-int8": lambda: OpenVINO("ov-gpu-ivrit-turbo-int8", str(OV_DIR / "ivrit-turbo-ov-int8"), "GPU"),
    "ov-npu-ivrit-turbo-int8": lambda: OpenVINO("ov-npu-ivrit-turbo-int8", str(OV_DIR / "ivrit-turbo-ov-int8"), "NPU"),
    "ov-cpu-ivrit-turbo-int8": lambda: OpenVINO("ov-cpu-ivrit-turbo-int8", str(OV_DIR / "ivrit-turbo-ov-int8"), "CPU"),
    "ivrit-turbo-t12": lambda: FasterWhisper("ivrit-turbo-t12", "ivrit-ai/whisper-large-v3-turbo-ct2", threads=12),
    "ivrit-turbo": lambda: FasterWhisper("ivrit-turbo", "ivrit-ai/whisper-large-v3-turbo-ct2"),
    "ivrit-turbo-b5": lambda: FasterWhisper("ivrit-turbo-b5", "ivrit-ai/whisper-large-v3-turbo-ct2", beam=5),
    "ivrit-large": lambda: FasterWhisper("ivrit-large", "ivrit-ai/whisper-large-v3-ct2"),
    "openai-turbo": lambda: FasterWhisper("openai-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo"),
    "openai-medium": lambda: FasterWhisper("openai-medium", "Systran/faster-whisper-medium"),
    "openai-small": lambda: FasterWhisper("openai-small", "Systran/faster-whisper-small"),
    "zipformer": lambda: Zipformer(),
}


# ---------------------------------------------------------------- stress
def _busy(stop):
    x = 0
    while not stop.is_set():
        for _ in range(200000):
            x += 1
        time.sleep(0.002)


# ---------------------------------------------------------------- main
def bucket(d):
    return "short<6s" if d < 6 else ("mid6-15s" if d <= 15 else "long>15s")


def run_engine(eng: Engine, clips: list[Clip]) -> dict:
    import jiwer
    gc.collect()
    rss0 = PROC.memory_info().rss
    t0 = time.perf_counter()
    eng.load()
    load_s = time.perf_counter() - t0
    # warmup (excluded): what the app does at startup
    t0 = time.perf_counter()
    eng.transcribe(np.zeros(16000, dtype=np.float32))
    warm_s = time.perf_counter() - t0
    rss_loaded = PROC.memory_info().rss
    peak = rss_loaded
    rows = []
    for c in clips:
        cpu0 = sum(PROC.cpu_times()[:2])
        t0 = time.perf_counter()
        eng._t_end = None
        hyp = eng.transcribe(c.audio)
        t1 = time.perf_counter()
        cpu = sum(PROC.cpu_times()[:2]) - cpu0
        lat = t1 - (eng._t_end or t0)
        peak = max(peak, PROC.memory_info().rss)
        r, h = norm(c.ref), norm(hyp)
        rows.append(dict(file=c.path.name, dur=c.dur, compute=t1 - t0, latency=lat, cpu=cpu,
                         wer=jiwer.wer(r, h) if r else 0.0, cer=jiwer.cer(r, h) if r else 0.0,
                         ref=c.ref, hyp=hyp, r_words=len(r.split())))
        print(f"  {eng.name:14s} {c.dur:5.1f}s lat={lat:5.2f}s wer={rows[-1]['wer']:.2f}  {hyp[:70]}", flush=True)

    def agg(rs):
        refs = [norm(x["ref"]) for x in rs]
        hyps = [norm(x["hyp"]) for x in rs]
        audio = sum(x["dur"] for x in rs)
        return dict(n=len(rs), audio_s=round(audio, 1),
                    wer=round(100 * jiwer.wer(refs, hyps), 2), cer=round(100 * jiwer.cer(refs, hyps), 2),
                    lat_p50=round(statistics.median(x["latency"] for x in rs), 3),
                    lat_p90=round(sorted(x["latency"] for x in rs)[int(0.9 * (len(rs) - 1))], 3),
                    rtf=round(sum(x["compute"] for x in rs) / audio, 3),
                    cpu_s_per_audio_s=round(sum(x["cpu"] for x in rs) / audio, 2))

    clean = [r for r in rows if is_reliable(r["ref"])]
    out = dict(engine=eng.name, load_s=round(load_s, 2), warmup_s=round(warm_s, 2),
               ram_loaded_mb=round((rss_loaded - rss0) / 2**20), ram_peak_mb=round((peak - rss0) / 2**20),
               all=agg(rows), clean=agg(clean) if clean else None,
               buckets={b: agg([r for r in rows if bucket(r["dur"]) == b])
                                       for b in sorted({bucket(r["dur"]) for r in rows})},
               rows=rows)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(ROOT / "data" / "fleurs_he"))
    ap.add_argument("--engines", default="ivrit-turbo,zipformer")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--stress", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args()

    clips = load_dataset(Path(a.dataset), a.limit)
    print(f"{len(clips)} clips, {sum(c.dur for c in clips):.0f}s audio", flush=True)
    names = list(ENGINES) if a.engines == "all" else a.engines.split(",")

    stop = mp.Event()
    workers = [mp.Process(target=_busy, args=(stop,), daemon=True) for _ in range(a.stress)]
    for w in workers:
        w.start()

    Path(a.out).mkdir(parents=True, exist_ok=True)
    tag = f"{Path(a.dataset).name}{'_stress' + str(a.stress) if a.stress else ''}_{time.strftime('%Y%m%d-%H%M%S')}"
    results = []
    for n in names:
        print(f"== {n}", flush=True)
        try:
            eng = ENGINES[n]()
            results.append(run_engine(eng, clips))
        except Exception as e:
            print(f"   !! {n} failed: {type(e).__name__}: {e}", flush=True)
            continue
        finally:
            eng = None
            gc.collect()
        # save after every engine so a crash/timeout later never loses finished results
        (Path(a.out) / f"{tag}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    stop.set()

    print("\nengine          load  warm  RAM(MB)  WER%  cleanWER%  CER%   lat_p50 lat_p90  RTF    cpu-s/s")
    for r in results:
        s = r["all"]
        cw = r["clean"]["wer"] if r.get("clean") else float("nan")
        print(f"{r['engine']:14s} {r['load_s']:5.1f} {r['warmup_s']:5.2f} {r['ram_loaded_mb']:7d} "
              f"{s['wer']:6.2f} {cw:8.2f} {s['cer']:6.2f} {s['lat_p50']:7.2f} {s['lat_p90']:7.2f} {s['rtf']:6.3f} {s['cpu_s_per_audio_s']:6.2f}")
        for b, bs in r["buckets"].items():
            print(f"   {b:11s} n={bs['n']:3d} WER {bs['wer']:6.2f}  lat_p50 {bs['lat_p50']:.2f}  p90 {bs['lat_p90']:.2f}")
    print(f"\nsaved {tag}.json")


if __name__ == "__main__":
    mp.freeze_support()
    main()
