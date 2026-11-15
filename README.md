# Local Dictation

Offline Hebrew + English voice typing for Windows and macOS. Hold a key, speak, release: the text appears at your cursor
in any app. Nothing leaves your machine.

Runs [ivrit.ai](https://www.ivrit.ai)'s Hebrew Whisper on your laptop's built-in GPU: Intel Arc via OpenVINO on Windows,
Apple Silicon via MLX on macOS. About **10× faster than CPU**, no NVIDIA needed.

> **macOS support is new and not yet tested on real hardware.** Windows is tested daily. Mac reports welcome.

## Performance

Windows, Core Ultra 9 285H, FLEURS-he (details in [`bench/RESULTS.md`](bench/RESULTS.md)):

| Engine | Latency after speech | Hebrew WER |
|---|---|---|
| **ivrit turbo, Intel Arc GPU (OpenVINO)** | **0.55 s** | **13.6%** |
| ivrit turbo, CPU | 5.2 s | 13.7% |
| OpenAI whisper-large-v3-turbo, CPU | 5.7 s | 29.5% |

## Features

- **Works in any app**: VS Code, Cursor, browsers, WhatsApp, Word, terminals. Never presses Enter.
- **Hebrew/English in one sentence**, with a personal dictionary that handles Hebrew prefixes (`בסופה בייס` → `ב-Supabase`).
- **Hold to talk, or tap for hands-free.** Any key, including the Copilot key.
- **Per-app modes**: Verbatim, Clean, Professional, Developer.
- **Optional live preview** while speaking (ivrit.ai Zipformer streaming).
- **Lecture editor**: a built-in RTL word processor that transcribes a lecture live while you keep editing.
  Timestamps, ⭐ important moments, a per-lecture glossary, autosave of audio + text, Word export.
- **Optional local LLM polish** (Gemma 4 E4B). A guard rejects any output that answers, follows dictated instructions,
  changes numbers, or adds English words you didn't say.
- **Private**: mic opens only while recording, no dictation into password fields, no network at runtime, logs contain timings only.

## Requirements

| | Windows | macOS |
|---|---|---|
| OS | Windows 11 | macOS 13+ |
| Python | 3.12 | 3.12 |
| Fast path | Intel Core Ultra / Arc (OpenVINO) | Apple Silicon M1+ (MLX) |
| Fallback | CPU, ~5 s | CPU on Intel Macs |

~6 GB disk, +6 GB for the optional LLM.

## Install

**Windows**
```
git clone https://github.com/oriamos/local-dictation
cd local-dictation
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\setup_models.py
```

**macOS**

macOS doesn't ship Python 3.12. Install it first from [python.org](https://www.python.org/downloads/macos/)
or with Homebrew (`brew install python@3.12`), and check that `python3.12 --version` works.

```
git clone https://github.com/oriamos/local-dictation
cd local-dictation
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/setup_models.py
```
Then grant **Accessibility**, **Input Monitoring** and **Microphone** to the app that runs Python
(System Settings → Privacy & Security). The app also prompts for them on first start.

Add `--llm` to the setup command to download the optional polish model.

## Usage

Start it with **`Local Dictation.lnk`** (Windows) or **`Local Dictation.command`** (macOS).
A mic icon appears in the tray / menu bar (the menu is in Hebrew).

| Action | Windows | macOS |
|---|---|---|
| Dictate: hold, speak, release | **Right Ctrl** | **Right ⌥ Option** |
| Hands-free | tap, then tap again | same |
| Cancel | Esc | Esc |
| Change the key | Tray → Hotkey → Pick a hotkey… | same |
| Transcribe a lecture | Tray → Lecture editor → Start | same |

## Known limitations

- No NVIDIA/CUDA path yet; no Linux.
- macOS: untested on hardware so far. Secure Input (password fields, some terminals) blocks dictation by design.
- Intel's digital mic takes ~0.4 s to wake (shown as "connecting"). Enable the warm-mic option for instant restarts.
- Live preview updates every ~1.3 s (model limit).
- The LLM polish adds 2–6 s, so it's off by default.

## Project layout

| Path | |
|---|---|
| `dictation/` | the app. Platform code in `*_win.py` / `*_mac.py` (hotkey, inject, context); the rest is shared |
| `scripts/setup_models.py` | model download/conversion, launcher |
| `bench/` | ASR and LLM benchmarks |
| `tests/` | smoke tests (`python -m tests.smoke_pipeline`, `tests.sim_mac_hotkey`, …) |
| `docs/ARCHITECTURE.md` | design decisions (Hebrew) |

Contributions welcome, especially macOS testing, an NVIDIA engine, and real-voice test data.

## License

Code: MIT. Models are downloaded separately under their own licenses:
[ivrit-ai Whisper](https://huggingface.co/ivrit-ai/whisper-large-v3-turbo) (Apache-2.0) and its
[MLX conversion](https://huggingface.co/mlx-community/ivrit-ai-whisper-large-v3-turbo-mlx),
[ivrit-ai Zipformer](https://huggingface.co/ivrit-ai/zipformer2-66m-streaming-he) by Yakov Kolani (CC-BY-4.0),
Gemma 4 E4B ([OpenVINO](https://huggingface.co/OpenVINO/gemma-4-E4B-it-int4-ov) / [MLX](https://huggingface.co/mlx-community/gemma-4-e4b-it-4bit), Apache-2.0).
Built on OpenVINO, MLX, faster-whisper, sherpa-onnx, Silero VAD, PyObjC and PySide6 (LGPL-3.0).
