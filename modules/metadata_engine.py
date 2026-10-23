"""Метаданни за качване: реални YouTube глави, metadata.txt/json, SRT, thumbnail."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from .image_engine import load_font
from .utils import fmt_ts, log


def build_chapters(scenes: list[dict], timeline: dict) -> list[tuple[float, str]]:
    ch = [(t["start"], s["chapter"].strip()) for s, t in zip(scenes, timeline["scenes"]) if s.get("chapter")]
    if not ch:
        return []
    if ch[0][0] > 0.01:
        ch.insert(0, (0.0, "Intro"))
    else:
        ch[0] = (0.0, ch[0][1])
    # YouTube: всяка глава >= 10s -> сливаме твърде къси
    merged = [ch[0]]
    for t, title in ch[1:]:
        if t - merged[-1][0] >= 10:
            merged.append((t, title))
    if timeline["duration"] - merged[-1][0] < 10 and len(merged) > 1:
        merged.pop()
    if len(merged) < 3:
        log.warning("⚠️  По-малко от 3 глави – YouTube няма да ги покаже като chapters")
    return merged


def make_description(base: str, chapters: list[tuple[float, str]]) -> str:
    block = "\n".join(f"{fmt_ts(t)} {title}" for t, title in chapters)
    if "{{CHAPTERS}}" in base:
        return base.replace("{{CHAPTERS}}", block).strip()
    return f"{base.strip()}\n\n⏱ Chapters\n{block}".strip() if block else base.strip()


def make_thumbnail(img_path: Path, text: str, out: Path, cfg) -> Path:
    W, H = 1280, 720
    img = Image.open(img_path).convert("RGB").resize((W, H), Image.LANCZOS)
    if text:
        # тъмен градиент отляво за четимост
        overlay = Image.new("L", (W, H), 0)
        od = ImageDraw.Draw(overlay)
        for x in range(W):
            od.line([(x, 0), (x, H)], fill=int(190 * max(0.0, 1 - x / (W * 0.75))))
        img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, overlay)
        d = ImageDraw.Draw(img)
        words, lines = text.upper().split(), []
        for size in range(150, 50, -6):
            font = load_font(cfg, size)
            lines, cur = [], ""
            for w in words:
                trial = f"{cur} {w}".strip()
                if d.textlength(trial, font=font) <= W * 0.62:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = w
            lines.append(cur)
            if len(lines) <= 3 and all(lines):
                break
        lh = int(size * 1.05)
        y = (H - lh * len(lines)) // 2
        for k, line in enumerate(lines):
            fill = (255, 205, 60) if k == len(lines) - 1 else (255, 255, 255)
            d.text((60, y + k * lh), line, font=font, fill=fill, stroke_width=max(4, size // 18),
                   stroke_fill=(0, 0, 0))
    img.save(out, "JPEG", quality=90, optimize=True)
    return out


def write_metadata(data: dict, scenes: list[dict], timeline: dict, shorts: list[dict],
                   images: list[Path], out_dir: Path, cfg) -> dict:
    yt = data["youtube_master"]
    chapters = build_chapters(scenes, timeline)
    desc = make_description(yt.get("description", ""), chapters)
    meta = {
        "master": {"file": "Master.mp4", "title": yt["title"], "description": desc, "tags": yt["tags"],
                   "duration": round(timeline["duration"], 2), "captions": "Master.srt",
                   "thumbnail": "thumbnail.jpg",
                   "chapters": [{"t": fmt_ts(t), "title": c} for t, c in chapters]},
        "shorts": shorts,
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    sep = "=" * 70
    txt = [sep, "YOUTUBE MASTER", sep, f"Файл: Master.mp4  ({fmt_ts(timeline['duration'])})", "",
           "ЗАГЛАВИЕ:", yt["title"], "", "ОПИСАНИЕ:", desc, "", "ТАГОВЕ:", ", ".join(yt["tags"]), "",
           "Субтитри за качване: Master.srt   |   Thumbnail: thumbnail.jpg", ""]
    for s in shorts:
        txt += [sep, f"{s['file']}  ({s['duration']:.0f}s)", sep, "ЗАГЛАВИЕ:", s["title"], "",
                "ОПИСАНИЕ:", s["description"], ""]
    (out_dir / "metadata.txt").write_text("\n".join(txt), encoding="utf-8")

    th = data.get("thumbnail") or {}
    i = data["_index"].get(th.get("scene_id"), 0)
    make_thumbnail(images[i], th.get("text", ""), out_dir / "thumbnail.jpg", cfg)
    return meta
