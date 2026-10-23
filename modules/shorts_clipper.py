"""
Модул 5: Вертикални Shorts/Reels/TikTok (1080x1920).
Подобрение спрямо оригиналната спецификация: не режем готовото мастър видео (неточно рязане по keyframe-и
и вече изпечени хоризонтални субтитри), а сглобяваме Short-а директно от чистите клипове на сцените
-> кадрово точни граници + субтитри, оразмерени специално за вертикален екран.
"""
from __future__ import annotations

from pathlib import Path

from .master_builder import concat_clips
from .subtitles import write_short_subs
from .utils import (ensure_dir, file_sig, log, run_ffmpeg, short_hash, stamp_ok, stamp_write,
                    video_encoder_args)


def _layout_filter(cfg, subs: str | None) -> str:
    W, H = cfg.short_width, cfg.short_height
    if cfg.shorts_layout == "crop":
        chain = f"[0:v]crop=ih*{W}/{H}:ih,scale={W}:{H}:flags=lanczos,setsar=1"
    else:  # blur: замъглен фон + оригиналният 16:9 кадър в центъра
        chain = (f"[0:v]split=2[bg][fg];"
                 f"[bg]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
                 f"boxblur=12:2,scale={W}:{H},eq=brightness=-0.10:saturation=1.15[bgb];"
                 f"[fg]scale={W}:-2:flags=lanczos[fgs];"
                 f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if subs:
        chain += f",subtitles=filename={subs}"
    return chain + ",format=yuv420p[v]"


def build_shorts(data: dict, timeline: dict, clips: list[Path], master_audio: Path,
                 work: Path, out_dir: Path, cfg) -> list[dict]:
    sdir = ensure_dir(work / "shorts")
    idx, results = data["_index"], []
    for n, sh in enumerate(data.get("shorts", []), 1):
        i0, i1 = idx[sh["start_scene_id"]], idx[sh["end_scene_id"]]
        t0, t1 = timeline["scenes"][i0]["start"], timeline["scenes"][i1]["end"]
        dur = t1 - t0
        sid = sh.get("short_id", n)

        vid = concat_clips(clips[i0:i1 + 1], sdir / f"short_{sid:02d}_video.mp4")
        words = [{"w": w["w"], "s": w["s"] - t0, "e": w["e"] - t0}
                 for t in timeline["scenes"][i0:i1 + 1] for w in t["words"]]
        ass = None
        if cfg.burn_subs_shorts:
            ass = sdir / f"short_{sid:02d}.ass"
            write_short_subs(words, dur, sh.get("title", ""), ass, cfg)

        out = out_dir / f"Short_{sid:02d}.mp4"
        af = f"afade=t=in:d=0.08,afade=t=out:st={max(0, dur - 0.5):.3f}:d=0.5"
        fc = _layout_filter(cfg, ass.name if ass else None)
        key = short_hash(file_sig(vid), file_sig(master_audio), t0, dur, fc, ass.read_text() if ass else None,
                         video_encoder_args(cfg, cfg.shorts_bitrate))
        results.append({"file": out.name, "title": sh.get("title", ""), "description": sh.get("description", ""),
                        "duration": round(dur, 2), "scenes": [sh["start_scene_id"], sh["end_scene_id"]]})
        if stamp_ok(out, key):
            log.info("   (кеш) %s е актуален", out.name)
            continue
        run_ffmpeg(["-i", vid.resolve(), "-ss", f"{t0:.4f}", "-t", f"{dur:.4f}", "-i", master_audio.resolve(),
                    "-filter_complex", fc,
                    "-map", "[v]", "-map", "1:a", "-af", af,
                    *video_encoder_args(cfg, cfg.shorts_bitrate),
                    "-c:a", "aac", "-b:a", cfg.audio_bitrate, "-ar", cfg.sample_rate, "-shortest",
                    out.resolve()], cwd=sdir)
        stamp_write(out, key)
        log.info("📱 %s готов (%.1fs)%s", out.name, dur,
                 "  ⚠️ над лимита" if dur > cfg.shorts_max_sec else "")
    return results
