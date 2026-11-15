# Benchmark results — ASUS Zenbook Duo UX8406CA (Core Ultra 9 285H, Arc 140T, 32GB)

Date: 2026-11-15. Dataset: FLEURS-he dev, 60 clips (501 s audio), plugged in, nothing else running unless noted.
Raw per-clip data: `bench/results/*.json`. Reproduce: `python bench/run_bench.py --engines <names> --limit 60`.

## Summary

| Engine | Device | WER all | **WER clean** | latency p50 | p90 | RTF | load (cold / cached) | RAM | CPU-s per audio-s |
|---|---|---|---|---|---|---|---|---|---|
| **ivrit turbo FP16 (OpenVINO)** | **Arc 140T GPU** | 25.2 | **13.6** | **0.55 s** | 0.82 s | 0.070 | 20 s / 4 s | ~2.0–2.6 GB* | 0.06 |
| ivrit turbo INT8 (OpenVINO) | Arc 140T GPU | 25.6 | 14.2 | 0.43 s | 0.56 s | 0.053 | 12 s | 0.5 GB | 0.04 |
| ivrit turbo INT8 (OpenVINO) | NPU (AI Boost) | 25.2 | 13.6 | 1.75 s | 2.04 s | 0.213 | 185 s (first compile) | 3.6 GB | **0.01** |
| ivrit turbo INT8 (CTranslate2), 12 thr | CPU | 25.1 | 13.7 | 5.18 s | 5.51 s | 0.629 | 3 s | 0.9 GB | 6.8 |
| openai turbo INT8 (CTranslate2) | CPU | 36.8 | 29.5 | 5.70 s | 6.03 s | 0.691 | 3 s | 0.9 GB | 2.8 |
| ivrit Zipformer2 streaming INT8 | CPU | 28.8 | 18.5 | **0.03 s** | 0.05 s | 0.023 | 2.5 s | 0.18 GB | 0.22 |
| ivrit turbo FP16 GPU **+ 6 busy CPU procs** | GPU | 28.8 | 15.5 (n=30) | 0.47 s | 0.89 s | 0.067 | 4 s | — | 0.05 |

\* includes GPU shared-memory mappings counted in the process working set.

**WER clean** = only references with no Latin letters, digits, quotes or parentheses (43/60 clips).
FLEURS-he has a data defect: readers read mixed-direction sentences from a bidi-broken prompt, so the audio has
clauses in a different order than the reference text (OpenAI's untuned model reproduces the exact same order).
Those clips inflate "WER all" for every engine equally; "WER clean" is the meaningful accuracy number here.

## Latency targets

| Target | Result (GPU FP16) |
|---|---|
| recording start < 150 ms | mic open measured per recording in `app.log` (`mic open … ms`) |
| 5 s dictation < 1 s after speech end | ✅ ~0.45–0.55 s |
| 15 s dictation < 2 s | ✅ ~0.75–0.9 s |
| streaming updates every 300–700 ms | ✅ feasible with Zipformer (0.03 s per 0.32 s chunk) — Phase 4 |
| idle CPU ≈ 0 | ✅ mic closed when idle; hook only |

## Decisions

* **Primary: ivrit turbo FP16 on the Arc GPU.** Accuracy first; latency already well within target.
  INT8 is 0.1 s faster and uses ~2 GB less memory for +0.6 WER — a one-line switch if memory matters.
* **Fallback: ivrit turbo on CPU (CTranslate2).** Same accuracy, ~5 s latency; used only if the GPU path fails.
* **NPU:** same accuracy, lowest CPU use (best on battery), but ~1.7 s latency and a 3-minute first compile.
  Worth offering as a "battery saver" engine later; not the default.
* **Zipformer:** best for the live preview while speaking (Phase 4), not for the final text (weaker accuracy, weak English).
* CPU latency is ~flat (~5 s) for any length: Whisper always encodes a 30 s window; the GPU makes that cheap.

## Streaming preview (Zipformer)

Fed in 30 ms blocks at real-time pace: first partial after ~1.5 s, then an update every **~1.3 s**.
The cadence is fixed by the model (trained with 1.28 s chunks), so the 300–700 ms target is not reachable with it.
Option for later: re-run Whisper on the GPU every ~0.7 s for the preview (better text + English, but the GPU is busy
the whole time you speak).

## Local LLM polish pass (`bench/llm_bench.py`, 11 cases incl. 2 prompt-injection traps)

| Model (OpenVINO int4, Arc GPU) | License | p50 | max (40-word paragraph) | Guard accepted | Quality |
|---|---|---|---|---|---|
| Qwen3-4B | Apache-2.0 | 1.37 s | 3.3 s | 5/11 | ✗ translates Hebrew to English; once emitted Arabic/Indonesian |
| Gemma 4 E2B | Apache-2.0 | 1.14 s | 3.0 s | 10/11 | safe but barely edits; turned "איי די" into "API" (blocked by guard) |
| **Gemma 4 E4B** | Apache-2.0 | 2.16 s | 5.8 s | 11/11 | **good**: full punctuation, `get_user_by_id … ID`, "רולבק"→rollback; ignored both traps |
| DictaLM 3.0 1.7B | Apache-2.0 | — | — | — | not tested: only a "Thinking" variant at 1.7B (reasoning adds latency); needs conversion |

The guard (`dictation/llm.py`) rejects: answers, length drift, any changed number, dropped Latin tokens, foreign scripts,
and any Latin word that does not phonetically re-spell Hebrew words actually spoken ("איי די"→"API" ✗, "גט יוזר"→getUser ✓).

**Decision:** Gemma 4 E4B, **off by default** (tray → מצב עיבוד → שיפור טקסט עם AI מקומי). When on: Professional mode,
and Clean dictations of 25+ words. Never Verbatim/Developer. Real Whisper output already has punctuation, so the
real-world gain is smaller than this bench (which feeds unpunctuated text) suggests.

## Still to measure (needs real voices — contributions welcome)

FLEURS is formal read speech with no code-switching. Record the personal set (`bench/record_testset.py`,
~20 prompts: tech terms, Hebrew+English, numbers, long dictation), then:

    python bench/run_bench.py --dataset bench/data/mine --limit 0 --engines ov-gpu-ivrit-turbo,ov-gpu-ivrit-turbo-int8,zipformer
