#!/usr/bin/env python3
"""
LoL Lore Video Pipeline – главен скрипт.

  python3 run_pipeline.py --input script.json              # пълен продукционен рън
  python3 run_pipeline.py --input script.json --dry-run    # безплатен тест (placeholder картини + macOS глас)
  python3 run_pipeline.py --input script.json --check      # само валидация + оценка на разходите
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from config import Config
from modules.audio_engine import generate_audio
from modules.image_engine import generate_images, generate_thumbnail_images
from modules.master_builder import build_master_audio, concat_clips, render_master, render_scene_clips
from modules.metadata_engine import write_metadata
from modules.pronunciation import Lexicon, load_global, report
from modules.shorts_clipper import build_shorts
from modules.subtitles import write_master_subs
from modules.timeline import build_timeline
from modules.utils import check_dependencies, ensure_dir, fmt_ts, log, setup_logging
from modules.validator import ScriptError, load_script
from modules.video_engine import render_motion_clips


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LoL Lore -> 10-мин YouTube видео + 3 Shorts")
    p.add_argument("--input", "-i", default="script.json", type=Path)
    p.add_argument("--tts", choices=["kokoro", "say", "silent", "elevenlabs"])
    p.add_argument("--images", choices=["mflux", "comfyui", "placeholder", "fal", "replicate"])
    p.add_argument("--motion", choices=["kenburns", "ltx"], help="kenburns = картина + zoom/pan, ltx = истинско видео")
    p.add_argument("--dry-run", action="store_true", help="placeholder картини + macOS 'say' (без API разходи)")
    p.add_argument("--check", action="store_true", help="само валидация и оценка")
    p.add_argument("--preview", metavar="ТЕКСТ", help="озвучава само този текст (за настройка на глас/произношение)")
    p.add_argument("--res", choices=["1080", "4k"])
    p.add_argument("--layout", choices=["zoom", "blur", "crop"], help="оформление на Shorts")
    p.add_argument("--encoder", help="напр. h264_videotoolbox | libx264 | hevc_videotoolbox")
    p.add_argument("--music", help="файл или папка с фонова музика")
    p.add_argument("--no-subs", action="store_true")
    p.add_argument("--no-shorts", action="store_true")
    p.add_argument("--force", action="store_true", help="игнорира кеша и генерира всичко наново")
    p.add_argument("--clean", action="store_true", help="изтрива междинните файлове след успешен рън")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    a = parse_args()
    setup_logging(a.verbose)
    cfg = Config()
    if a.dry_run:
        cfg.tts_engine = "say" if platform.system() == "Darwin" else "silent"
        cfg.image_engine = "placeholder"
        cfg.motion_engine = "kenburns"
    if a.motion:
        cfg.motion_engine = a.motion
    if a.tts:
        cfg.tts_engine = a.tts
    if a.images:
        cfg.image_engine = a.images
    if a.res:
        cfg.resolution = a.res
    if a.layout:
        cfg.shorts_layout = a.layout
    if a.encoder:
        cfg.video_encoder = a.encoder
    if a.music:
        cfg.music_path = a.music
    if a.no_subs:
        cfg.burn_subs_master = cfg.burn_subs_shorts = False
    cfg.finalize()

    if a.preview:
        return preview_voice(a.preview, cfg, a.input)

    try:
        data = load_script(a.input, cfg)
    except ScriptError as e:
        log.error("❌ %s", e)
        return 2

    st = data["_stats"]
    scenes = data["youtube_master"]["script"]
    log.info("📜 %s: %d сцени, %d думи, ~%.1f мин, %d shorts", data["project_name"], st["scenes"],
             st["words"], st["est_minutes"], len(data.get("shorts", [])))
    log.info("   TTS: %s (%d символа)  |  Картини: %s (%d бр.)  |  Движение: %s  |  %dx%d, %s",
             cfg.tts_engine, st["chars"], cfg.image_engine, st["scenes"], cfg.motion_engine, cfg.width, cfg.height,
             cfg.video_encoder)
    lexicon = Lexicon.build(load_global(cfg.pronunciations_file), data["pronunciations"])
    report(lexicon, [s["narration"] for s in scenes])
    if data["characters"]:
        log.info("🧍 Герои: %s", ", ".join(data["characters"]))
    if a.check:
        return 0

    check_dependencies(cfg)
    t_start = time.time()
    out_dir = ensure_dir(cfg.output_dir / data["project_name"])
    work = ensure_dir(out_dir / "_work")

    log.info("━━ [1/6] Глас")
    audio = generate_audio(scenes, work, cfg, a.force, lexicon)

    log.info("━━ [2/6] Изображения")
    images = generate_images(scenes, work, cfg, a.force, data["characters"])
    thumb_images = generate_thumbnail_images(data, work, cfg, a.force)

    log.info("━━ [3/6] Времева линия + аудио микс")
    tl = build_timeline(scenes, audio, work, cfg)
    master_audio, music = build_master_audio(tl, work, cfg, seed=data["project_name"])
    log.info("   Обща дължина: %s", fmt_ts(tl["duration"]))

    if cfg.motion_engine == "ltx":
        log.info("━━ [4/6] Видео клипове (LTX-2.5, %dx%d @ %d fps)", cfg.ltx_width, cfg.ltx_height, cfg.fps)
        clips = render_motion_clips(scenes, tl, images, work, cfg, data["characters"], a.force)
    else:
        log.info("━━ [4/6] Клипове с Ken Burns")
        clips = render_scene_clips(scenes, tl, images, work, cfg, a.force)

    log.info("━━ [5/6] Рендер на мастър видеото")
    words = [w for t in tl["scenes"] for w in t["words"]]
    ass = work / "master.ass"
    write_master_subs(words, ass, out_dir / "Master.srt", cfg)
    video = concat_clips(clips, work / "master_video.mp4")
    render_master(video, master_audio, ass if cfg.burn_subs_master else None, out_dir / "Master.mp4", cfg)
    log.info("🎬 Master.mp4 готов")

    shorts = []
    if not a.no_shorts and data.get("shorts"):
        log.info("━━ [6/6] Shorts (%s)", cfg.shorts_layout)
        shorts = build_shorts(data, tl, clips, master_audio, work, out_dir, cfg)

    write_metadata(data, scenes, tl, shorts, images, out_dir, cfg, music=music, thumb_images=thumb_images)
    if a.clean:
        shutil.rmtree(work, ignore_errors=True)

    log.info("✅ Готово за %.0fs -> %s", time.time() - t_start, out_dir.resolve())
    for f in sorted(out_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            log.info("   %-16s %6.1f MB", f.name, f.stat().st_size / 1e6)
    return 0


def preview_voice(text: str, cfg, a_input: Path) -> int:
    """Бърза проба на гласа + речника, без да се пуска целият пайплайн."""
    check_dependencies(cfg)
    script_pron = {}
    if a_input.exists():  # речникът от сценария (ако има) също важи
        try:
            script_pron = load_script(a_input, cfg).get("pronunciations", {})
        except ScriptError:
            pass
    lexicon = Lexicon.build(load_global(cfg.pronunciations_file), script_pron)
    work = ensure_dir(cfg.output_dir / "_preview")
    res = generate_audio([{"scene_id": 0, "narration": text}], work, cfg, force=True, lexicon=lexicon)[0]
    out = work / "preview.wav"
    shutil.copy(res["wav"], out)
    sent = json.loads(res["wav"].with_suffix(".json").read_text())["text"]
    log.info("🔊 Към гласа: %s", sent)
    log.info("   Файл: %s (%.1fs)", out.resolve(), res["dur"])
    if shutil.which("afplay"):
        subprocess.run(["afplay", str(out)])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.warning("Прекъснато. Пуснете отново – готовите части са кеширани.")
        sys.exit(130)
    except RuntimeError as e:
        log.error("❌ %s", e)
        sys.exit(1)
