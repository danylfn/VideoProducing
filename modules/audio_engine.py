"""
Модул 2: Синтез на реч.
Двигатели:
  kokoro     – локален невронен глас (82M, Apache 2.0), с тайминг на всяка дума. По подразбиране.
  elevenlabs – /with-timestamps -> аудио + точни тайминги на всяка дума (за субтитри)
  say        – вграденият macOS глас, безплатно и офлайн (за тестове)
  silent     – тишина с реалистична дължина (за тест на видео частта без глас)
Всеки резултат се кешира по хеш на текста + настройките -> повторно пускане не харчи кредити.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

from .pronunciation import Lexicon, map_timings, prepare
from .utils import (RetryableError, check_http, ensure_dir, log, run_ffmpeg,
                    short_hash, with_retry)


# ------------------------------------------------------------------ двигатели

def _tts_elevenlabs(text: str, prev_text: str, next_text: str, out_mp3: Path, cfg) -> dict | None:
    if not cfg.elevenlabs_api_key or not cfg.elevenlabs_voice_id:
        raise SystemExit("❌ Липсват ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID в .env")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{cfg.elevenlabs_voice_id}/with-timestamps"
    payload = {
        "text": text,
        "model_id": cfg.elevenlabs_model,
        "voice_settings": {
            "stability": cfg.elevenlabs_stability,
            "similarity_boost": cfg.elevenlabs_similarity,
            "style": cfg.elevenlabs_style,
            "use_speaker_boost": True,
            "speed": cfg.elevenlabs_speed,
        },
        # контекст от съседните сцени -> естествена интонация на прехода
        "previous_text": prev_text,
        "next_text": next_text,
    }
    payload = {k: v for k, v in payload.items() if v}

    def call():
        try:
            r = requests.post(url, params={"output_format": "mp3_44100_128"},
                              headers={"xi-api-key": cfg.elevenlabs_api_key}, json=payload, timeout=180)
        except requests.RequestException as e:
            raise RetryableError(str(e))
        check_http(r, "ElevenLabs")
        return r.json()

    data = with_retry(call, "ElevenLabs")
    out_mp3.write_bytes(base64.b64decode(data["audio_base64"]))
    al = data.get("alignment") or data.get("normalized_alignment")
    if not al:
        return None
    return {"chars": al["characters"],
            "starts": al["character_start_times_seconds"],
            "ends": al["character_end_times_seconds"]}


def _tts_say(text: str, out_aiff: Path, cfg) -> None:
    if not shutil.which("say"):
        raise SystemExit("❌ 'say' е наличен само на macOS. Ползвайте --tts silent или elevenlabs.")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        txt = f.name
    subprocess.run(["say", "-v", cfg.say_voice, "-r", str(cfg.say_rate), "-o", str(out_aiff), "-f", txt],
                   check=True)
    Path(txt).unlink(missing_ok=True)


def _tts_silent(text: str, out_wav: Path, cfg) -> None:
    secs = max(1.5, len(text.split()) / (cfg.words_per_minute / 60))
    n = int(secs * cfg.sample_rate)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(cfg.sample_rate)
        w.writeframes(b"\x00\x00" * n)


# ------------------------------------------------------------------ Kokoro (локално)

_KOKORO_SR = 24000
_kokoro_pipe = None
_kokoro_lock = threading.Lock()


def _kokoro_pipeline(cfg):
    global _kokoro_pipe
    if _kokoro_pipe is None:
        try:
            from kokoro import KPipeline
        except ImportError:
            raise SystemExit("❌ Липсва Kokoro. Инсталирайте: brew install espeak-ng && "
                             "pip install -r requirements-local.txt")
        log.info("🔊 Зареждам Kokoro (глас %s)...", cfg.kokoro_voice)
        _kokoro_pipe = KPipeline(lang_code=cfg.kokoro_lang, repo_id="hexgrad/Kokoro-82M")
    return _kokoro_pipe


def _kokoro_words(tokens, offset: float) -> list[dict]:
    """Слива токените на Kokoro в думи като в оригиналния текст ("Noxus" + "," -> "Noxus,")."""
    words, cur, s, e = [], "", None, None
    for tok in tokens or []:
        txt = getattr(tok, "text", "") or ""
        st, en = getattr(tok, "start_ts", None), getattr(tok, "end_ts", None)
        cur += txt
        if st is not None and s is None:
            s = st
        if en is not None:
            e = en
        if getattr(tok, "whitespace", "") and cur.strip():
            if s is not None and e is not None:
                words.append({"w": cur.strip(), "s": offset + s, "e": offset + e})
            cur, s, e = "", None, None
    if cur.strip() and s is not None and e is not None:
        words.append({"w": cur.strip(), "s": offset + s, "e": offset + e})
    return words


def _tts_kokoro(text: str, out_wav: Path, cfg) -> list[dict] | None:
    pipe = _kokoro_pipeline(cfg)
    chunks, words, offset = [], [], 0.0
    with _kokoro_lock:
        for res in pipe(text, voice=cfg.kokoro_voice, speed=cfg.kokoro_speed, split_pattern=r"\n+"):
            audio = getattr(res, "audio", None)
            if audio is None and isinstance(res, tuple):
                audio = res[2]
            if audio is None:
                continue
            a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
            a = a.astype(np.float32).reshape(-1)
            words += _kokoro_words(getattr(res, "tokens", None), offset)
            offset += len(a) / _KOKORO_SR
            chunks.append(a)
    if not chunks:
        raise RuntimeError(f"Kokoro не върна аудио за: {text[:60]}...")
    pcm = (np.clip(np.concatenate(chunks), -1, 1) * 32767).astype("<i2")
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_KOKORO_SR)
        w.writeframes(pcm.tobytes())
    return words or None


# ------------------------------------------------------------------ тайминг на думи

_WORD_RE = re.compile(r"\S+")


def words_from_alignment(al: dict) -> list[dict]:
    words, cur, start, end = [], "", None, None
    for ch, s, e in zip(al["chars"], al["starts"], al["ends"]):
        if ch.isspace():
            if cur:
                words.append({"w": cur, "s": start, "e": end})
            cur, start = "", None
            continue
        if not cur:
            start = s
        cur += ch
        end = e
    if cur:
        words.append({"w": cur, "s": start, "e": end})
    return words


def words_proportional(text: str, dur: float) -> list[dict]:
    """Без alignment: разпределя времето пропорционално на дължината на думите (+ паузи на пунктуация)."""
    toks = _WORD_RE.findall(text)
    if not toks:
        return []
    weights = [len(t) + 2 + (4 if t[-1] in ".!?…" else 2 if t[-1] in ",;:—" else 0) for t in toks]
    total = sum(weights)
    lead, usable = dur * 0.03, dur * 0.94
    t, out = lead, []
    for tok, wgt in zip(toks, weights):
        d = usable * wgt / total
        out.append({"w": tok, "s": t, "e": t + d * 0.9})
        t += d
    return out


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


# ------------------------------------------------------------------ публичен API

def generate_audio(scenes: list[dict], work: Path, cfg, force: bool = False,
                   lexicon: Lexicon | None = None) -> list[dict]:
    """Връща за всяка сцена: {'wav': Path (моно, sample_rate), 'dur': float, 'words': [...]}
    Думите в 'words' са с оригиналното изписване (за субтитри), а гласът ползва речника за произношение."""
    adir = ensure_dir(work / "audio")
    engine = cfg.tts_engine
    lexicon = lexicon or Lexicon()
    voice_key = {
        "elevenlabs": [cfg.elevenlabs_voice_id, cfg.elevenlabs_model, cfg.elevenlabs_stability,
                       cfg.elevenlabs_similarity, cfg.elevenlabs_style, cfg.elevenlabs_speed],
        "say": [cfg.say_voice, cfg.say_rate],
        "silent": [cfg.words_per_minute],
        "kokoro": [cfg.kokoro_voice, cfg.kokoro_lang, cfg.kokoro_speed],
    }.get(engine)
    if voice_key is None:
        raise SystemExit(f"❌ Непознат TTS двигател: {engine}")
    preps = [prepare(" ".join(sc["narration"].split()), lexicon, engine) for sc in scenes]

    def job(i: int) -> dict:
        sc, prep = scenes[i], preps[i]
        # ключът е по текста КЪМ гласа -> промяна в речника регенерира само засегнатите сцени
        key = short_hash(engine, voice_key, prep.text, cfg.sample_rate)
        stem = adir / f"scene_{sc['scene_id']:03d}_{key}"
        wav, meta = stem.with_suffix(".wav"), stem.with_suffix(".json")

        if force or not wav.exists() or not meta.exists():
            tts_words = None
            if engine == "kokoro":
                raw = stem.with_suffix(".raw.wav")
                tts_words = _tts_kokoro(prep.text, raw, cfg)
            elif engine == "elevenlabs":
                raw = stem.with_suffix(".mp3")
                prev_t = preps[i - 1].text if i > 0 else ""
                next_t = preps[i + 1].text if i + 1 < len(scenes) else ""
                al = _tts_elevenlabs(prep.text, prev_t, next_t, raw, cfg)
                tts_words = words_from_alignment(al) if al else None
            elif engine == "say":
                raw = stem.with_suffix(".aiff")
                _tts_say(prep.text, raw, cfg)
            else:
                raw = stem.with_suffix(".raw.wav")
                _tts_silent(prep.text, raw, cfg)
            # нормализиран междинен формат: PCM 16-bit моно @ sample_rate
            run_ffmpeg(["-i", raw, "-ac", "1", "-ar", cfg.sample_rate, "-c:a", "pcm_s16le", wav])
            dur = _wav_duration(wav)
            words = map_timings(prep, tts_words)
            exact = words is not None
            if not exact:
                words = map_timings(prep, words_proportional(prep.text, dur))
            meta.write_text(json.dumps({"text": prep.text, "dur": dur, "words": words, "exact_timing": exact},
                                       ensure_ascii=False))
            log.info("🎙  Сцена %3d: %.1fs%s", sc["scene_id"], dur, "" if exact or engine in ("say", "silent")
                     else "  (приблизителен тайминг на субтитрите)")
        else:
            log.debug("Кеш: аудио сцена %d", sc["scene_id"])
        m = json.loads(meta.read_text())
        return {"wav": wav, "dur": m["dur"], "words": m["words"]}

    workers = cfg.tts_workers if engine == "elevenlabs" else 1
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(job, range(len(scenes))))
