"""
Речник за произношение: променя текста САМО за синтеза на глас.
Субтитрите остават с оригиналното изписване, защото таймингите на думите
се връщат обратно към оригиналните думи.

Стойност в речника:
  "Kah-Zix"          -> фонетичен правопис (работи с всички TTS двигатели)
  "/kəzˈɪks/"        -> IPA (само Kokoro; другите двигатели ползват оригинала)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .utils import log

_PUNCT = "\"'“”‘’«»()[]{}.,!?;:…—–-*"
_POSS = ("'s", "’s")
_APOS_NAME = re.compile(r"\b[A-Z][a-z]*['’][A-Z][a-z]+\b")


def _split(tok: str) -> tuple[str, str, str]:
    i, j = 0, len(tok)
    while i < j and tok[i] in _PUNCT:
        i += 1
    while j > i and tok[j - 1] in _PUNCT:
        j -= 1
    return tok[:i], tok[i:j], tok[j:]


def _norm(word: str) -> str:
    return _split(word)[1].lower().replace("’", "'")


@dataclass
class Lexicon:
    entries: list[tuple[tuple[str, ...], str]] = field(default_factory=list)

    @classmethod
    def build(cls, *sources: dict) -> "Lexicon":
        merged: dict[tuple[str, ...], str] = {}
        for src in sources:
            for k, v in (src or {}).items():
                if not isinstance(k, str) or k.startswith("_") or not str(v).strip():
                    continue
                key = tuple(_norm(w) for w in k.split())
                if all(key):
                    merged[key] = str(v).strip()
        return cls(sorted(merged.items(), key=lambda kv: -len(kv[0])))

    def __contains__(self, word: str) -> bool:
        return any(len(k) == 1 and k[0] == _norm(word) for k, _ in self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def load_global(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"❌ {path.name} е невалиден JSON: {e}")


@dataclass
class TTSText:
    text: str                              # какво се праща на TTS двигателя
    tts_words: list[str]
    groups: list[tuple[str, int, int]]     # (оригинален текст за субтитри, начало, край в tts_words)
    replaced: list[str] = field(default_factory=list)


def _render(value: str, orig_core: str, engine: str) -> str:
    if len(value) > 2 and value[0] == "/" and value[-1] == "/":
        return f"[{orig_core.replace(' ', '')}]({value})" if engine == "kokoro" else orig_core
    return value


def prepare(text: str, lex: Lexicon, engine: str) -> TTSText:
    words = text.split()
    tts: list[str] = []
    groups: list[tuple[str, int, int]] = []
    replaced: list[str] = []
    i = 0
    while i < len(words):
        hit = None
        for key, value in lex.entries:
            L = len(key)
            if i + L > len(words):
                continue
            parts = [_split(w) for w in words[i:i + L]]
            if any(parts[k][1].lower().replace("’", "'") != key[k] for k in range(L - 1)):
                continue
            last = parts[-1][1].lower().replace("’", "'")
            poss = ""
            if last != key[-1]:
                if last.endswith("'s") and last[:-2] == key[-1]:
                    poss = parts[-1][1][-2:]
                else:
                    continue
            hit = (L, parts, value, poss)
            break
        if hit is None:
            tts.append(words[i])
            groups.append((words[i], len(tts) - 1, len(tts)))
            i += 1
            continue
        L, parts, value, poss = hit
        core = " ".join(p[1] for p in parts)
        if poss:
            core = core[:-2]
        rep = _render(value, core, engine).split()
        rep[0] = parts[0][0] + rep[0]
        rep[-1] = rep[-1] + poss + parts[-1][2]
        start = len(tts)
        tts += rep
        groups.append((" ".join(words[i:i + L]), start, len(tts)))
        replaced.append(core)
        i += L
    return TTSText(" ".join(tts), tts, groups, replaced)


def map_timings(prep: TTSText, tts_timings: list[dict] | None) -> list[dict] | None:
    """Връща таймингите от TTS думите обратно към оригиналните думи (за субтитри)."""
    if not tts_timings or len(tts_timings) != len(prep.tts_words):
        return None
    return [{"w": disp, "s": tts_timings[a]["s"], "e": tts_timings[b - 1]["e"]} for disp, a, b in prep.groups]


def suspicious_names(texts: list[str], lex: Lexicon) -> list[str]:
    """Имена като Kha'Zix / Rek'Sai, които TTS почти сигурно ще сбърка, ако не са в речника."""
    found = []
    for t in texts:
        for m in _APOS_NAME.findall(t):
            if m not in lex and m not in found:
                found.append(m)
    return found


def report(lex: Lexicon, texts: list[str]) -> None:
    used = set()
    for t in texts:
        used.update(prepare(t, lex, "kokoro").replaced)
    if used:
        log.info("🗣  Произношение: %d от %d записа в речника се ползват (%s)",
                 len(used), len(lex), ", ".join(sorted(used)[:8]) + ("…" if len(used) > 8 else ""))
    sus = suspicious_names(texts, lex)
    if sus:
        log.warning("⚠️  Имена без запис в речника (TTS вероятно ще ги сбърка): %s", ", ".join(sus))
