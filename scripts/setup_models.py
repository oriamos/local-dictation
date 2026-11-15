"""One-time model setup (needs internet once; the app itself runs offline afterwards).

    .venv\\Scripts\\python scripts\\setup_models.py          # ASR models (required, ~5 GB download)
    .venv\\Scripts\\python scripts\\setup_models.py --llm    # + optional local-LLM polish (Gemma 4 E4B, ~6 GB)

What it does:
  1. Downloads the CPU fallback (ivrit-ai/whisper-large-v3-turbo-ct2) and the streaming preview
     (ivrit-ai/zipformer2-66m-streaming-he) into the Hugging Face cache.
  2. Converts ivrit-ai/whisper-large-v3-turbo to OpenVINO FP16 → models/ivrit-turbo-ov-fp16 (Intel GPU/NPU).
     The converter (optimum-intel + torch) is installed into a separate, throw-away venv (.convenv) so the
     app's own environment stays small. Delete .convenv afterwards if you like.
  3. With --llm: downloads OpenVINO/gemma-4-E4B-it-int4-ov → models/gemma-4-E4B-it-int4-ov.
  4. Reports which OpenVINO devices (GPU / NPU / CPU) this machine has.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = Path(os.environ.get("LOCALDICTATION_MODELS", ROOT / "models"))
CONVENV = ROOT / ".convenv"
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def step(msg):
    print(f"\n== {msg}", flush=True)


def hf_download(repo, **kw):
    from huggingface_hub import snapshot_download
    return snapshot_download(repo, **kw)


def converter_python() -> Path:
    py = CONVENV / "Scripts" / "python.exe"
    if not py.exists():
        step("Creating converter environment (.convenv) — one time, ~1.5 GB")
        venv.create(CONVENV, with_pip=True)
        subprocess.check_call([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"])
        subprocess.check_call([str(py), "-m", "pip", "install", "-q", "torch", "--index-url",
                               "https://download.pytorch.org/whl/cpu"])
        subprocess.check_call([str(py), "-m", "pip", "install", "-q", "optimum-intel[openvino]", "nncf"])
    return py


def convert_whisper(weight_format="fp16"):
    out = MODELS / f"ivrit-turbo-ov-{weight_format}"
    if (out / "openvino_encoder_model.xml").exists():
        print(f"   already there: {out}"); return
    py = converter_python()
    cli = CONVENV / "Scripts" / "optimum-cli.exe"
    step(f"Converting ivrit-ai/whisper-large-v3-turbo → OpenVINO {weight_format.upper()} (a few minutes)")
    subprocess.check_call([str(cli), "export", "openvino", "--model", "ivrit-ai/whisper-large-v3-turbo",
                           "--task", "automatic-speech-recognition-with-past", "--weight-format", weight_format, str(out)])


def report_devices():
    try:
        import openvino as ov
        core = ov.Core()
        for d in core.available_devices:
            print(f"   {d:5s} {core.get_property(d, 'FULL_DEVICE_NAME')}")
        if "GPU" not in core.available_devices:
            print("   No Intel GPU found: the app will use the CPU fallback (accurate, ~5 s per dictation).\n"
                  "   On NVIDIA machines, faster-whisper with CUDA is the better route (not wired up yet).")
    except Exception as e:
        print("   could not query OpenVINO devices:", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="also download the optional Gemma 4 E4B polish model")
    ap.add_argument("--int8", action="store_true", help="also convert an INT8 Whisper (less memory, +0.6 WER)")
    a = ap.parse_args()
    MODELS.mkdir(parents=True, exist_ok=True)

    step("Downloading CPU fallback and streaming-preview models")
    hf_download("ivrit-ai/whisper-large-v3-turbo-ct2")
    hf_download("ivrit-ai/zipformer2-66m-streaming-he", allow_patterns=["sherpa-onnx/*"])

    convert_whisper("fp16")
    if a.int8:
        convert_whisper("int8")

    if a.llm:
        step("Downloading Gemma 4 E4B (OpenVINO int4) for the optional polish pass")
        hf_download("OpenVINO/gemma-4-E4B-it-int4-ov", local_dir=str(MODELS / "gemma-4-E4B-it-int4-ov"))

    step("OpenVINO devices on this machine")
    report_devices()
    make_shortcut()
    print("\nDone. Double-click 'Local Dictation.lnk' (or run:  .venv\\Scripts\\pythonw.exe launch.pyw)")


def make_shortcut():
    """'Local Dictation.lnk' in the project folder → the venv's pythonw + launch.pyw (no console window)."""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    lnk = ROOT / "Local Dictation.lnk"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{pyw}';$s.Arguments='\"{ROOT / 'launch.pyw'}\"';$s.WorkingDirectory='{ROOT}';"
          "$s.IconLocation='%SystemRoot%\\System32\\SndVol.exe,0';$s.Save()")
    try:
        subprocess.check_call(["powershell", "-NoProfile", "-Command", ps])
        print(f"   shortcut: {lnk}")
    except Exception as e:
        print("   could not create shortcut:", e)


if __name__ == "__main__":
    main()
