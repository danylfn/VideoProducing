#!/usr/bin/env python3
"""
Explainer Shorts: истинска 3D анимация (Blender, физически точна) + глас, субтитри и музика от пайплайна.

  python explainer/run.py explainer/specs/camshaft.json            # финален рендер (1080x1920, 60 fps, Cycles)
  python explainer/run.py explainer/specs/camshaft.json --preview  # бърза проверка на цялата анимация (540x960)

spec: {"project_name", "scene" (файл в explainer/scenes/), "title", "narration": [изречение на план, ...],
       "description", "tags", по желание "voice_speed"}. Всяко изречение = един план (камера) в сцената; cut-ът е в началото на изречението.
Прекъснат рендер продължава от последния готов кадър.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from modules.audio_engine import generate_audio  # noqa: E402
from modules.master_builder import build_master_audio  # noqa: E402
from modules.metadata_engine import music_credits  # noqa: E402
from modules.subtitles import write_short_subs  # noqa: E402
from modules.utils import ensure_dir, log, run_ffmpeg, setup_logging, short_hash, video_encoder_args  # noqa: E402
from modules.verify import loudness, probe  # noqa: E402

BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
FPS, LEAD, GAP, TAIL = 60, 0.4, 0.2, 1.2   # тишина преди, между изреченията и след края (s)


def narration(spec: dict, work: Path, cfg) -> tuple[Path, list[dict], list[float], float]:
    """Глас за всяко изречение -> един WAV, думите с общи времена, началото на всяко изречение, дължина."""
    parts = generate_audio([{"scene_id": i + 1, "narration": s} for i, s in enumerate(spec["narration"])], work, cfg)
    words, starts, t, inputs, chain = [], [], LEAD, [], []
    for k, p in enumerate(parts):
        starts.append(t)
        words += [{"w": w["w"], "s": w["s"] + t, "e": w["e"] + t} for w in p["words"]]
        inputs += ["-i", p["wav"]]
        chain.append(f"[{k}:a]adelay={int(t * 1000)}:all=1[a{k}]")
        t += p["dur"] + GAP
    total = t - GAP + TAIL
    out = work / "narration.wav"
    mix = "".join(f"[a{k}]" for k in range(len(parts)))
    run_ffmpeg([*inputs, "-filter_complex", ";".join(chain) + f";{mix}amix=inputs={len(parts)}:normalize=0,apad[o]",
                "-map", "[o]", "-t", f"{total:.3f}", "-ar", cfg.sample_rate, "-ac", 1, "-c:a", "pcm_s16le", out])
    return out, words, starts, total


def render_frames(spec: dict, frames: int, cuts: list[int], work: Path, preview: bool, samples: int) -> Path:
    scene_py = ROOT / "explainer" / "scenes" / f"{spec['scene']}.py"
    res = [540, 960] if preview else [1080, 1920]
    samples = 12 if preview else samples
    key = short_hash(scene_py.read_text(), frames, cuts, res, samples, FPS)
    out = ensure_dir(work / f"frames_{'preview' if preview else 'final'}_{key}")
    done = len(list(out.glob("f_*.png")))
    if done >= frames:
        return out
    bspec = {"frames": frames, "fps": FPS, "out": str(out), "engine": "cycles", "samples": samples, "cuts": cuts,
             "res": res}
    (work / "blender_spec.json").write_text(json.dumps(bspec, indent=1))
    log.info("🧊 Blender: %d кадъра %dx%d @ %d fps, %d samples%s", frames, *res, FPS, samples,
             f" (продължава от кадър {done + 1})" if done else "")
    t0, last = time.time(), done
    for attempt in range(1, 6):  # при срив Blender се пуска отново и продължава от последния готов кадър
        proc = subprocess.Popen([BLENDER, "-b", "-P", str(scene_py), "--", str(work / "blender_spec.json")],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        tail = []
        for line in proc.stdout:
            tail = (tail + [line.rstrip()])[-30:]
            if "Saved:" in line and "f_" in line:
                n = len(list(out.glob("f_*.png")))
                if n - last >= 60 or n == frames:
                    rate = (time.time() - t0) / max(n - done, 1)
                    log.info("   %d/%d кадъра (%.1f s/кадър, остават ~%.0f мин)", n, frames, rate,
                             rate * (frames - n) / 60)
                    last = n
        code = proc.wait()
        for f in out.glob("f_*.png"):  # празни „placeholder“ файлове от прекъснатия кадър
            if f.stat().st_size == 0:
                f.unlink()
        if code == 0 and len(list(out.glob("f_*.png"))) >= frames:
            return out
        log.warning("⚠️  Blender спря (код %s, опит %d) – продължавам от кадър %d", code, attempt,
                    len(list(out.glob("f_*.png"))) + 1)
    raise RuntimeError("Blender грешка:\n" + "\n".join(tail))


def main() -> int:
    ap = argparse.ArgumentParser(description="Explainer Short: 3D анимация + глас + субтитри")
    ap.add_argument("spec", type=Path)
    ap.add_argument("--preview", action="store_true", help="540x960, 12 samples – бърза проверка на движението")
    ap.add_argument("--samples", type=int, default=64, help="Cycles samples за финалния рендер")
    a = ap.parse_args()
    setup_logging()
    spec = json.loads(a.spec.read_text())
    cfg = Config()
    cfg.fps = FPS
    cfg.kokoro_speed = spec.get("voice_speed", cfg.kokoro_speed)  # Shorts: малко по-бързо темпо
    cfg.finalize()
    out_dir = ensure_dir(cfg.output_dir / spec["project_name"])
    work = ensure_dir(out_dir / "_work")
    t_start = time.time()

    log.info("━━ [1/4] Глас")
    wav, words, starts, total = narration(spec, work, cfg)
    frames = math.ceil(total * FPS)
    cuts = [1] + [round(s * FPS) + 1 for s in starts[1:]]
    log.info("   %.1f s, %d кадъра, планове от кадри %s", total, frames, cuts)

    log.info("━━ [2/4] 3D анимация (Blender)")
    fdir = render_frames(spec, frames, cuts, work, a.preview, a.samples)

    log.info("━━ [3/4] Аудио микс + субтитри")
    audio, music = build_master_audio({"duration": total, "narration": wav}, work, cfg, seed=spec["project_name"])
    ass = work / "short.ass"
    write_short_subs(words, total, spec["title"], ass, cfg)

    log.info("━━ [4/4] Видео")
    video = work / f"video_{fdir.name}.mp4"
    if not video.exists():
        run_ffmpeg(["-framerate", FPS, "-i", fdir / "f_%04d.png", "-frames:v", frames, "-c:v", "libx264",
                    "-preset", "slow", "-crf", 12, "-pix_fmt", "yuv420p", video])
    name = "Short_preview.mp4" if a.preview else "Short.mp4"
    run_ffmpeg(["-i", video.resolve(), "-i", audio.resolve(), "-map", "0:v", "-map", "1:a",
                "-vf", f"subtitles=filename={ass.name}", *video_encoder_args(cfg, "16M"),
                "-c:a", "aac", "-b:a", cfg.audio_bitrate, "-ar", cfg.sample_rate, "-shortest",
                (out_dir / name).resolve()], cwd=work)

    credits = music_credits(music)
    meta = [f"TITLE: {spec['title']}", "", "DESCRIPTION:", spec["description"]]
    if credits:
        meta += ["", "Music:", *credits]
    meta += ["", "TAGS: " + ", ".join(spec.get("tags", []))]
    (out_dir / "metadata.txt").write_text("\n".join(meta) + "\n")

    d = probe(out_dir / name)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    num, den = map(int, v["r_frame_rate"].split("/"))
    lufs, tp = loudness(out_dir / name)
    log.info("✅ %s: %sx%s @ %.0f fps, %.1f s, %.1f LUFS  (%.0f s)  -> %s", name, v["width"], v["height"], num / den,
             float(v["duration"]), lufs, time.time() - t_start, (out_dir / name).resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
