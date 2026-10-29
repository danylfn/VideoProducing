"""Зарежда и валидира script.json. Толерантен към копиран от Claude текст (```json огради и др.)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .utils import log


class ScriptError(Exception):
    pass


def _extract_json(raw: str) -> dict:
    raw = raw.strip().lstrip("\ufeff")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ScriptError("Не намирам JSON обект във файла.")
    text = raw[start:end + 1]
    # „умни“ кавички, които понякога се появяват при копиране
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        lines = text.splitlines()
        ctx = lines[e.lineno - 1] if 0 < e.lineno <= len(lines) else ""
        raise ScriptError(f"Невалиден JSON (ред {e.lineno}, кол. {e.colno}): {e.msg}\n  -> {ctx.strip()[:120]}")


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", s.strip().lower()).strip("_")
    return s or "project"


def load_script(path: Path, cfg) -> dict:
    if not path.exists():
        raise ScriptError(f"Файлът {path} не съществува.")
    data = _extract_json(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    warns: list[str] = []

    data["project_name"] = _slug(str(data.get("project_name", path.stem)))
    yt = data.get("youtube_master")
    if not isinstance(yt, dict):
        raise ScriptError("Липсва обект 'youtube_master'.")

    title = str(yt.get("title", "")).strip()
    if not title:
        errors.append("youtube_master.title е празно")
    elif len(title) > 100:
        errors.append(f"youtube_master.title е {len(title)} символа (лимит на YouTube: 100)")
    desc = str(yt.get("description", ""))
    if len(desc) > 4500:
        warns.append(f"Описанието е {len(desc)} символа – близо до лимита 5000 (главите се добавят отгоре)")
    if any(c in desc + title for c in "<>"):
        errors.append("YouTube не позволява '<' и '>' в заглавие/описание")
    tags = [str(t).strip() for t in yt.get("tags", []) if str(t).strip()]
    if sum(len(t) + 1 for t in tags) > 500:
        warns.append("Таговете надхвърлят 500 символа – YouTube ще отреже част от тях")
    yt["tags"] = tags
    alt = yt.get("title_variants") or []
    if not isinstance(alt, list) or not all(isinstance(t, str) and t.strip() for t in alt):
        errors.append("youtube_master.title_variants трябва да е списък от заглавия")
        alt = []
    if len(alt) > 2:
        warns.append("YouTube тества до 3 заглавия – ползвам основното + първите 2 варианта")
        alt = alt[:2]
    for t in alt:
        if len(t) > 100:
            errors.append(f"Вариант на заглавие е {len(t)} символа (лимит 100): {t[:40]}…")
        if any(c in t for c in "<>"):
            errors.append("YouTube не позволява '<' и '>' в заглавие")
    yt["title_variants"] = [t.strip() for t in alt]

    scenes = yt.get("script")
    if not isinstance(scenes, list) or not scenes:
        raise ScriptError("youtube_master.script трябва да е непразен списък от сцени.")
    seen: set[int] = set()
    for i, sc in enumerate(scenes):
        sid = sc.get("scene_id")
        if not isinstance(sid, int):
            errors.append(f"Сцена #{i + 1}: scene_id трябва да е цяло число")
            continue
        if sid in seen:
            errors.append(f"Дублиран scene_id {sid}")
        seen.add(sid)
        if not str(sc.get("narration", "")).strip():
            errors.append(f"Сцена {sid}: празно 'narration'")
        if not str(sc.get("image_prompt", "")).strip():
            errors.append(f"Сцена {sid}: празно 'image_prompt'")
        if "video_prompt" in sc and not (isinstance(sc["video_prompt"], str) and sc["video_prompt"].strip()):
            errors.append(f"Сцена {sid}: 'video_prompt' трябва да е непразен текст (или да липсва)")
        if sc.get("motion") and sc["motion"] not in cfg.kb_modes:
            warns.append(f"Сцена {sid}: непознат motion '{sc['motion']}' – ще се избере автоматично")
            sc.pop("motion")
    if errors:
        raise ScriptError("Грешки в сценария:\n  - " + "\n  - ".join(errors))
    scenes.sort(key=lambda s: s["scene_id"])

    # ---- герои (визуална консистентност)
    raw_chars = data.get("characters") or {}
    if not isinstance(raw_chars, dict):
        raise ScriptError("'characters' трябва да е обект {ключ: описание}")
    chars = {}
    for k, v in raw_chars.items():
        key = re.sub(r"[^a-z0-9_]", "_", str(k).lower())
        desc = " ".join(str(v).split()).rstrip(" ,.")
        if not desc:
            errors.append(f"Герой '{k}' е без описание")
            continue
        if len(desc.split()) > 55:
            warns.append(f"Описанието на '{key}' е {len(desc.split())} думи – дръжте го под ~45, "
                         f"иначе дългият промпт се реже от модела")
        chars[key] = desc
    from .image_engine import CHAR_REF, full_prompt
    used_chars = set()
    for sc in scenes:
        for ref in CHAR_REF.findall(sc["image_prompt"] + " " + sc.get("video_prompt", "")):
            if ref.lower() not in chars:
                errors.append(f"Сцена {sc['scene_id']}: {{{ref}}} не е дефиниран в 'characters'")
            used_chars.add(ref.lower())
        bare = CHAR_REF.sub("", sc["image_prompt"]).lower()
        for key in chars:
            name = key.split("_")[0]
            if len(name) > 2 and re.search(rf"\b{re.escape(name)}\b", bare):
                warns.append(f"Сцена {sc['scene_id']}: '{name}' е написан без {{{key}}} – "
                             f"описанието няма да се вмъкне")
                break
        est_tokens = len(full_prompt(sc, cfg, chars).split()) * 1.35
        if est_tokens > 230:
            warns.append(f"Сцена {sc['scene_id']}: промптът е ~{est_tokens:.0f} токена – Flux schnell чете до 256, "
                         f"краят (стилът) може да се загуби")
    for key in set(chars) - used_chars:
        warns.append(f"Героят '{key}' не се ползва в нито една сцена")
    no_vp = [sc["scene_id"] for sc in scenes if not sc.get("video_prompt")]
    if cfg.motion_engine == "ltx" and no_vp:
        warns.append(f"{len(no_vp)} сцени без 'video_prompt' (напр. {no_vp[:5]}) – ще получат общо описание на "
                     f"движението; собствен video_prompt дава много по-добро видео")
    data["characters"] = chars

    # ---- произношение
    pron = data.get("pronunciations") or {}
    if not isinstance(pron, dict) or not all(isinstance(v, str) for v in pron.values()):
        errors.append("'pronunciations' трябва да е обект {\"Име\": \"Фо-не-тич-но\"}")
        pron = {}
    data["pronunciations"] = pron
    if errors:
        raise ScriptError("Грешки в сценария:\n  - " + "\n  - ".join(errors))
    index = {s["scene_id"]: i for i, s in enumerate(scenes)}

    total_words = sum(len(s["narration"].split()) for s in scenes)
    est_min = total_words / cfg.words_per_minute
    if abs(est_min - cfg.target_minutes) > 2:
        warns.append(f"Сценарият е ~{est_min:.1f} мин ({total_words} думи) при цел {cfg.target_minutes:.0f} мин "
                     f"(~{int(cfg.target_minutes * cfg.words_per_minute)} думи)")

    shorts = data.get("shorts", []) or []
    if len(shorts) > 6:
        warns.append(f"{len(shorts)} shorts – препоръчват се 3–6 на епизод")
    for sh in shorts:
        sid = sh.get("short_id", "?")
        a, b = sh.get("start_scene_id"), sh.get("end_scene_id")
        if a not in index or b not in index:
            errors.append(f"Short {sid}: сцени {a}-{b} не съществуват")
            continue
        if index[a] > index[b]:
            errors.append(f"Short {sid}: start_scene_id > end_scene_id")
            continue
        if len(str(sh.get("title", ""))) > 100:
            errors.append(f"Short {sid}: заглавието е над 100 символа")
        words = sum(len(scenes[i]["narration"].split()) for i in range(index[a], index[b] + 1))
        est = words / cfg.words_per_minute * 60
        if est > cfg.shorts_max_sec:
            warns.append(f"Short {sid}: ~{est:.0f}s (над {cfg.shorts_max_sec:.0f}s) – помислете за по-къс диапазон")
    if errors:
        raise ScriptError("Грешки в shorts:\n  - " + "\n  - ".join(errors))

    thumb = data.get("thumbnail") or {}
    if thumb.get("scene_id") is not None and thumb["scene_id"] not in index:
        warns.append("thumbnail.scene_id не съществува – ще ползвам първата сцена")
        thumb.pop("scene_id")
    variants = thumb.get("variants") or []
    if not isinstance(variants, list) or not all(isinstance(v, dict) for v in variants):
        errors.append("thumbnail.variants трябва да е списък от {\"prompt\": ..., \"text\": ...}")
        variants = []
    if len(variants) > 3:
        warns.append("YouTube тества до 3 thumbnails – ползвам първите 3 варианта")
        variants = variants[:3]
    for k, v in enumerate(variants, 1):
        for ref in CHAR_REF.findall(str(v.get("prompt", ""))):
            if ref.lower() not in chars:
                errors.append(f"thumbnail вариант {k}: {{{ref}}} не е дефиниран в 'characters'")
        if v.get("scene_id") is not None and v["scene_id"] not in index:
            errors.append(f"thumbnail вариант {k}: сцена {v['scene_id']} не съществува")
        if len(str(v.get("text", "")).split()) > 5:
            warns.append(f"thumbnail вариант {k}: текстът е над 5 думи – на малкия размер ще е нечетим")
    thumb["variants"] = variants
    if errors:
        raise ScriptError("Грешки в thumbnail/заглавия:\n  - " + "\n  - ".join(errors))
    data["thumbnail"] = thumb

    for w in warns:
        log.warning("⚠️  %s", w)
    data["_index"] = index
    data["_stats"] = {
        "scenes": len(scenes),
        "words": total_words,
        "chars": sum(len(s["narration"]) for s in scenes),
        "est_minutes": est_min,
    }
    return data
