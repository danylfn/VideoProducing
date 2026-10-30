"""
Модул 4b: Истинско видео с движение (LTX-2.5 image-to-video, локално през MLX) вместо Ken Burns.

  1. Всяка сцена се разделя на 2–3 shots (modules/shots.py) с твърди cut-ове. Всеки shot тръгва от СОБСТВЕНА
     картина (с героите от библиотеката) – без верижно продължение от последния кадър, затова героите не се
     променят от shot на shot, а камерата не „диша“ (zoom навътре, после навън).
  2. Генерирането става в tools/ltx_worker.py в отделната среда .venv-ltx: моделът се зарежда веднъж за всички
     сцени. Всеки shot се кешира по хеш (промпт, картина, дължина, seed) -> прекъснат рън продължава.
  3. Всеки нов shot минава автоматична проверка (modules/shot_check.py); при „дишащ“ zoom или рязък скок се
     генерира наново с друг seed (SHOT_RETRIES пъти).
  4. Shots на сцената се режат до точния брой кадри от времевата линия, залепват се, мащабират до резолюцията на
     мастъра и получават леко филмово зърно -> останалата част от пайплайна (мастър, субтитри, Shorts) не се променя.
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
from .shot_check import check_shot
from .shots import scene_shots
from .utils import ensure_dir, fmt_ts, log, run_ffmpeg, short_hash

# Частите от модела, които distilled пайплайнът не ползва (~29.5 GB по-малко за теглене)
LTX_SKIP = ["transformer-dev.safetensors", "*distilled-lora*"]
DEFAULT_MOTION = ("The scene comes alive with natural motion: the subject breathes and shifts, wind moves cloth "
                  "and hair, smoke and embers drift, light flickers")


def _grid(frames: int) -> int:
    """Най-малкият валиден брой кадри за LTX (8k+1), който е >= frames."""
    return 8 * math.ceil(max(frames - 1, 8) / 8) + 1


def plan_episode(scenes: list[dict], timeline: dict, cfg) -> list[list[dict]]:
    """Shots за всяка сцена: [[{"image_prompt", "video_prompt", "frames"}, ...], ...]."""
    return [scene_shots(sc, t["frames"], cfg, DEFAULT_MOTION) for sc, t in zip(scenes, timeline["scenes"])]


def shot_image_items(scenes: list[dict], plan: list[list[dict]]) -> list[dict]:
    """Картините за всички shots (в реда на plan), във формата на generate_images."""
    return [{"scene_id": sc["scene_id"], "image_prompt": sh["image_prompt"],
             "name": f"scene_{sc['scene_id']:03d}_s{k + 1}",
             "label": f"Сцена {sc['scene_id']:3d} ({k + 1}/{len(shots)})"}
            for sc, shots in zip(scenes, plan) for k, sh in enumerate(shots)]


def video_prompt(text: str, cfg, characters: dict | None = None) -> str:
    chars = characters or {}
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


def _run_worker(jobs: list[dict], spec_path: Path, cfg, stats: dict) -> None:
    todo = [j for j in jobs if not Path(j["out"]).exists()]
    if not todo:
        return
    spec_path.write_text(json.dumps({"model_dir": str(_model_dir(cfg)), "low_ram": cfg.ltx_low_ram, "jobs": todo},
                                    ensure_ascii=False, indent=1))
    stats["generated"] += len(todo)
    total_frames = sum(j["frames"] for j in todo)
    log.info("🎥 LTX-2.5: %d shots за генериране (%.0f s видео при %d fps). Зареждам модела...",
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
            stats["compute"] += ev["seconds"]
            stats["video_sec"] += ev["frames"] / cfg.fps
            rate = (time.time() - t0) / done_frames
            log.info("🎥 %s: %.0fs (%.1f s/секунда видео)  |  %d/%d кадъра, остават ~%s", ev["id"], ev["seconds"],
                     ev["seconds"] / (ev["frames"] / cfg.fps), done_frames, total_frames,
                     fmt_ts(rate * (total_frames - done_frames)))
    if proc.wait() != 0:
        raise RuntimeError("LTX worker грешка:\n" + "\n".join(tail) + "\n\nПуснете отново: готовите кадри са кеширани.")


def render_motion_clips(scenes: list[dict], timeline: dict, plan: list[list[dict]], shot_images: list[list[Path]],
                        work: Path, cfg, characters: dict | None = None, force: bool = False,
                        stats: dict | None = None) -> list[Path]:
    """plan/shot_images: shots и картините им за всяка сцена (plan_episode + generate_images).
    stats (по желание) се попълва за render_report.md: shots, генерирани сега, време, повторени от проверката."""
    stats = {} if stats is None else stats
    stats.update(shots=sum(map(len, plan)), generated=0, compute=0.0, video_sec=0.0, retried=0, still_bad=[])
    if not Path(cfg.ltx_python).exists():
        raise SystemExit(f"❌ Липсва LTX средата ({cfg.ltx_python}). Инсталирайте я (README, т.2) "
                         f"или ползвайте --motion kenburns")
    mdir = ensure_dir(work / "motion")
    raw_dir, key_dir = ensure_dir(mdir / "raw"), ensure_dir(mdir / "keys")
    model_id = [cfg.ltx_model, cfg.ltx_width, cfg.ltx_height, cfg.fps]
    negative = cfg.ltx_negative.strip() or None
    shots = [(sc, k, len(sh_list), sh, img) for sc, sh_list, imgs in zip(scenes, plan, shot_images)
             for k, (sh, img) in enumerate(zip(sh_list, imgs))]

    def job(s, attempt: int) -> dict:
        sc, k, n, sh, img = s
        prompt = video_prompt(sh["video_prompt"], cfg, characters)
        seed = (int(short_hash(prompt, img.name), 16) + 7919 * attempt) % (2**31 - 1)
        gen = _grid(sh["frames"])
        key = short_hash("ltx-shot", model_id, prompt, img.name, gen, seed, negative)
        return {"id": f"Сцена {sc['scene_id']:3d} ({k + 1}/{n})" + (f" опит {attempt + 1}" if attempt else ""),
                "prompt": prompt, "negative": negative, "image": str(_keyframe(img, key_dir, cfg)), "frames": gen,
                "width": cfg.ltx_width, "height": cfg.ltx_height, "fps": cfg.fps, "seed": seed,
                "out": str(raw_dir / f"scene_{sc['scene_id']:03d}_s{k + 1}_{key}.mp4"), "key": key,
                "need": sh["frames"]}

    def bad(j: dict) -> dict | None:
        res = check_shot(Path(j["out"]))
        return None if res["ok"] else res

    attempts = [0] * len(shots)
    if force:
        for s in shots:
            Path(job(s, 0)["out"]).unlink(missing_ok=True)

    def select() -> list[dict]:
        """Текущият опит за всеки shot; прескача вече генерирани опити, които са се провалили на проверката."""
        out = []
        for i, s in enumerate(shots):
            j = job(s, attempts[i])
            while (cfg.shot_check and attempts[i] < cfg.shot_retries and Path(j["out"]).exists() and bad(j)):
                attempts[i] += 1
                j = job(s, attempts[i])
            out.append(j)
        return out

    for _ in range(cfg.shot_retries + 1):
        jobs = select()
        _run_worker(jobs, mdir / "jobs.json", cfg, stats)
        if not cfg.shot_check:
            break
        retry = [(i, j, r) for i, j in enumerate(jobs) if attempts[i] < cfg.shot_retries and (r := bad(j))]
        for i, j, r in retry:
            log.info("🔁 %s: %s -> нов опит с друг seed", j["id"], r["reason"])
            attempts[i] += 1
        stats["retried"] += len(retry)
        if not retry:
            break
    jobs = select()
    if cfg.shot_check:
        for j in jobs:
            if r := bad(j):
                log.warning("⚠️  %s: %s (остава и след %d опита)", j["id"], r["reason"], cfg.shot_retries + 1)
                stats["still_bad"].append(" ".join(j["id"].split()))

    clips, i = [], 0
    for sc, t, sh_list in zip(scenes, timeline["scenes"], plan):
        sj, i = jobs[i:i + len(sh_list)], i + len(sh_list)
        key = short_hash([s["key"] for s in sj], [s["need"] for s in sj], t["frames"], cfg.width, cfg.height,
                         cfg.fps, cfg.film_grain, "v3")
        out = mdir / f"scene_{sc['scene_id']:03d}_{key}.mp4"
        if force or not out.exists():
            inputs, chains = [], []
            for k, s in enumerate(sj):
                inputs += ["-i", s["out"]]
                chains.append(f"[{k}:v]trim=end_frame={s['need']},setpts=PTS-STARTPTS[v{k}]")
            cat = "".join(f"[v{k}]" for k in range(len(sj)))
            grain = f"noise=alls={cfg.film_grain:g}:allf=t," if cfg.film_grain > 0 else ""
            fc = (";".join(chains) + f";{cat}concat=n={len(sj)}:v=1:a=0,"
                  f"tpad=stop_mode=clone:stop=-1,scale={cfg.width}:{cfg.height}:flags=lanczos,"
                  f"unsharp=5:5:0.35:5:5:0,{grain}setsar=1,format=yuv420p[v]")
            tmp = out.with_suffix(".tmp.mp4")
            # същите параметри като Ken Burns клиповете -> безопасен concat без прекодиране
            run_ffmpeg([*inputs, "-filter_complex", fc, "-map", "[v]", "-frames:v", t["frames"], "-r", cfg.fps,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", "-an", tmp])
            tmp.replace(out)
        clips.append(out)
    return clips
