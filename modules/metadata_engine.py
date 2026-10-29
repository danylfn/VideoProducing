"""Метаданни за качване: реални YouTube глави, metadata.txt/json, thumbnails (A/B варианти), кредити за музиката."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

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


def make_description(base: str, chapters: list[tuple[float, str]], credits: list[str] | None = None) -> str:
    block = "\n".join(f"{fmt_ts(t)} {title}" for t, title in chapters)
    if "{{CHAPTERS}}" in base:
        desc = base.replace("{{CHAPTERS}}", block).strip()
    else:
        desc = f"{base.strip()}\n\n⏱ Chapters\n{block}".strip() if block else base.strip()
    if credits:
        desc += "\n\n🎵 Music:\n" + "\n\n".join(credits)
    return desc


def music_credits(tracks: list[Path] | None) -> list[str]:
    """Текстът за кредит на всяка песен е в съседен файл <песен>.txt (лицензите като CC BY го изискват)."""
    credits = []
    for t in tracks or []:
        note = t.with_suffix(".txt")
        if note.exists():
            credits.append(note.read_text(encoding="utf-8").strip())
        else:
            log.warning("⚠️  Няма кредит за музиката %s (създайте %s, ако лицензът го изисква)", t.name, note.name)
    return credits


def make_thumbnail(img_path: Path, text: str, out: Path, cfg) -> Path:
    W, H = 1280, 720
    img = Image.open(img_path).convert("RGB").resize((W, H), Image.LANCZOS)
    # по-наситен и контрастен кадър: изпъква в малкия размер на началната страница
    img = ImageEnhance.Color(ImageEnhance.Contrast(img).enhance(1.12)).enhance(1.15)
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
                   images: list[Path], out_dir: Path, cfg, music: list[Path] | None = None,
                   thumb_images: list[Path] | None = None) -> dict:
    yt = data["youtube_master"]
    chapters = build_chapters(scenes, timeline)
    credits = music_credits(music)
    desc = make_description(yt.get("description", ""), chapters, credits)
    if credits:  # Shorts носят същата музика -> кратък кредит и в техните описания
        short_credit = "🎵 Music: " + "; ".join(c.splitlines()[0] for c in credits) + " (CC BY 4.0)"
        shorts = [{**s, "description": f"{s['description'].strip()}\n\n{short_credit}"} for s in shorts]
    titles = [yt["title"], *yt.get("title_variants", [])]

    # thumbnails: варианти със собствена картина (thumb_images) или кадър от сцена + текст
    th = data.get("thumbnail") or {}
    variants = th.get("variants") or [{"text": th.get("text", "")}]
    thumbs = []
    for k, v in enumerate(variants):
        if thumb_images and k < len(thumb_images) and thumb_images[k]:
            src = thumb_images[k]
        else:
            src = images[data["_index"].get(v.get("scene_id", th.get("scene_id")), 0)]
        name = "thumbnail.jpg" if k == 0 else f"thumbnail_{k + 1}.jpg"
        make_thumbnail(src, v.get("text", ""), out_dir / name, cfg)
        thumbs.append(name)
    for old in out_dir.glob("thumbnail_*.jpg"):  # варианти от предишен рън с повече thumbnails
        if old.name not in thumbs:
            old.unlink()

    meta = {
        "master": {"file": "Master.mp4", "title": yt["title"], "title_variants": titles[1:], "description": desc,
                   "tags": yt["tags"], "duration": round(timeline["duration"], 2), "captions": "Master.srt",
                   "thumbnail": thumbs[0], "thumbnails": thumbs,
                   "chapters": [{"t": fmt_ts(t), "title": c} for t, c in chapters]},
        "shorts": shorts,
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    sep = "=" * 70
    txt = [sep, "YOUTUBE MASTER", sep, f"Файл: Master.mp4  ({fmt_ts(timeline['duration'])})", "",
           "ЗАГЛАВИЕ:", titles[0], ""]
    if len(titles) > 1 or len(thumbs) > 1:
        txt += ["A/B ТЕСТ (YouTube Studio → Test & Compare):"]
        txt += [f"  заглавие {k + 1}: {t}" for k, t in enumerate(titles)]
        txt += [f"  thumbnail {k + 1}: {t}" for k, t in enumerate(thumbs)] + [""]
    txt += ["ОПИСАНИЕ:", desc, "", "ТАГОВЕ:", ", ".join(yt["tags"]), "",
            f"Субтитри за качване: Master.srt   |   Thumbnail: {', '.join(thumbs)}", ""]
    for s in shorts:
        txt += [sep, f"{s['file']}  ({s['duration']:.0f}s)  –  YouTube Shorts / TikTok", sep, "ЗАГЛАВИЕ:", s["title"],
                "", "ОПИСАНИЕ:", s["description"], ""]
    (out_dir / "metadata.txt").write_text("\n".join(txt), encoding="utf-8")
    return meta
