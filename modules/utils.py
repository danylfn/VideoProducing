"""Общи помощни функции: FFmpeg, хеширане, HTTP с retry, логове."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, TypeVar

log = logging.getLogger("pipeline")
T = TypeVar("T")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---------------------------------------------------------------- FFmpeg

def _ffmpeg_output(*args: str) -> str:
    return subprocess.run(["ffmpeg", "-hide_banner", *args],
                          capture_output=True, text=True).stdout


def check_dependencies(cfg) -> None:
    """Проверява FFmpeg/libass/енкодера и автоматично пада на libx264 при нужда."""
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"❌ Липсва {tool}. На macOS: brew install ffmpeg")
    filters = _ffmpeg_output("-filters")
    for f in ("zoompan", "loudnorm", "sidechaincompress", "boxblur"):
        if f" {f} " not in filters:
            raise SystemExit(f"❌ Вашият FFmpeg няма филтър '{f}'. Инсталирайте пълния: brew install ffmpeg")
    if " subtitles " not in filters:
        log.warning("FFmpeg е без libass -> субтитрите няма да се изпекат във видеото (SRT пак се генерира).")
        cfg.burn_subs_master = cfg.burn_subs_shorts = False
    encoders = _ffmpeg_output("-encoders")
    if f" {cfg.video_encoder} " not in encoders:
        log.warning("Енкодер %s не е наличен -> използвам libx264", cfg.video_encoder)
        cfg.video_encoder = "libx264"


def run_ffmpeg(args: list[str], cwd: Path | None = None) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)]
    log.debug("ffmpeg %s", " ".join(map(str, args)))
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"FFmpeg грешка:\n{tail}\n\nКоманда: {' '.join(map(str, cmd))}")


def ffprobe_duration(path: Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def measure_lufs(path: Path) -> float:
    """Integrated loudness (EBU R128) на аудио файл."""
    res = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128",
                          "-f", "null", "-"], capture_output=True, text=True)
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", res.stderr)
    if not found:
        raise RuntimeError(f"Не мога да измеря силата на звука на {path.name}")
    return float(found[-1])


def video_encoder_args(cfg, bitrate: str) -> list[str]:
    common = ["-pix_fmt", "yuv420p", "-r", str(cfg.fps), "-g", str(cfg.fps * 2), "-movflags", "+faststart"]
    if cfg.video_encoder == "h264_videotoolbox":
        rate = int(bitrate.rstrip("M"))
        return ["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-maxrate", f"{int(rate * 1.5)}M",
                "-bufsize", f"{rate * 2}M", "-profile:v", "high", "-allow_sw", "1", *common]
    return ["-c:v", "libx264", "-preset", cfg.x264_preset, "-crf", str(cfg.x264_crf),
            "-profile:v", "high", *common]


# ---------------------------------------------------------------- Разни

def short_hash(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode())
        h.update(b"\x00")
    return h.hexdigest()[:10]


def fmt_ts(sec: float) -> str:
    """Таймкод за YouTube глави: 0:00 / 12:34 / 1:02:03."""
    s = int(sec)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


class RetryableError(Exception):
    pass


def with_retry(fn: Callable[[], T], what: str, tries: int = 5, base_delay: float = 3.0) -> T:
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except RetryableError as e:
            if attempt == tries:
                raise RuntimeError(f"{what}: неуспех след {tries} опита: {e}") from e
            delay = base_delay * 2 ** (attempt - 1)
            log.warning("%s: %s -> нов опит след %.0fs (%d/%d)", what, e, delay, attempt, tries)
            time.sleep(delay)
    raise AssertionError("unreachable")


def check_http(resp, what: str) -> None:
    """429/5xx -> RetryableError, друг 4xx -> фатална грешка с ясно съобщение."""
    if resp.status_code < 400:
        return
    body = resp.text[:500]
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RetryableError(f"HTTP {resp.status_code}: {body}")
    raise RuntimeError(f"{what}: HTTP {resp.status_code}: {body}")


def stamp_ok(out: Path, key: str) -> bool:
    """True ако out съществува и е рендериран със същите входни данни (key)."""
    st = out.parent / f".{out.name}.key"
    return out.exists() and st.exists() and st.read_text() == key


def stamp_write(out: Path, key: str) -> None:
    (out.parent / f".{out.name}.key").write_text(key)


def file_sig(p: Path) -> list:
    st = Path(p).stat()
    return [Path(p).name, st.st_size, int(st.st_mtime)]


def file_hash(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
