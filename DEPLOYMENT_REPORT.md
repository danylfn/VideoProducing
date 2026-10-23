# LoL Video Pipeline — Deployment & Test Report

**Date:** 2026-10-23 · **Machine:** MacBook, Apple M5 Pro (15 cores), 48 GB unified memory, macOS 26.6

**Result:** The pipeline is installed and every step in `README.md` ran successfully on this Mac. That covers the dry-run, the example with the real local models, and a real full-length episode written from `claude_prompt.md`. Four bugs blocked or degraded it on today's software versions; all four are fixed. The README's default image model (FLUX.1-schnell) now requires a free Hugging Face login. The episode was first made with an ungated model, FLUX.2-klein-4B, then re-made with schnell after you logged in. The comparison in §7 favors klein, which is now the default in `.env`.

---

## 1. What you have now

| Output | Location | Details |
|---|---|---|
| **Full episode, FLUX.2-klein images** (recommended) | [output/darius_hand_of_noxus_klein/](output/darius_hand_of_noxus_klein/) | 11:04 master (1920×1080, 925 MB), 3 Shorts (62 s / 52 s / 55 s), SRT with 200 cues, 8 YouTube chapters, thumbnail, metadata |
| Full episode, FLUX.1-schnell images | [output/darius_hand_of_noxus_schnell/](output/darius_hand_of_noxus_schnell/) | same voice, timing and subtitles; only the images differ |
| Working folder with the shared cache | [output/darius_hand_of_noxus/](output/darius_hand_of_noxus/) | latest render (schnell) plus `_work/` holding both models' images, so either version re-renders in minutes |
| README example (real models) | [output/darius_lore_ep1/](output/darius_lore_ep1/) | 1:01 master + 3 Shorts, Kokoro voice + FLUX.2-klein images |
| Episode script | [script.json](script.json) | 66 scenes, 1508 words, 8 chapters, 5 characters, 6 pronunciation entries, 3 Shorts |

Chapters from `metadata.txt`, with timestamps filled in by the pipeline:

```
0:00 The Man Who Never Retreats      5:41 The Hand of Noxus
0:44 Orphans of Basilich             7:02 Winter in the Freljord
3:24 Rise Through the Ranks          8:13 A Throne Stained Red
4:17 Blood on Dalamor Plain          9:28 The Trifarix
```

---

## 2. Installation (README §1)

| Step | What was done |
|---|---|
| Homebrew packages | `brew install ffmpeg python@3.12 espeak-ng` → FFmpeg 9.0.2, Python 3.12.15, espeak-ng 1.52.0 |
| **Extra:** `ffmpeg-full` | Homebrew's regular `ffmpeg` formula **no longer includes libass**, so it has no `subtitles` filter and subtitles would silently not be burned in. I installed `brew install ffmpeg-full` (keg-only, has libass and VideoToolbox). |
| PATH for ffmpeg-full | Rather than editing `~/.zshrc`, I appended `export PATH="/opt/homebrew/opt/ffmpeg-full/bin:$PATH"` to `.venv/bin/activate`, so `source .venv/bin/activate` picks it up automatically. |
| venv + packages | `python3.12 -m venv .venv` + `pip install -r requirements-local.txt` → kokoro 0.9.4, mflux 0.23.0, mlx 0.32.4, torch 2.14.1, Pillow 12.3.0 (pinned, see §3.1) |
| `.env` | `cp .env.example .env` (defaults unchanged) |
| Model downloads | Kokoro-82M (~330 MB) and FLUX.2-klein-4B (~15 GB) into `~/.cache/huggingface`. On its first run Kokoro also pip-installs spaCy `en_core_web_sm` (12.8 MB) into the venv by itself, so the first run needs internet even with `HF_HUB_OFFLINE`. |

Disk used: `.venv` 1.5 GB, Hugging Face cache 15 GB, outputs about 3 GB.

---

## 3. Bugs found and fixed

### 3.1 Pillow 13 crashes mflux on Python 3.12 (blocking)
- **Symptom:** `ImportError: cannot import name '_IntegralLike' from 'numbers'` whenever mflux was imported.
- **Cause:** Pillow 13.0.0's `PIL/_typing.py` relies on newer-Python features (`__lazy_modules__` and a private `numbers` name). The dry-run didn't hit it because the pipeline's own code never imports `PIL._typing`; mflux does.
- **Fix:** [requirements.txt](requirements.txt) now has `Pillow>=10.1,<13`.

### 3.2 mflux 0.23 API change (severe slowdown)
- **Cause:** New mflux removed the `Config` object; `generate_image()` now takes `num_inference_steps/height/width` directly. The old code imported `Flux1` but got `Config=None`. The first call then raised `TypeError`, and the code fell back to the `mflux-generate` CLI, which **reloads the 12 GB model for every scene**.
- **Fix:** [modules/image_engine.py](modules/image_engine.py) now imports `Flux1` and `Config` independently and passes keyword arguments when `Config` is absent. It still works with older mflux versions.

### 3.3 Loudness was −15.5 LUFS instead of −14 (quality)
- **Cause:** Single-pass `loudnorm` in dynamic mode undershoots by 1–2 LU on narration with pauses; its own JSON reported `output_i -15.47`. Two-pass linear mode didn't help, because the required +5 dB would exceed the true-peak limit, so it falls back to dynamic mode. YouTube only turns loud videos down, so this video would have played about 1.5 dB quieter than other channels.
- **Fix:** [modules/master_builder.py](modules/master_builder.py) measures the mix after `loudnorm` and applies the remaining gain through a −1.5 dB peak limiter. The helper `measure_lufs()` is in [modules/utils.py](modules/utils.py). A `"lufs-fix"` tag in the cache key makes previously cached mixes re-render.
- **Result:** the master measures −14.0 to −14.1 LUFS and the Shorts −13.9 to −14.2, with or without music.

### 3.4 FLUX.1-schnell is now a gated model on Hugging Face (blocking without login)
- **Symptom:** `GatedRepoError: 401 … Access to model black-forest-labs/FLUX.1-schnell is restricted`. The README says only `dev` needs a login; that is no longer true. The error showed up as a raw traceback, and the model load was retried once per scene.
- **Fixes in [modules/image_engine.py](modules/image_engine.py):**
  - A clear message in place of the traceback (*"… е gated модел: изпълнете 'hf auth login' и приемете лиценза на …"*), and the failure is remembered so the download isn't retried for every scene.
  - **New option `MFLUX_MODEL=flux2-klein-4b`:** FLUX.2 [klein] 4B, the official Black Forest Labs model, Apache-2.0 (commercial use OK), **no login needed**, 4 steps. It uses mflux's `Flux2Klein` class; the CLI fallback is `mflux-generate-flux2`. Schnell remains the default.

---

## 4. Test results

### 4.1 README §2: dry-run (`--dry-run`, placeholder images + macOS `say`)
Finished in **30 s**. My verification script ([verify.py](#appendix-verification-script)) checked every output file:
- Master is 16:9, H.264 High, yuv420p, 30 fps, AAC 48 kHz stereo; its duration equals the timeline to the frame. Video and audio lengths match (Δ 0–33 ms).
- Shorts are 1080×1920 and their durations match the metadata.
- SRT cues are in order and don't overlap. Chapters start at 0:00, the `{{CHAPTERS}}` placeholder is replaced, and the thumbnail is 1280×720.
- I looked at frames: subtitles are burned into the master, and the Shorts have the title overlay plus the yellow TikTok-style word highlight. The placeholder text also confirmed that `{darius}` expands to the full character description.

### 4.2 README §2: real models on the example
Kokoro (voice `am_michael`) + FLUX.2-klein-4B, 8-bit, 1920×1088. **All checks pass.**
- Kokoro returned **exact per-word timings for every scene** (`exact_timing: true`), so subtitles follow the real speech. `say` uses proportional timing, as documented.
- About **45–50 s per image**. Quality is good and the dark-fantasy style is consistent.

### 4.3 README §3: real episode
1. **Script:** I took the role of the "Claude" step and wrote [script.json](script.json) following every rule in `claude_prompt.md`. The lore follows Riot's official Darius biography (pulled from the Riot Universe API): Basilich, Cyrus's warhost, Dalamor Plain, the Varju, Boram Darkwill, the Freljord and Winter's Claw, Swain's coup, the Trifarix. Draft-stage rule violations were fixed: 10 chapters reduced to 8, and Shorts shortened to 122–139 words.
2. **`--check`:** passes with zero warnings. 66 scenes, 1508 words; 8 of 22 pronunciation entries are used.
3. **Full run:** **57.5 min total** (voice 1.5 min, images 53 min at about 48 s each, mix, clips and renders about 3 min). **All checks pass.**
4. **Character consistency** (spot-checked): young Darius, Cyrus, Draven and adult Darius each match their descriptions, and adult Darius is recognizably the same person across scenes and in the thumbnail.

### 4.4 README §4: caching and edits
| Test | Result |
|---|---|
| Identical re-run | everything from cache, **0.4 s** |
| Edited scene 14's narration and image prompt | **only** scene 14's voice, image and clip were regenerated; the master and all 3 Shorts re-rendered (their audio comes from the shared mix). **2 min 55 s instead of 57 min.** |
| Reverted the edit | the original assets were still cached, so nothing was regenerated: 1 min 48 s |
| `--force` | regenerated all 27 assets (9 voice, 9 image, 9 clip) |
| `--clean` | `_work/` removed after success, final files kept |
| Switching voice engine | the `say` and Kokoro caches sit side by side (keyed by engine) |

### 4.5 README §5: CLI flags
| Flag | Result |
|---|---|
| `--dry-run`, `--check`, `-v` | ✅ |
| `--preview "…"` (global lexicon), `-i script.json --preview` (episode lexicon), `--tts say --preview` | ✅ e.g. *"Kha'Zix … Shurima"* is sent to the voice as *"Kah-Zix … Shoo-ree-mah"*. Playback was tested through an `afplay` stand-in so no sound played on your Mac. |
| `--tts kokoro / say / silent` | ✅ (`silent` measures −91 dB) |
| `--images placeholder / mflux` | ✅ |
| `--res 4k` | ✅ 3840×2160, subtitles scaled correctly |
| `--layout blur` / `--layout crop` | ✅ both look right |
| `--music <folder>` | ✅ picked a track from the folder, music ducks under the voice, −14.2 LUFS |
| `--encoder libx264` | ✅ |
| `--no-subs` | ✅ nothing burned in, SRT still written |
| `--no-shorts` | ✅ |
| `--tts elevenlabs`, `--images fal / replicate` without keys | ✅ stop immediately with a clear "missing key in .env" message (no keys were used: the setup is self-hosted by design) |
| `--images comfyui` without a workflow / without a server | ✅ clear messages ("Липсва ComfyUI workflow…" / "ComfyUI не отговаря…") |

### 4.6 README §6.5: validator, characters and pronunciation
I fed 12 deliberately broken scripts to `--check`; each was handled as documented:

| Case | Result |
|---|---|
| ```` ```json ```` fences, extra text, "smart" quotes | ✅ cleaned up and accepted |
| Invalid JSON | ✅ error with line and column |
| Undefined `{garen}` | ✅ error |
| Name without braces (`darius on a cliff`) / unused character | ✅ warnings |
| Title over 100 characters, `<` `>` in the description | ✅ errors |
| Duplicate `scene_id`, empty narration | ✅ errors |
| Unknown `motion` | ✅ warning, chosen automatically |
| Short range that doesn't exist / start > end | ✅ errors |
| Prompt over about 256 tokens; `Xin'Zhao` missing from the lexicon | ✅ warnings |
| Missing `youtube_master`; bad thumbnail scene; missing file | ✅ |

Pronunciation unit test: possessives (`Kha'Zix's`), multi-word keys (`Jericho Swain`), IPA only for Kokoro, subtitles keep the original spelling, and a timing mismatch falls back safely. ✅

---

## 5. Remaining observations (not fixed; suggestions)

1. **The episode is 11:04, not about 10 min.** The validator assumes 150 words/min, but Kokoro at `KOKORO_SPEED=0.95` speaks about 136 words/min. Fix: set `words_per_minute` to about 136 in `config.py` (or `KOKORO_SPEED=1.0`), or aim for about 1350 words in `claude_prompt.md`. Short 1 came out 61.8 s for the same reason (only a warning; YouTube accepts up to 3 min).
2. **Occasional fake signatures or gibberish text in images.** The `negative_prompt` is only sent to ComfyUI; the distilled Flux models ignore negative prompts. Fix: review `_work/images/raw/`, delete bad images and re-run (the README's own workflow), or use ComfyUI.
3. **True peak +0.5 dBTP after AAC encoding.** The WAV mix peaks at a safe −2.6 dBTP, but FFmpeg's built-in AAC encoder adds about 3 dB of overshoot. It's minor because YouTube re-encodes the audio. Fix: encode audio with `aac_at` (Apple's encoder), or lower the limiter ceiling to −3 dB.
4. **`COMFYUI_WORKFLOW=./comfyui_workflow_api.json` in `.env.example`** resolves against the current directory, so it breaks when the script runs from elsewhere. Drop the line to use `config.py`'s project-relative default.
5. **Laptop sleep stops the run.** Closing the lid on battery suspended generation from 11:45 to 13:25 ("Clamshell Sleep"). `caffeinate` can't prevent that. For long runs, keep the lid open or the charger plugged in. Nothing is lost when it sleeps: everything resumes from cache.
6. **Suggested README updates:** `brew install ffmpeg-full` (plus the PATH line) instead of `ffmpeg`; schnell now needs `hf auth login` plus accepting the license; mention `MFLUX_MODEL=flux2-klein-4b`.

**Not tested:** ComfyUI actually generating images (not installed); ElevenLabs, fal.ai and Replicate with real keys (none configured: the setup is self-hosted); `MFLUX_MODEL=dev` (gated, non-commercial license).

---

## 6. Files changed

| File | Change |
|---|---|
| [requirements.txt](requirements.txt) | `Pillow>=10.1,<13` |
| [modules/image_engine.py](modules/image_engine.py) | mflux 0.23 API support; `flux2-klein-4b` option; clear gated-model error, no retry per scene |
| [modules/master_builder.py](modules/master_builder.py) | loudness correction to −14 LUFS, cache key tag |
| [modules/utils.py](modules/utils.py) | `measure_lufs()` helper |
| [script.json](script.json) | **new**: full episode script |
| `.venv/bin/activate` | adds ffmpeg-full to PATH |
| `.env` | **new**: copied from `.env.example`, with `MFLUX_MODEL=flux2-klein-4b` (not in git) |
| `.gitignore` | **new**: excludes `.venv/`, `.env`, `output/` |

---

## 7. FLUX.1-schnell vs FLUX.2-klein (done after the Hugging Face login)

You logged in as `ikonomovdaniel` (token saved by `hf auth login` in `~/.cache/huggingface/token`) and accepted the schnell license. The whole episode was then re-rendered with `MFLUX_MODEL=schnell`. Voice, timing and subtitles came from cache; only the images and clips were regenerated.

| | FLUX.2-klein-4B | FLUX.1-schnell |
|---|---|---|
| Download | ~16 GB, no login | 33.7 GB, needs HF login + license (took about 2.5 h at ~3.4 MB/s) |
| Speed (1920×1088, 8-bit, 4 steps) | ~48 s per image | ~56 s per image |
| Following the character descriptions | **Good.** Adult Darius has the same short grey-streaked hair, scarred face and spiked plate across scenes and in the thumbnail. | **Weaker.** In scenes 1, 30 and the thumbnail Darius gets a full beard and different hair that aren't in his description. |
| Look | Painterly, detailed | More cinematic and dramatic lighting and framing |
| Artifacts | Occasional fake signature in a corner | No signatures seen |
| Technical checks | ALL PASS | ALL PASS |

**Why klein follows the characters better:** schnell's T5 text encoder reads only about 256 tokens. With the inserted character description and the style suffix, prompts reach that limit and details like "short-cropped hair" get dropped. Klein uses a newer text encoder with a much longer context.

**Decision:** `.env` now has `MFLUX_MODEL=flux2-klein-4b`. To switch back, set `MFLUX_MODEL=schnell`; both models stay downloaded. To render either version again, set the model and run `python run_pipeline.py -i script.json`. Every image is cached per model, so switching takes only a few minutes.

---

## 8. How to run it yourself

```bash
cd ~/Downloads/MovieMakingMoney/LoL_Video_Pipeline
source .venv/bin/activate                      # also puts ffmpeg-full on PATH
python run_pipeline.py -i script.example.json --dry-run    # 30 s smoke test
python run_pipeline.py -i script.json --check              # validate a new script
python run_pipeline.py -i script.json                      # full run
```

### Appendix: verification script
`verify.py` (kept in the session scratchpad, not the project) runs ffprobe and ebur128 on every output. It checks resolution and aspect, codec, profile, pixel format, fps, AAC 48 kHz, video vs. audio length, duration vs. timeline and metadata, integrated loudness, SRT order and overlap, chapters at 0:00, the replaced `{{CHAPTERS}}` placeholder, and thumbnail size. Final result on both output folders: **ALL PASS**.
