"""
Shots: всяка сцена се разделя на 2–3 кадъра (shots) с твърди cut-ове, всеки от собствена картина.

  - Ако сценарият има "shots" (Claude ги пише по claude_prompt.md), се ползват те.
  - Иначе (стари сценарии) се правят автоматично: wide / medium / close-up вариации на картината на сцената,
    всеки с различно движение на камерата.
  - Камерата: едно ясно и бавно движение на shot, никога едно и също два пъти подред. Силни движения (кран,
    dolly, zoom) LTX ги преиграва – героят излиза от кадър или zoom-ът се връща обратно.
"""
from __future__ import annotations

import math
import re

# Проверени с LTX-2.5 distilled: бавни, умерени движения, които моделът изпълнява в една посока
CAMERA_MOVES = {
    "push_in": "The camera slowly pushes in",
    "truck_left": "The camera slowly trucks to the left",
    "truck_right": "The camera slowly trucks to the right",
    "arc_left": "The camera slowly arcs to the left around the subject",
    "arc_right": "The camera slowly arcs to the right around the subject",
    "handheld": "Subtle handheld camera with slight natural sway",
    "tilt_up": "The camera slowly tilts up",
    "static": "Static camera",
}
# Автоматичният ред: без static (твърде статично) и без два еднакви подред
AUTO_MOVES = ["truck_right", "push_in", "arc_left", "handheld", "truck_left", "arc_right"]
FRAMINGS = ["Wide shot", "Medium shot", "Close-up"]

_MOVE_PATTERNS = {
    "push_in": r"push(es|ing)?[- ]in|pushes (forward|closer)|drifts? closer|moves? closer",
    "pull_out": r"pull(s|ing)?[- ](back|out)",
    "truck_left": r"(truck|track|pan|slide|drift)s?(ing)? (slowly )?(to the )?left",
    "truck_right": r"(truck|track|pan|slide|drift)s?(ing)? (slowly )?(to the )?right",
    "arc_left": r"arcs? (slowly )?(to the )?left|orbits? (slowly )?(to the )?left",
    "arc_right": r"arcs? (slowly )?(to the )?right|orbits? (slowly )?(to the )?right",
    "handheld": r"handheld",
    "tilt_up": r"tilts? up",
    "tilt_down": r"tilts? down",
    "static": r"static camera|locked[- ]off|camera (is )?still",
}
# Движения, които LTX преиграва (дават „дишащ“ zoom или изгубен герой)
STRONG_MOVES = re.compile(r"\b(crane|cranes|craning|dolly|dollies|zoom|zooms|zooming|whip|fast|rapid|spins?|"
                          r"swoops?|flies|flying through)\b", re.I)
_FRAMING_RE = re.compile(r"\b(extreme )?(wide|medium|long|full|establishing|close[- ]?up|closeup)( shot)?\b,?\s*", re.I)
_CAMERA_CLAUSE = re.compile(r"\b(camera|pan|pans|push|pushes|zoom|dolly|truck|tilt|crane|orbit|arc|arcs|track|"
                            r"tracking|handheld|shot|close-up|closeup|angle)\b", re.I)


def camera_move(text: str) -> str | None:
    """Каноничното име на движението на камерата във video_prompt (или None)."""
    for name, pat in _MOVE_PATTERNS.items():
        if re.search(pat, text, re.I):
            return name
    return None


def _strip_camera(video_prompt: str) -> str:
    """Маха изреченията/клаузите за камера и кадриране – автоматичните shots слагат свои."""
    parts = re.split(r"(?<=[.,;])\s+", video_prompt.strip())
    kept = [p for p in parts if not _CAMERA_CLAUSE.search(p)]
    return " ".join(kept).strip(" ,.;") or video_prompt.strip(" ,.")


def split_frames(frames: int, n: int) -> list[int]:
    base, extra = divmod(frames, n)
    return [base + (1 if k < extra else 0) for k in range(n)]


def scene_shots(scene: dict, frames: int, cfg, default_motion: str) -> list[dict]:
    """[{"image_prompt", "video_prompt", "frames"}] за сцена с дадена дължина в кадри."""
    fps = cfg.fps
    given = [s for s in scene.get("shots") or [] if s.get("image_prompt")]
    n_min = math.ceil(frames / (cfg.ltx_max_shot_sec * fps))             # никой shot над тавана
    n_max = max(n_min, frames // (2 * fps))                              # никой shot под ~2 s
    n = len(given) or round(frames / (cfg.ltx_shot_sec * fps))
    n = min(max(n, n_min, 1), n_max)
    base_img = _FRAMING_RE.sub("", scene["image_prompt"]).strip(" ,.")
    base_vid = _strip_camera(scene.get("video_prompt") or default_motion)
    shots = []
    for k, f in enumerate(split_frames(frames, n)):
        if k < len(given):
            img, vid = given[k]["image_prompt"], given[k].get("video_prompt") or base_vid
            if not camera_move(vid):  # shot без движение на камерата получава автоматично
                vid = f"{vid.rstrip(' ,.')}. {CAMERA_MOVES[AUTO_MOVES[(scene['scene_id'] + k) % len(AUTO_MOVES)]]}"
        else:
            src = given[k % len(given)] if given else None
            framing = FRAMINGS[k % len(FRAMINGS)] if n > 1 else None
            img_src = _FRAMING_RE.sub("", src["image_prompt"]).strip(" ,.") if src else base_img
            vid_src = _strip_camera(src.get("video_prompt") or base_vid) if src else base_vid
            img = f"{framing}: {img_src}" if framing else scene["image_prompt"]
            move = CAMERA_MOVES[AUTO_MOVES[(scene["scene_id"] + k) % len(AUTO_MOVES)]]
            vid = f"{vid_src.rstrip(' ,.')}. {move}" + (f", {framing.lower()}" if framing else "")
        shots.append({"image_prompt": img, "video_prompt": vid, "frames": f})
    return shots
