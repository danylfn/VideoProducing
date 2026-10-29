"""
Модул 3: Генериране на изображения.
Двигатели:
  mflux       – Flux върху Apple MLX, директно на GPU-то на Mac-а (локално, по подразбиране)
  comfyui     – локален ComfyUI сървър (всякакви модели/LoRA/workflow-и)
  placeholder – тест без модели
  fal / replicate – облачни (по желание)
Всяка картина се кешира по хеш на промпта -> редакция на 1 сцена регенерира само нея.
След генерация: crop/resize до точната резолюция + лек кинематографичен грейд + винетка.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from .utils import RetryableError, check_http, ensure_dir, log, short_hash, with_retry


CHAR_REF = re.compile(r"\{([A-Za-z0-9_]+)\}")


def full_prompt(scene: dict, cfg, characters: dict | None = None) -> str:
    """Сцена + описания на героите ({darius} -> пълното визуално описание) + общ стил."""
    chars = characters or {}
    p = " ".join(scene["image_prompt"].split()).rstrip(" ,.")
    p = CHAR_REF.sub(lambda m: chars.get(m.group(1).lower(), m.group(1).replace("_", " ")), p)
    if cfg.style_suffix and cfg.style_suffix.lower()[:30] not in p.lower():
        p = f"{p}, {cfg.style_suffix}"
    return p


def _download(url: str) -> bytes:
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])

    def call():
        try:
            r = requests.get(url, timeout=120)
        except requests.RequestException as e:
            raise RetryableError(str(e))
        check_http(r, "Изтегляне на изображение")
        return r.content
    return with_retry(call, "Изтегляне")


# ------------------------------------------------------------------ fal.ai

def _gen_fal(prompt: str, cfg) -> bytes:
    if not cfg.fal_key:
        raise SystemExit("❌ Липсва FAL_KEY в .env")
    payload: dict = {"prompt": prompt, "num_images": 1, "enable_safety_checker": True, "output_format": "png"}
    if "ultra" in cfg.fal_model:
        payload["aspect_ratio"] = "16:9"
    else:
        payload["image_size"] = {"width": 1920, "height": 1088}  # кратно на 16; режем до 1080 после

    def call():
        try:
            r = requests.post(f"https://fal.run/{cfg.fal_model}", json=payload,
                              headers={"Authorization": f"Key {cfg.fal_key}"}, timeout=300)
        except requests.RequestException as e:
            raise RetryableError(str(e))
        check_http(r, "fal.ai")
        return r.json()
    data = with_retry(call, "fal.ai")
    return _download(data["images"][0]["url"])


# ------------------------------------------------------------------ Replicate

def _gen_replicate(prompt: str, cfg) -> bytes:
    if not cfg.replicate_token:
        raise SystemExit("❌ Липсва REPLICATE_API_TOKEN в .env")
    headers = {"Authorization": f"Bearer {cfg.replicate_token}", "Prefer": "wait"}
    url = f"https://api.replicate.com/v1/models/{cfg.replicate_model}/predictions"
    body = {"input": {"prompt": prompt, "aspect_ratio": "16:9", "output_format": "png"}}

    def call():
        try:
            r = requests.post(url, json=body, headers=headers, timeout=300)
            check_http(r, "Replicate")
            pred = r.json()
            while pred.get("status") in ("starting", "processing"):
                time.sleep(2)
                r = requests.get(pred["urls"]["get"], headers=headers, timeout=60)
                check_http(r, "Replicate")
                pred = r.json()
        except requests.RequestException as e:
            raise RetryableError(str(e))
        if pred.get("status") != "succeeded":
            raise RetryableError(f"Replicate статус {pred.get('status')}: {pred.get('error')}")
        return pred
    out = with_retry(call, "Replicate")["output"]
    return _download(out[0] if isinstance(out, list) else out)


# ------------------------------------------------------------------ ComfyUI (локално)

def _gen_comfyui(prompt: str, cfg) -> bytes:
    """
    Ползва workflow, експортиран от ComfyUI чрез 'Export (API)'. В него сложете:
      "{{PROMPT}}"   – в текстовото поле на позитивния CLIP Text Encode
      "{{NEGATIVE}}" – (по желание) в негативния
      "{{SEED}}"     – като стойност на seed в KSampler (като низ, ще бъде заменен с число)
    """
    wf_path = cfg.comfyui_workflow
    if not wf_path.exists():
        raise SystemExit(f"❌ Липсва ComfyUI workflow: {wf_path} (вижте README)")
    wf = json.loads(wf_path.read_text())
    seed = random.randint(1, 2**31 - 1)
    found = False
    for node in wf.values():
        for k, v in node.get("inputs", {}).items():
            if v == "{{PROMPT}}":
                node["inputs"][k], found = prompt, True
            elif v == "{{NEGATIVE}}":
                node["inputs"][k] = cfg.negative_prompt
            elif v == "{{SEED}}":
                node["inputs"][k] = seed
    if not found:
        raise SystemExit("❌ В ComfyUI workflow-а няма поле със стойност \"{{PROMPT}}\"")

    base = cfg.comfyui_url.rstrip("/")

    def call():
        try:
            r = requests.post(f"{base}/prompt", json={"prompt": copy.deepcopy(wf), "client_id": str(uuid.uuid4())},
                              timeout=30)
            check_http(r, "ComfyUI")
            pid = r.json()["prompt_id"]
            t0 = time.time()
            while time.time() - t0 < 900:
                h = requests.get(f"{base}/history/{pid}", timeout=30).json()
                if pid in h:
                    for out in h[pid].get("outputs", {}).values():
                        for img in out.get("images", []):
                            if img.get("type") == "output":
                                v = requests.get(f"{base}/view", params=img, timeout=60)
                                check_http(v, "ComfyUI view")
                                return v.content
                    raise RuntimeError("ComfyUI завърши без изходно изображение (има ли Save Image нод?)")
                time.sleep(1.5)
            raise RetryableError("ComfyUI timeout")
        except requests.ConnectionError:
            raise SystemExit(f"❌ ComfyUI не отговаря на {base}. Стартиран ли е?")
        except requests.RequestException as e:
            raise RetryableError(str(e))
    return with_retry(call, "ComfyUI", tries=2)


# ------------------------------------------------------------------ mflux (локално, Apple MLX)

_mflux_state: dict = {}
_mflux_lock = threading.Lock()


def _is_klein(cfg) -> bool:
    return cfg.mflux_model.lower().startswith(("flux2-klein", "klein"))


def _mflux_load(cfg):
    if _mflux_state.get("mode") == "failed":  # не теглим наново за всяка следваща сцена
        raise SystemExit(_mflux_state["error"])
    if "mode" in _mflux_state:
        return
    try:
        _mflux_load_model(cfg)
    except Exception as e:
        if type(e).__name__ not in ("GatedRepoError", "RepositoryNotFoundError"):
            raise
        repo = re.search(r"huggingface\.co/([\w.-]+/[\w.-]+)", str(e))
        repo = repo.group(1) if repo else cfg.mflux_model
        _mflux_state.update(mode="failed", error=(
            f"❌ {repo} е gated модел в Hugging Face: изпълнете 'hf auth login' и приемете лиценза на "
            f"https://huggingface.co/{repo} (или ползвайте MFLUX_MODEL=flux2-klein-4b, който не изисква вход)"))
        raise SystemExit(_mflux_state["error"])


def _mflux_load_model(cfg):
    if _is_klein(cfg):  # FLUX.2 [klein] 4B: Apache 2.0, без gated достъп в Hugging Face
        from mflux.models.common.config.model_config import ModelConfig
        from mflux.models.flux2.variants import Flux2Klein
        log.info("🧠 Зареждам %s (MLX, %s-bit)... първия път тегли теглата от Hugging Face",
                 cfg.mflux_model, cfg.mflux_quantize or 16)
        _mflux_state.update(mode="api", Config=None, model=Flux2Klein(
            model_config=ModelConfig.from_name(cfg.mflux_model), quantize=cfg.mflux_quantize or None))
        return
    Flux1 = Config = None
    for mod_flux in ("mflux", "mflux.models.flux.variants.txt2img.flux"):
        try:
            Flux1 = __import__(mod_flux, fromlist=["Flux1"]).Flux1
            break
        except (ImportError, AttributeError):
            continue
    # mflux < 0.10 подава параметрите през Config; новите версии ги приемат директно в generate_image
    for mod_cfg in ("mflux", "mflux.config.config"):
        try:
            Config = __import__(mod_cfg, fromlist=["Config"]).Config
            break
        except (ImportError, AttributeError):
            continue
    if Flux1 is not None:
        log.info("🧠 Зареждам Flux %s (MLX, %s-bit)... първия път тегли теглата от Hugging Face",
                 cfg.mflux_model, cfg.mflux_quantize or 16)
        _mflux_state.update(mode="api", model=Flux1.from_name(model_name=cfg.mflux_model,
                                                              quantize=cfg.mflux_quantize or None), Config=Config)
    elif shutil.which("mflux-generate"):
        _mflux_state["mode"] = "cli"
    else:
        raise SystemExit("❌ Липсва mflux. Инсталирайте: pip install -r requirements-local.txt")


def _mflux_cli(prompt: str, seed: int, steps: int, out: Path, cfg) -> None:
    cmd = ["mflux-generate-flux2" if _is_klein(cfg) else "mflux-generate", "--model", cfg.mflux_model,
           "--prompt", prompt, "--steps", str(steps),
           "--seed", str(seed), "--width", str(cfg.mflux_width), "--height", str(cfg.mflux_height),
           "--output", str(out)]
    if cfg.mflux_quantize:
        cmd += ["--quantize", str(cfg.mflux_quantize)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not out.exists():
        raise RuntimeError("mflux-generate грешка:\n" + "\n".join(res.stderr.strip().splitlines()[-10:]))


def _gen_mflux(prompt: str, cfg, seed: int) -> bytes:
    steps = cfg.mflux_steps or (4 if cfg.mflux_model == "schnell" or _is_klein(cfg) else 25)
    with _mflux_lock:
        _mflux_load(cfg)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "img.png"
            if _mflux_state["mode"] == "api":
                try:
                    params = dict(num_inference_steps=steps, height=cfg.mflux_height, width=cfg.mflux_width)
                    if _mflux_state["Config"] is not None:
                        params = {"config": _mflux_state["Config"](**params)}
                    img = _mflux_state["model"].generate_image(seed=seed, prompt=prompt, **params)
                    img.save(path=str(out))
                except (TypeError, AttributeError) as e:  # различна версия на API-то -> CLI
                    log.warning("mflux Python API несъвместимо (%s) -> минавам на mflux-generate", e)
                    _mflux_state.clear()
                    _mflux_state["mode"] = "cli"
            if _mflux_state["mode"] == "cli":
                _mflux_cli(prompt, seed, steps, out, cfg)
            return out.read_bytes()


# ------------------------------------------------------------------ Placeholder

def load_font(cfg, size: int) -> ImageFont.FreeTypeFont:
    for f in cfg.pil_fonts:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def _gen_placeholder(prompt: str, cfg, label: str) -> bytes:
    rnd = random.Random(prompt)
    w, h = 1920, 1080
    c1 = np.array([rnd.randint(20, 90) for _ in range(3)], dtype=np.float32)
    c2 = np.array([rnd.randint(90, 200) for _ in range(3)], dtype=np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = ((xx / w) * 0.6 + (yy / h) * 0.4)[..., None]
    arr = (c1 * (1 - t) + c2 * t).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    d = ImageDraw.Draw(img)
    for _ in range(14):  # геометрия, за да се вижда Ken Burns движението
        x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(30, 220)
        d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255), width=3)
    d.text((80, 70), label, font=load_font(cfg, 110), fill=(255, 255, 255))
    words, line, lines = prompt.split(), "", []
    for wd in words:
        if len(line) + len(wd) > 60:
            lines.append(line)
            line = ""
        line += wd + " "
    lines.append(line)
    d.multiline_text((80, 230), "\n".join(lines[:6]), font=load_font(cfg, 40), fill=(235, 235, 235), spacing=12)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ Пост-обработка

def grade(raw: bytes, cfg, size: tuple[int, int]) -> Image.Image:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img = ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.45))
    img = ImageEnhance.Contrast(img).enhance(cfg.grade_contrast)
    img = ImageEnhance.Color(img).enhance(cfg.grade_saturation)
    if cfg.grade_vignette > 0:
        w, h = size
        yy, xx = np.ogrid[-1:1:complex(0, h), -1:1:complex(0, w)]
        mask = 1 - cfg.grade_vignette * np.clip((xx**2 + yy**2) / 2, 0, 1) ** 1.2
        img = Image.fromarray((np.asarray(img, dtype=np.float32) * mask[..., None]).clip(0, 255).astype(np.uint8))
    return img


def generate_images(scenes: list[dict], work: Path, cfg, force: bool = False,
                    characters: dict | None = None) -> list[Path]:
    raw_dir, out_dir = ensure_dir(work / "images" / "raw"), ensure_dir(work / "images")
    engine = cfg.image_engine
    model = {"fal": cfg.fal_model, "replicate": cfg.replicate_model,
             "mflux": [cfg.mflux_model, cfg.mflux_steps, cfg.mflux_quantize, cfg.mflux_width, cfg.mflux_height],
             "comfyui": str(cfg.comfyui_workflow), "placeholder": "v1"}.get(engine)
    if model is None:
        raise SystemExit(f"❌ Непознат image двигател: {engine}")

    def job(sc: dict) -> Path:
        prompt = full_prompt(sc, cfg, characters)
        key = short_hash(engine, model, prompt)
        raw = raw_dir / f"scene_{sc['scene_id']:03d}_{key}.png"
        out = out_dir / f"scene_{sc['scene_id']:03d}_{key}_{cfg.width}.png"
        if force or not raw.exists():
            if engine == "fal":
                data = _gen_fal(prompt, cfg)
            elif engine == "replicate":
                data = _gen_replicate(prompt, cfg)
            elif engine == "mflux":
                data = _gen_mflux(prompt, cfg, seed=int(key, 16) % (2**31 - 1))
            elif engine == "comfyui":
                data = _gen_comfyui(prompt, cfg)
            else:
                data = _gen_placeholder(prompt, cfg, f"SCENE {sc['scene_id']}")
            raw.write_bytes(data)
            log.info("🖼  Сцена %3d: изображение готово", sc["scene_id"])
        if force or not out.exists():
            grade(raw.read_bytes(), cfg, (cfg.width, cfg.height)).save(out, "PNG", compress_level=3)
        return out

    workers = 1 if engine in ("comfyui", "mflux") else cfg.image_workers
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(job, scenes))


def generate_thumbnail_images(data: dict, work: Path, cfg, force: bool = False) -> list[Path | None]:
    """Собствени картини за thumbnail вариантите, които имат "prompt" (напр. лице отблизо със силна емоция).
    Връща списък, подравнен с вариантите: картина или None (тогава се ползва кадър от сцена)."""
    variants = (data.get("thumbnail") or {}).get("variants") or []
    todo = [(k, v) for k, v in enumerate(variants) if v.get("prompt")]
    if not todo:
        return []
    made = generate_images([{"scene_id": k + 1, "image_prompt": v["prompt"]} for k, v in todo],
                           work / "thumbs", cfg, force, data.get("characters"))
    out: list[Path | None] = [None] * len(variants)
    for (k, _), img in zip(todo, made):
        out[k] = img
    return out
