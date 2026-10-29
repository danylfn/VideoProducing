"""
Модул 4b: Истинско видео с движение (LTX-2.5 image-to-video, локално през MLX) вместо Ken Burns.

  1. Всяка сцена се разделя на кадри (shots) с дължина до LTX_MAX_SHOT_SEC; първият тръгва от картината на
     сцената, следващите продължават от последния кадър на предишния.
  2. Генерирането става в tools/ltx_worker.py в отделната среда .venv-ltx: моделът се зарежда веднъж за всички
     сцени. Всеки кадър се кешира по хеш (промпт, картина, дължина, seed) -> прекъснат рън продължава.
  3. Кадрите на сцената се залепват цели (без дублирания първи кадър на продълженията), последният се реже до
     точния брой кадри от времевата линия и всичко се мащабира до
     резолюцията на мастъра -> останалата част от пайплайна (мастър, субтитри, Shorts) не се променя.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections import deque
from pathlib import Path

from PIL import Image, ImageOps

from .image_engine import CHAR_REF
from .utils import ensure_dir, fmt_ts, log, run_ffmpeg, short_hash

# Частите от модела, които distilled пайплайнът не ползва (~29.5 GB по-малко за теглене)
LTX_SKIP = ["transformer-dev.safetensors", "*distilled-lora*"]
DEFAULT_MOTION = ("The scene comes alive with subtle natural motion: wind moving cloth and hair, drifting smoke and "
                  "embers, flickering light, slow cinematic camera push-in")


def _grid(frames: int) -> int:
    """Най-малкият валиден брой кадри за LTX (8k+1), който е >= frames."""
    return 8 * math.ceil(max(frames - 1, 8) / 8) + 1


def plan_shots(frames: int, max_frames: int) -> list[tuple[int, int]]:
    """Връща [(нужни кадри, кадри за генериране)] за всеки shot на сцената.
    Shot k>0 започва с последния кадър на предишния, затова той се генерира с 1 кадър повече и се изрязва.
    При залепването shot-овете се ползват изцяло (иначе на шева се прескачат кадри); реже се само краят на последния."""
    n = 1
    while True:
        base, extra = divmod(frames, n)
        need = [base + (1 if k < extra else 0) for k in range(n)]
        plan = [(nk, _grid(nk + (1 if k else 0))) for k, nk in enumerate(need)]
        if all(g <= max_frames for _, g in plan) or n >= frames:
            return plan
        n += 1


def video_prompt(scene: dict, cfg, characters: dict | None = None) -> str:
    chars = characters or {}
    text = scene.get("video_prompt") or f"{scene['image_prompt'].rstrip(' ,.')}. {DEFAULT_MOTION}"
    text = " ".join(text.split()).rstrip(" ,.")
    text = CHAR_REF.sub(lambda m: chars.get(m.group(1).lower(), m.group(1).replace("_", " ")), text)
    if cfg.ltx_style and cfg.ltx_style.lower()[:20] not in text.lower():
        text = f"{text}. {cfg.ltx_style}"
    return text + "."


def _model_dir(cfg) -> Path:
    local = Path(cfg.ltx_model).expanduser()
    if local.exists():
        return local
    from huggingface_hub import snapshot_download
    try:
        return Path(snapshot_download(cfg.ltx_model, ignore_patterns=LTX_SKIP, local_files_only=True))
    except Exception:
        log.info("📥 Тегля LTX модела %s (еднократно, ~45 GB)...", cfg.ltx_model)
        return Path(snapshot_download(cfg.ltx_model, ignore_patterns=LTX_SKIP))


def _keyframe(img: Path, out_dir: Path, cfg) -> Path:
    out = out_dir / f"{img.stem}_{cfg.ltx_width}x{cfg.ltx_height}.png"
    if not out.exists():
        im = ImageOps.fit(Image.open(img).convert("RGB"), (cfg.ltx_width, cfg.ltx_height), Image.LANCZOS)
        im.save(out)
    return out


def _run_worker(spec: dict, spec_path: Path, cfg) -> None:
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1))
    todo = [j for j in spec["jobs"] if not Path(j["out"]).exists()]
    if not todo:
        return
    total_frames = sum(j["frames"] for j in todo)
    log.info("🎥 LTX-2.5: %d кадъра за генериране (%.0f s видео при %d fps). Зареждам модела...",
             len(todo), total_frames / cfg.fps, cfg.fps)
    env = dict(os.environ, HF_HUB_OFFLINE="1")  # моделът е локален: никога не тегли целия пакет
    proc = subprocess.Popen([str(cfg.ltx_python), str(Path(__file__).resolve().parents[1] / "tools/ltx_worker.py"),
                             str(spec_path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    tail: deque[str] = deque(maxlen=40)
    done_frames, t0 = 0, time.time()
    for line in proc.stdout:
        line = line.rstrip()
        if not line.startswith("@@LTX "):
            tail.append(line)
            log.debug("ltx: %s", line)
            continue
        ev = json.loads(line[6:])
        if ev["event"] == "done":
            done_frames += ev["frames"]
            rate = (time.time() - t0) / done_frames
            log.info("🎥 %s: %.0fs (%.1f s/секунда видео)  |  %d/%d кадъра, остават ~%s", ev["id"], ev["seconds"],
                     ev["seconds"] / (ev["frames"] / cfg.fps), done_frames, total_frames,
                     fmt_ts(rate * (total_frames - done_frames)))
    if proc.wait() != 0:
        raise RuntimeError("LTX worker грешка:\n" + "\n".join(tail) + "\n\nПуснете отново: готовите кадри са кеширани.")


def render_motion_clips(scenes: list[dict], timeline: dict, images: list[Path], work: Path, cfg,
                        characters: dict | None = None, force: bool = False) -> list[Path]:
    if not Path(cfg.ltx_python).exists():
        raise SystemExit(f"❌ Липсва LTX средата ({cfg.ltx_python}). Инсталирайте я (README, т.2) "
                         f"или ползвайте --motion kenburns")
    mdir = ensure_dir(work / "motion")
    raw_dir, key_dir = ensure_dir(mdir / "raw"), ensure_dir(mdir / "keys")
    max_frames = 8 * max(1, int((cfg.ltx_max_shot_sec * cfg.fps - 1) // 8)) + 1  # най-дългият 8k+1 <= лимита
    model_id = [cfg.ltx_model, cfg.ltx_width, cfg.ltx_height, cfg.fps]

    jobs, per_scene = [], []
    for sc, t, img in zip(scenes, timeline["scenes"], images):
        prompt = video_prompt(sc, cfg, characters)
        key_img = _keyframe(img, key_dir, cfg)
        seed = int(short_hash(prompt, img.name), 16) % (2**31 - 1)
        shots, prev = [], None
        for k, (need, gen) in enumerate(plan_shots(t["frames"], max_frames)):
            shot_seed = (seed + k) % (2**31 - 1)
            key = short_hash("ltx", model_id, prompt, img.name, prev and prev["key"], gen, shot_seed)
            out = raw_dir / f"scene_{sc['scene_id']:03d}_s{k + 1}_{key}.mp4"
            if force and out.exists():
                out.unlink()
            job = {"id": f"Сцена {sc['scene_id']:3d}" + (f" ({k + 1})" if k or need < t["frames"] else ""),
                   "prompt": prompt, "image": None if prev else str(key_img), "image_from": prev and prev["out"],
                   "frames": gen, "width": cfg.ltx_width, "height": cfg.ltx_height, "fps": cfg.fps,
                   "seed": shot_seed, "out": str(out), "key": key, "need": need}
            jobs.append(job)
            shots.append(job)
            prev = job
        per_scene.append((sc, t, shots))

    _run_worker({"model_dir": str(_model_dir(cfg)), "low_ram": cfg.ltx_low_ram, "jobs": jobs},
                mdir / "jobs.json", cfg)

    clips = []
    for sc, t, shots in per_scene:
        key = short_hash([s["key"] for s in shots], t["frames"], cfg.width, cfg.height, cfg.fps, "v2")
        out = mdir / f"scene_{sc['scene_id']:03d}_{key}.mp4"
        if force or not out.exists():
            inputs, chains = [], []
            for k, s in enumerate(shots):
                inputs += ["-i", s["out"]]
                # Shot-ът се ползва ЦЯЛ: следващият продължава от последния му генериран кадър, затова рязане
                # в средата дава скок. Излишъкът се реже само от края на последния shot (-frames:v отдолу).
                skip = 1 if k else 0  # първият кадър на продължението = последният на предишния shot
                chains.append(f"[{k}:v]trim=start_frame={skip},setpts=PTS-STARTPTS[v{k}]")
            cat = "".join(f"[v{k}]" for k in range(len(shots)))
            fc = (";".join(chains) + f";{cat}concat=n={len(shots)}:v=1:a=0,"
                  f"tpad=stop_mode=clone:stop=-1,scale={cfg.width}:{cfg.height}:flags=lanczos,"
                  f"unsharp=5:5:0.35:5:5:0,setsar=1,format=yuv420p[v]")
            tmp = out.with_suffix(".tmp.mp4")
            # същите параметри като Ken Burns клиповете -> безопасен concat без прекодиране
            run_ffmpeg([*inputs, "-filter_complex", fc, "-map", "[v]", "-frames:v", t["frames"], "-r", cfg.fps,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", "-an", tmp])
            tmp.replace(out)
        clips.append(out)
    return clips
