"""
Модул 4: Сглобяване на мастър видеото.
  1. Всяка сцена -> видео клип с Ken Burns (zoompan върху супер-семплирана картина = без трептене)
  2. Клиповете се залепват без прекодиране (concat demuxer)
  3. Аудио миксът (глас + музика с ducking + loudnorm -14 LUFS) се прави отделно, като един непрекъснат WAV
  4. Финален енкод: субтитри + аудио -> Master.mp4 (h264_videotoolbox на Apple Silicon)
"""
from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .utils import (ensure_dir, file_hash, file_sig, log, measure_lufs, run_ffmpeg, short_hash, stamp_ok,
                    stamp_write, video_encoder_args)

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff"}


def _motion_for(i: int, scene: dict, prev: str | None, cfg) -> str:
    if scene.get("motion") in cfg.kb_modes:
        return scene["motion"]
    m = cfg.kb_modes[i % len(cfg.kb_modes)]
    return m if m != prev else cfg.kb_modes[(i + 1) % len(cfg.kb_modes)]


def _zoompan(mode: str, frames: int, cfg) -> str:
    n1 = max(frames - 1, 1)
    p = f"(on/{n1})"
    Z = cfg.kb_zoom
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    z, x, y = {
        "zoom_in":   (f"1+{Z - 1:.4f}*{p}", cx, cy),
        "zoom_out":  (f"{Z:.4f}-{Z - 1:.4f}*{p}", cx, cy),
        "pan_right": (f"{Z:.4f}", f"(iw-iw/zoom)*{p}", cy),
        "pan_left":  (f"{Z:.4f}", f"(iw-iw/zoom)*(1-{p})", cy),
    }[mode]
    # супер-семплиране: zoompan закръгля x/y до цели пиксели -> работим в ~4x резолюция
    ss = max(2, 7680 // cfg.width)
    return (f"scale={cfg.width * ss}:{cfg.height * ss}:flags=lanczos,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={cfg.width}x{cfg.height}:fps={cfg.fps},"
            f"setsar=1,format=yuv420p")


def render_scene_clips(scenes: list[dict], timeline: dict, images: list[Path], work: Path, cfg,
                       force: bool = False) -> list[Path]:
    cdir = ensure_dir(work / "clips")
    modes, prev = [], None
    for i, sc in enumerate(scenes):
        prev = _motion_for(i, sc, prev, cfg)
        modes.append(prev)

    def job(i: int) -> Path:
        t, img, mode = timeline["scenes"][i], images[i], modes[i]
        key = short_hash(img.name, t["frames"], mode, cfg.kb_zoom, cfg.width, cfg.fps)
        out = cdir / f"scene_{t['scene_id']:03d}_{key}.mp4"
        if force or not out.exists():
            tmp = out.with_suffix(".tmp.mp4")
            # междинен формат: libx264 с висок битрейт и еднакви параметри -> безопасен concat без прекодиране
            run_ffmpeg(["-i", img, "-vf", _zoompan(mode, t["frames"], cfg), "-frames:v", t["frames"],
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
                        "-r", cfg.fps, "-an", tmp])
            tmp.replace(out)
            log.info("🎞  Сцена %3d: клип %s (%d кадъра)", t["scene_id"], mode, t["frames"])
        return out

    with ThreadPoolExecutor(max_workers=cfg.clip_workers) as ex:
        return list(ex.map(job, range(len(scenes))))


def concat_clips(clips: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    listing = "".join(f"file '{c.resolve().as_posix()}'\n" for c in clips)
    if out.exists() and lst.exists() and lst.read_text() == listing:
        return out
    lst.write_text(listing)
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    return out


def _pick_music(cfg, seed: str) -> Path | None:
    if not cfg.music_path:
        return None
    p = Path(cfg.music_path).expanduser()
    if p.is_dir():
        tracks = sorted(f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXT)
        if not tracks:
            log.warning("В %s няма аудио файлове – продължавам без музика", p)
            return None
        return random.Random(seed).choice(tracks)
    if p.exists():
        return p
    log.warning("MUSIC_PATH=%s не съществува – продължавам без музика", p)
    return None


def build_master_audio(timeline: dict, work: Path, cfg, seed: str) -> Path:
    out = work / "master_audio.wav"
    dur = timeline["duration"]
    music = _pick_music(cfg, seed)
    key = short_hash(file_hash(timeline["narration"]), file_sig(music) if music else None,
                     cfg.music_volume, cfg.target_lufs, "lufs-fix")
    if stamp_ok(out, key):
        return out
    norm = f"loudnorm=I={cfg.target_lufs}:TP=-1.5:LRA=11,aresample={cfg.sample_rate}"
    if music:
        log.info("🎵 Фонова музика: %s", music.name)
        fade_st = max(0.0, dur - 4)
        fc = (f"[0:a]aformat=channel_layouts=stereo,asplit=2[v][sc];"
              f"[1:a]aformat=channel_layouts=stereo,aresample={cfg.sample_rate},volume={cfg.music_volume},"
              f"afade=t=in:d=2,afade=t=out:st={fade_st:.2f}:d=4[m];"
              f"[m][sc]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=450[duck];"
              f"[v][duck]amix=inputs=2:duration=first:normalize=0,{norm}[out]")
        run_ffmpeg(["-i", timeline["narration"], "-stream_loop", "-1", "-i", music,
                    "-filter_complex", fc, "-map", "[out]", "-t", f"{dur:.4f}",
                    "-ar", cfg.sample_rate, "-ac", 2, "-c:a", "pcm_s16le", out])
    else:
        run_ffmpeg(["-i", timeline["narration"], "-af", norm, "-t", f"{dur:.4f}",
                    "-ar", cfg.sample_rate, "-ac", 2, "-c:a", "pcm_s16le", out])
    # еднопасовият loudnorm (динамичен режим) остава ~1-2 LU под целта при реч с паузи -> довършваме с усилване
    fix = cfg.target_lufs - measure_lufs(out)
    if abs(fix) > 0.3:
        tmp = out.with_suffix(".tmp.wav")
        run_ffmpeg(["-i", out, "-af", f"volume={fix:.2f}dB,alimiter=limit={10 ** (-1.5 / 20):.3f}:level=false",
                    "-c:a", "pcm_s16le", tmp])
        tmp.replace(out)
    log.info("🔉 Сила на звука: %.1f LUFS (цел %.0f)", measure_lufs(out), cfg.target_lufs)
    stamp_write(out, key)
    return out


def render_master(video: Path, audio: Path, ass: Path | None, out: Path, cfg) -> Path:
    key = short_hash(file_sig(video), file_sig(audio), ass.read_text() if ass else None,
                     video_encoder_args(cfg, cfg.master_bitrate), cfg.audio_bitrate)
    if stamp_ok(out, key):
        log.info("   (кеш) Master.mp4 е актуален")
        return out
    args = ["-i", video, "-i", audio, "-map", "0:v", "-map", "1:a"]
    if ass is not None:
        # cwd = папката на .ass -> относително име, без проблеми с escaping на пътища
        args += ["-vf", f"subtitles=filename={ass.name}"]
    args += [*video_encoder_args(cfg, cfg.master_bitrate), "-c:a", "aac", "-b:a", cfg.audio_bitrate,
             "-ar", cfg.sample_rate, out.resolve()]
    run_ffmpeg([str(a) if not isinstance(a, Path) else str(a.resolve()) for a in args],
               cwd=ass.parent if ass else None)
    stamp_write(out, key)
    return out
