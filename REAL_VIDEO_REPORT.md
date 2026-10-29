# Real Video Report: from slideshow to locally generated motion

**Date:** 29 October 2026 · **Machine:** MacBook M5 Pro, 48 GB · **Episode:** `darius_hand_of_noxus` (66 scenes, 11:04)

## 1. Result

The pipeline now produces real video, not a slideshow. Every scene is animated by a local video model, LTX-2.5, at a native 30 fps, starting from that scene's Flux image. Everything runs on the Mac and no paid service is used.

| | |
|---|---|
| **Finished episode** | [output/darius_hand_of_noxus/](output/darius_hand_of_noxus/): `Master.mp4` (1920×1080, 30 fps, 11:04, -14.1 LUFS, with music), 6 Shorts (29–51 s, 1080×1920, new `zoom` layout), 3 thumbnail variants, 2 extra title variants, SRT, 8 chapters, music credits in the description |
| **Time for one 11-minute episode** | **~9 hours** of compute: video 8.0 h, images 53 min, everything else ~10 min. Your limit was 10 h. |
| **Checks** | `tools/verify_output.py`: **ALL PASS**. No frozen stretches in 11 minutes. Shot joins are smooth after a fix (§6). |

## 2. Goal and constraints

Your brief: *"Everything local, no paid models. A smooth video, not a slideshow with zooms. An 11-minute video must not take more than 10 hours. Look through all the variants, choose the best, and ask me before implementing."*

I asked before implementing, and you approved LTX-2.5.

## 3. Research: which model

| Option | Verdict |
|---|---|
| **LTX-2.5 22B, distilled** (Lightricks, MLX port `ltx-2-mlx`) | **Chosen.** Image-to-video, native 30 fps, runs on Apple MLX with 8-bit weights, ~42 s of compute per second of video. That fits 11 minutes into ~8 h. Free under $10M annual revenue. |
| LTX-2.3 | Older version of the same family; 2.5 is better at the same speed. |
| Wan 2.2 (5B / 14B / Turbo, FastWan) | Good quality, but much slower on a Mac and not native 30 fps (needs interpolation). An 11-minute episode would go well over 10 h. |
| CogVideoX, Wan 2.1 1.3B | Fast enough, but low resolution, low frame rate and visibly weaker quality. |
| HunyuanVideo | Its license excludes the EU, so it can't be used from Bulgaria. |
| Frame interpolation / 2.5D parallax | Smooth, but still the same still image; not real motion. |

## 4. Benchmark before integration

Measured on this Mac with the 8-bit pack, before writing the pipeline code:

| Test | Result |
|---|---|
| 145 frames (4.8 s) | 208.6 s → **43.2 s per second of video**, peak memory 22.2 GB |
| 353 frames (11.8 s) | 599.3 s → 50.9 s per second. Attention cost grows quadratically with length. |

**Conclusion:** split each scene into shots of up to ~5 s (`LTX_MAX_SHOT_SEC=5`). Each shot continues from the last frame of the previous one.

## 5. Implementation

| File | What it does |
|---|---|
| [modules/video_engine.py](modules/video_engine.py) | New. Splits scenes into shots on LTX's 8k+1 frame grid, builds prompts (`video_prompt` + characters + style), and caches every shot by hash. Then joins the shots, cuts each scene to the exact frame count of the timeline and scales it to 1920×1080. Voice, subtitles and Shorts stay frame-exact. |
| [tools/ltx_worker.py](tools/ltx_worker.py) | New. Runs in a separate environment (`.venv-ltx`). Loads the model once for all scenes and reports progress for every shot. Runs offline (`HF_HUB_OFFLINE=1`), so it never downloads the full 75 GB pack. |
| [tools/verify_output.py](tools/verify_output.py) | New. Automatic checks of a finished episode: format, duration, A/V sync, loudness, Shorts, SRT, chapters, thumbnail. |
| [requirements-ltx.txt](requirements-ltx.txt) | The LTX environment, pinned to a known commit. |
| `config.py`, `run_pipeline.py`, `.env.example` | `MOTION_ENGINE=ltx\|kenburns` (`--motion`) and the `LTX_*` settings. `--motion kenburns` still produces the old fast version. |
| `script.json` + `claude_prompt.md` | A hand-written `video_prompt` for each of the 66 scenes: what moves and how the camera moves. Claude now writes these by default. |
| `modules/validator.py` | Checks `video_prompt` and warns when the LTX engine is on but prompts are missing. |

Model: `dgrauet/ltx-2.5-mlx-q8`. This is a gated repo; you accepted the license on Hugging Face. Only the needed 42 GB subset is downloaded; the dev transformer and LoRA files are skipped.

## 6. The full render

| | |
|---|---|
| Shots | 176, totalling 20,696 frames (690 s of generated video for 664.9 s of episode) |
| Pure compute time | **8.0 h**, averaging ~42 s per second of video (38–48 s across the shots) |
| Wall-clock time | 11.3 h (08:24 → 19:43). About 3.3 h of that the Mac was asleep (§8). |
| Errors | none. After each sleep the render continued with nothing lost. |

### Bug found during verification and fixed

After the render I measured the motion at all 110 shot joins and found a jump at most of them.

- **Cause:** LTX only generates lengths of 8k+1 frames, so each shot was 1–7 frames longer than needed. Conform cut that excess off every shot. The next shot, however, continues from the last *generated* frame, so the cut frames were skipped at each join.
- **Why it slipped through earlier:** the earlier integration test used scene 3, which needed 112 of 113 frames. It skipped only one frame, so the test looked smooth.
- **Fix** in [modules/video_engine.py](modules/video_engine.py): shots are now used whole, and only the end of each scene's last shot is cut. No re-render was needed, because the raw shots are cached; re-joining all 66 scenes took ~1 minute.

| Motion at the join vs. the motion around it | Before | After |
|---|---|---|
| Median | 3.24× | **1.07×** |
| Joins above 2× | 82 of 110 | 6 of 110 |

All 6 remaining joins are in nearly static scenes, where a tiny absolute change shows up as a high ratio. I looked at the worst one (scene 39) frame by frame and there is no visible break.

## 7. View-growth features (done during the render, without stopping it)

1. **Music bed.** All tracks in `music/` are shuffled per project and chained with 3 s crossfades, then ducked under the voice. CC-BY credits go into the description automatically. There are 5 Kevin MacLeod CC-BY 4.0 tracks; this episode uses Oppressive Gloom, The Descent, Hero Down and Majestic Hills.
2. **A/B testing.** 3 thumbnail variants (Flux image + text, with a stronger contrast/colour pass) and 2 extra titles, for YouTube Studio → *Test & Compare*. `metadata.txt` has an "A/B ТЕСТ" block.
3. **More Shorts.** 6 instead of 3, in the new `zoom` layout (4:5 crop over a blurred background, filling ~70% of the screen). Shorts 1 was 61.8 s before; now every Short is under 60 s.
4. **Retention prompt.** `claude_prompt.md` now asks for a cold open, open loops, originality, 1300–1400 words, 5–6 Shorts with hooks and loops, title variants and 3 thumbnails. `words_per_minute` was set to 136, the measured Kokoro speed, so length estimates are correct.

The README has new sections 15 (YouTube/TikTok growth) and 16 (policies: YouTube's "inauthentic content" rules, AI-content labels, TikTok's AI slider).

## 8. Problems and how they were solved

| Problem | Fix |
|---|---|
| The LTX repo was gated | You accepted the license; the mirror was not used. |
| The loader wanted the whole 75 GB pack | Pre-downloaded only the needed subset; the worker runs offline. |
| Mac slept with the lid closed on battery, then a *Thermal Emergency Sleep* | Plugged in, lid open, on a hard surface. `caffeinate` prevents only idle sleep, not lid-closed sleep on battery. Each time the render resumed with nothing lost. |
| Jumps at shot joins | Fixed in conform, without a re-render (§6). |
| The running render had loaded the old code (no music, 3 Shorts) | A short re-run after it finished (~3.5 min): every shot came from cache, and only the thumbnails, music, master and Shorts were produced. |

## 9. Timing against the 10-hour limit

| Step | Time |
|---|---|
| Voice (Kokoro) | ~1.5 min |
| 66 images (FLUX.2-klein) | ~53 min |
| 3 thumbnails | ~1.5 min |
| Video (LTX-2.5, 176 shots) | ~8.0 h |
| Joining, master, 6 Shorts | ~3 min |
| **Total** | **~9 h** ✅ (limit 10 h) |

The limit holds only while the Mac stays awake: on AC power, lid open, not overheating.

## 10. How to run it yourself

```bash
cd LoL_Video_Pipeline && source .venv/bin/activate
caffeinate -dims python run_pipeline.py --input script.json
python tools/verify_output.py output/<project>
```

`MOTION_ENGINE=ltx` and `MUSIC_PATH=music` are already set in `.env`. For a quick draft without real video, add `--motion kenburns`.

What the pipeline does **not** do on its own (these were done by me in this session): visual review of the result, README updates, and reports like this one.

## 11. Open decisions

- The Ken Burns version of the episode was deleted on request (30 October 2026).
- Keep-awake protection and an automatic per-episode `render_report.md` were added afterwards (see README §5).
- **Possible next upgrades** (each needs your approval): test another voice (e.g. Chatterbox), YouTube auto-upload via the API, an analytics feedback loop, 4K upload.
- Two empty folders from the earlier model search (`models--mlx-community--ltx-2.5-mlx-*` in `~/.cache/huggingface/hub`, a few KB) can be deleted; they do no harm.
