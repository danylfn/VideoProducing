"""Проверка на готов епизод: формат, синхрон, сила на звука, субтитри, глави, thumbnail.
Пуска се автоматично в края на всеки рън (резултатът е в render_report.md) и ръчно с tools/verify_output.py."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from PIL import Image


def probe(p: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def loudness(p: Path) -> tuple[float, float | None]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-map", "0:a", "-af",
                        "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)
    return float(i[-1]), (float(tp[-1]) if tp else None)


def verify(out: Path) -> tuple[bool, list[str]]:
    """Връща (всичко наред?, редове за показване)."""
    lines: list[str] = []
    ok = True

    def check(cond, msg: str) -> None:
        nonlocal ok
        ok &= bool(cond)
        lines.append(("  PASS " if cond else "  FAIL ") + msg)

    meta = json.loads((out / "metadata.json").read_text())
    for f in sorted(out.glob("*.mp4")):
        d = probe(f)
        v = next(s for s in d["streams"] if s["codec_type"] == "video")
        a = next(s for s in d["streams"] if s["codec_type"] == "audio")
        vdur, adur = float(v["duration"]), float(a["duration"])
        num, den = map(int, v["r_frame_rate"].split("/"))
        fps = num / den
        lufs, tp = loudness(f)
        lines.append(f"{f.name}: {v['width']}x{v['height']} {v['codec_name']} {fps:.2f}fps | {a['codec_name']} "
                     f"{a['sample_rate']}Hz | video {vdur:.3f}s audio {adur:.3f}s | {lufs} LUFS, TP {tp} dBFS")
        master = f.name.startswith("Master")
        if master:
            check(v["width"] * 9 == v["height"] * 16, "16:9")
            check(abs(vdur - meta["master"]["duration"]) < 0.05, f"дължина = времевата линия ({meta['master']['duration']}s)")
        else:
            check((v["width"], v["height"]) == (1080, 1920), "1080x1920")
            sh = next((s for s in meta["shorts"] if s["file"] == f.name), None)
            check(sh and abs(vdur - sh["duration"]) < 0.1,
                  f"дължина = метаданните ({sh['duration']}s)" if sh else "липсва в metadata.json (стар файл?)")
        check(abs(fps - 30) < 0.01 and v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p", "H.264 yuv420p 30 fps")
        check(a["codec_name"] == "aac" and a["sample_rate"] == "48000", "AAC 48 kHz")
        check(abs(vdur - adur) < 0.1, f"видео = аудио (разлика {abs(vdur - adur) * 1000:.0f} ms)")
        if master and adur > 15:  # loudnorm е неточен при много къси клипове
            check(abs(lufs + 14) <= 1.0, "сила на звука ≈ -14 LUFS")

    srt = (out / "Master.srt").read_text()
    cues = re.findall(r"(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})", srt)
    sec = lambda s: int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:8]) + int(s[9:]) / 1000
    mono = all(sec(a) < sec(b) for a, b in cues) and all(sec(cues[i][1]) <= sec(cues[i + 1][0])
                                                         for i in range(len(cues) - 1))
    check(cues and mono, f"SRT: {len(cues)} реда, подредени, без застъпване")
    ch = meta["master"]["chapters"]
    check(not ch or ch[0]["t"] == "0:00", f"глави: {len(ch)}, първата е 0:00")
    check("{{CHAPTERS}}" not in meta["master"]["description"], "{{CHAPTERS}} е попълнено")
    check(Image.open(out / "thumbnail.jpg").size == (1280, 720), "thumbnail 1280x720")
    lines.append("ALL PASS" if ok else "SOME CHECKS FAILED")
    return ok, lines
