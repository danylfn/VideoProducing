"""Отчет за всеки рън (render_report.md в папката на епизода) + защита на Mac-а от заспиване по време на рендер."""
from __future__ import annotations

import os
import platform
import re
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .utils import ffprobe_duration, fmt_ts, log

IS_MAC = platform.system() == "Darwin"
ROUTINE_SLEEP = ("Maintenance Sleep", "Sleep Service Back to Sleep")  # повторно заспиване след DarkWake


def keep_awake(long_run: bool) -> None:
    """caffeinate държи Mac-а буден, докато този процес работи (спира сам с него).
    Не помага при затворен капак на батерия -> предупреждение, ако рънът е дълъг."""
    if not IS_MAC:
        return
    subprocess.Popen(["caffeinate", "-dims", "-w", str(os.getpid())],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    batt = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    if long_run and "Battery Power" in batt:
        log.warning("🔋 Mac-ът е на батерия. Рендерът с истинско видео е часове: включете зарядното и не "
                    "затваряйте капака (иначе Mac-ът заспива и рендерът спира до събуждане).")


def sleep_events(start: float, end: float) -> list[tuple[str, int, str]]:
    """[(час, секунди, причина)] за всяко заспиване на Mac-а между start и end (от pmset -g log).
    Кратките „тъмни събуждания“ (DarkWake) по време на сън се сливат в едно заспиване."""
    if not IS_MAC:
        return []
    out = subprocess.run(["pmset", "-g", "log"], capture_output=True, text=True).stdout
    merged: list[list] = []  # [начало, край, причина]
    for m in re.finditer(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) [+-]\d{4} Sleep\s+Entering Sleep state due to "
                         r"'([^']+)'.*?(\d+) secs\s*$", out, re.M):
        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        if not start <= t <= end:
            continue
        if merged and t - merged[-1][1] < 180:
            merged[-1][1] = t + int(m.group(3))
            if m.group(2) not in merged[-1][2] and m.group(2) not in ROUTINE_SLEEP:
                merged[-1][2] += f", {m.group(2)}"
        else:
            merged.append([t, t + int(m.group(3)), m.group(2)])
    return [(f"{datetime.fromtimestamp(a):%H:%M}", int(b - a), why) for a, b, why in merged]


class RunReport:
    def __init__(self) -> None:
        self.t0 = time.time()
        self.stages: list[tuple[str, float]] = []
        self.ltx: dict = {}

    @contextmanager
    def stage(self, name: str):
        log.info("━━ %s", name)
        t = time.time()
        yield
        self.stages.append((name, time.time() - t))

    def write(self, out_dir: Path, data: dict, cfg, music: list[Path], check: tuple[bool, list[str]]) -> Path:
        end = time.time()
        wall = end - self.t0
        sleeps = sleep_events(self.t0, end)
        slept = min(sum(s for _, s, _ in sleeps), wall)
        st = data["_stats"]
        images = f"{cfg.image_engine}" + (f" ({cfg.mflux_model})" if cfg.image_engine == "mflux" else "")
        voice = f"{cfg.tts_engine}" + (f" ({cfg.kokoro_voice}, скорост {cfg.kokoro_speed})"
                                        if cfg.tts_engine == "kokoro" else "")
        motion = cfg.motion_engine + (f" (LTX-2.5, {cfg.ltx_width}x{cfg.ltx_height}, shots до "
                                      f"{cfg.ltx_max_shot_sec:g} s)" if cfg.motion_engine == "ltx" else "")
        md = [f"# Отчет от рендера: {data['project_name']}", "",
              f"- **Дата:** {datetime.fromtimestamp(self.t0):%Y-%m-%d %H:%M} → {datetime.fromtimestamp(end):%H:%M}",
              f"- **Време:** {fmt_ts(wall)} общо" + (f", от които {fmt_ts(slept)} Mac-ът е спал → "
                                                     f"**{fmt_ts(wall - slept)} реална работа**" if slept else ""),
              f"- **Проверка:** {'✅ ALL PASS' if check[0] else '❌ има FAIL (виж долу)'}", "",
              "## Настройки", "",
              f"| | |", "|---|---|",
              f"| Сценарий | {st['scenes']} сцени, {st['words']} думи, {len(data.get('shorts', []))} Shorts |",
              f"| Глас | {voice} |", f"| Картини | {images} |", f"| Движение | {motion} |",
              f"| Видео | {cfg.width}x{cfg.height} @ {cfg.fps} fps, {cfg.video_encoder} |",
              f"| Музика | {', '.join(p.stem for p in music) or 'няма'} |",
              f"| Shorts | оформление `{cfg.shorts_layout}` |", "",
              "## Етапи", "", "| Етап | Време |", "|---|---|",
              *[f"| {n} | {fmt_ts(s)} |" for n, s in self.stages], ""]
        if self.ltx:
            x = self.ltx
            md += ["## Видео (LTX)", "",
                   f"- Shots: {x['shots']} общо, **{x['generated']} генерирани сега**, "
                   f"{x['shots'] - x['generated']} от кеша"]
            if x["generated"]:
                md.append(f"- Чисто време на модела: {fmt_ts(x['compute'])} за {x['video_sec']:.0f} s видео "
                          f"(**{x['compute'] / max(x['video_sec'], 1e-6):.1f} s на секунда видео**)")
            if x.get("retried") or x.get("still_bad"):
                md.append(f"- Автоматична проверка: {x['retried']} shots с „дишащ“ zoom или скок са генерирани наново"
                          + (f"; остават: {', '.join(x['still_bad'])}" if x.get("still_bad") else ""))
            md.append("")
        md += ["## Заспиване на Mac-а", ""]
        if sleeps:
            md += ["| Час | Продължителност | Причина |", "|---|---|---|",
                   *[f"| {t} | {fmt_ts(s)} | {why} |" for t, s, why in sleeps],
                   "", "Рендерът продължава сам при събуждане. Дръжте зарядното включено и капака отворен."]
        else:
            md.append("Няма." if IS_MAC else "Не се следи извън macOS.")
        md += ["", "## Файлове", "", "| Файл | Размер | Дължина |", "|---|---|---|"]
        for f in sorted(out_dir.iterdir()):
            if f.is_file() and not f.name.startswith(".") and f.name != "render_report.md":
                dur = fmt_ts(ffprobe_duration(f)) if f.suffix == ".mp4" else ""
                md.append(f"| {f.name} | {f.stat().st_size / 1e6:.1f} MB | {dur} |")
        md += ["", "## Проверка (`modules/verify.py`)", "", "```", *check[1], "```", ""]
        path = out_dir / "render_report.md"
        path.write_text("\n".join(md))
        return path
