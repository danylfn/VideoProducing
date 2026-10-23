"""Субтитри: ASS (за изпичане с libass) + SRT (за качване в YouTube като captions)."""
from __future__ import annotations

import re
from pathlib import Path

SENT_END = re.compile(r"[.!?…]['\")\]]*$")
CLAUSE_END = re.compile(r"[,;:—–]['\")\]]*$")


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def _wrap2(text: str, limit: int = 40) -> str:
    """Чупи на 2 балансирани реда, ако е нужно."""
    if len(text) <= limit:
        return text
    mid, best = len(text) // 2, None
    for m in re.finditer(" ", text):
        if best is None or abs(m.start() - mid) < abs(best - mid):
            best = m.start()
    return text if best is None else text[:best] + "\\N" + text[best + 1:]


def chunk_words(words: list[dict], max_chars: int, max_words: int = 99, max_dur: float = 5.0) -> list[dict]:
    chunks, cur = [], []

    def flush():
        if cur:
            chunks.append({"words": list(cur), "s": cur[0]["s"], "e": cur[-1]["e"]})
            cur.clear()

    for w in words:
        if cur:
            text_len = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            if text_len > max_chars or len(cur) >= max_words or w["e"] - cur[0]["s"] > max_dur:
                flush()
        cur.append(w)
        cur_len = sum(len(x["w"]) + 1 for x in cur)
        if SENT_END.search(w["w"]) or (CLAUSE_END.search(w["w"]) and
                                       (cur_len >= max_chars * 0.4 or len(cur) >= max(2, max_words - 1))):
            flush()
    flush()
    # четимост: минимум 0.6s и леко задържане до следващия блок
    for i, c in enumerate(chunks):
        nxt = chunks[i + 1]["s"] if i + 1 < len(chunks) else c["e"] + 0.6
        c["e"] = min(max(c["e"] + 0.25, c["s"] + 0.6), nxt - 0.02)
    return chunks


def _header(w: int, h: int, styles: list[str]) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + "\n".join(styles)
        + "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _event(s: float, e: float, style: str, text: str, layer: int = 0) -> str:
    return f"Dialogue: {layer},{_ass_time(s)},{_ass_time(e)},{style},,0,0,0,,{text}"


def write_master_subs(words: list[dict], ass_path: Path, srt_path: Path, cfg) -> None:
    k = cfg.height / 1080
    size, outline, margin = int(cfg.subs_master_size * k), max(2, int(3 * k)), int(70 * k)
    style = (f"Style: Main,{cfg.subs_font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,"
             f"-1,0,0,0,100,100,0,0,1,{outline},{max(1, int(1.5 * k))},2,{int(160 * k)},{int(160 * k)},{margin},1")
    chunks = chunk_words(words, cfg.subs_master_max_chars, max_dur=5.5)
    lines = [_header(cfg.width, cfg.height, [style])]
    srt = []
    for i, c in enumerate(chunks, 1):
        text = " ".join(w["w"] for w in c["words"])
        lines.append(_event(c["s"], c["e"], "Main", _wrap2(_esc(text))))
        srt.append(f"{i}\n{_srt_time(c['s'])} --> {_srt_time(c['e'])}\n{_wrap2(text).replace(chr(92) + 'N', chr(10))}\n")
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    srt_path.write_text("\n".join(srt), encoding="utf-8")


def write_short_subs(words: list[dict], duration: float, title: str, ass_path: Path, cfg) -> None:
    W, H = cfg.short_width, cfg.short_height
    cap = (f"Style: Cap,{cfg.subs_font},{cfg.subs_short_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
           f"-1,0,0,0,100,100,1,0,1,7,3,2,60,60,520,1")
    ttl = (f"Style: Title,{cfg.subs_font},70,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
           f"-1,0,0,0,100,100,0,0,1,6,3,8,70,70,250,1")
    lines = [_header(W, H, [cap, ttl])]

    if title and cfg.shorts_title_overlay:
        t = _esc(title.upper() if cfg.subs_short_uppercase else title)
        lines.append(_event(0, duration, "Title", r"{\fad(250,0)}" + _wrap2(t, 22), layer=1))

    up = (lambda s: s.upper()) if cfg.subs_short_uppercase else (lambda s: s)
    for c in chunk_words(words, max_chars=24, max_words=cfg.subs_short_max_words, max_dur=2.5):
        ws = c["words"]
        if not cfg.subs_short_highlight:
            lines.append(_event(c["s"], c["e"], "Cap", _esc(up(" ".join(w["w"] for w in ws)))))
            continue
        for j, w in enumerate(ws):
            s = c["s"] if j == 0 else w["s"]
            e = ws[j + 1]["s"] if j + 1 < len(ws) else c["e"]
            parts = [(r"{\c&H0000E5FF&}" + _esc(up(x["w"])) + r"{\c&H00FFFFFF&}") if k == j else _esc(up(x["w"]))
                     for k, x in enumerate(ws)]
            pop = r"{\fscx115\fscy115\t(0,120,\fscx100\fscy100)}" if j == 0 else ""
            lines.append(_event(s, e, "Cap", pop + " ".join(parts)))
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
