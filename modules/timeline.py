"""
Кадрово-точна времева линия. Ключът към перфектния синхрон:
всяка сцена = цяло число кадри, а аудиото ѝ = точно кадри * (sample_rate / fps) семпъла.
Така няма натрупващо се разминаване между картина и глас дори след 70+ сцени.
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

from .utils import ensure_dir


def build_timeline(scenes: list[dict], audio: list[dict], work: Path, cfg) -> dict:
    spf = cfg.sample_rate // cfg.fps  # семпъла на кадър
    pad_dir = ensure_dir(work / "audio_padded")
    lead = int(round(cfg.scene_lead_sec * cfg.sample_rate))
    tail = int(round(cfg.scene_tail_sec * cfg.sample_rate))

    items, frame_cursor = [], 0
    narration = work / "narration.wav"
    with wave.open(str(narration), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(cfg.sample_rate)
        for sc, au in zip(scenes, audio):
            with wave.open(str(au["wav"]), "rb") as w:
                pcm = w.readframes(w.getnframes())
            speech = len(pcm) // 2
            frames = math.ceil((lead + speech + tail) / spf)
            total = frames * spf
            padded = b"\x00\x00" * lead + pcm + b"\x00\x00" * (total - lead - speech)
            out.writeframes(padded)

            start = frame_cursor / cfg.fps
            offset = start + lead / cfg.sample_rate
            items.append({
                "scene_id": sc["scene_id"],
                "frames": frames,
                "start_frame": frame_cursor,
                "start": start,
                "end": (frame_cursor + frames) / cfg.fps,
                "words": [{"w": w["w"], "s": offset + w["s"], "e": offset + w["e"]} for w in au["words"]],
            })
            frame_cursor += frames
    return {"scenes": items, "total_frames": frame_cursor,
            "duration": frame_cursor / cfg.fps, "narration": narration}
