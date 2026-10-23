"""
Централна конфигурация. Всичко може да се override-не през .env или CLI флагове.
"""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:  # python-dotenv е по желание
    pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v else default


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    return int(v) if v else default


IS_MAC = platform.system() == "Darwin"


@dataclass
class Config:
    # ---------- Пътища ----------
    output_dir: Path = BASE_DIR / "output"

    # ---------- Видео ----------
    resolution: str = field(default_factory=lambda: _env("VIDEO_RES", "1080"))  # "1080" | "4k"
    fps: int = 30
    width: int = 1920
    height: int = 1080
    short_width: int = 1080
    short_height: int = 1920
    video_encoder: str = field(default_factory=lambda: _env(
        "VIDEO_ENCODER", "h264_videotoolbox" if IS_MAC else "libx264"))
    master_bitrate: str = "12M"          # препоръка на YouTube за 1080p30: 8–12 Mbps
    shorts_bitrate: str = "10M"
    x264_preset: str = field(default_factory=lambda: _env("X264_PRESET", "medium"))
    x264_crf: int = 18
    clip_workers: int = max(2, (os.cpu_count() or 4) // 3)

    # ---------- Ken Burns ----------
    kb_zoom: float = 1.08
    kb_modes: tuple = ("zoom_in", "pan_right", "zoom_out", "pan_left")

    # ---------- Аудио ----------
    sample_rate: int = 48000             # 48000/30fps = 1600 семпъла на кадър -> перфектен синхрон
    scene_lead_sec: float = 0.12         # тишина преди репликата
    scene_tail_sec: float = 0.40         # пауза след репликата (дишане между сцените)
    audio_bitrate: str = "192k"
    target_lufs: float = -14.0           # стандарт за YouTube
    music_path: str = field(default_factory=lambda: _env("MUSIC_PATH"))  # файл или папка
    music_volume: float = field(default_factory=lambda: _env_float("MUSIC_VOLUME", 0.16))

    # ---------- TTS ----------
    tts_engine: str = field(default_factory=lambda: _env("TTS_ENGINE", "kokoro"))  # kokoro | say | silent | elevenlabs
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = field(default_factory=lambda: _env("ELEVENLABS_VOICE_ID"))
    elevenlabs_model: str = field(default_factory=lambda: _env("ELEVENLABS_MODEL", "eleven_multilingual_v2"))
    elevenlabs_stability: float = field(default_factory=lambda: _env_float("ELEVENLABS_STABILITY", 0.45))
    elevenlabs_similarity: float = field(default_factory=lambda: _env_float("ELEVENLABS_SIMILARITY", 0.80))
    elevenlabs_style: float = field(default_factory=lambda: _env_float("ELEVENLABS_STYLE", 0.25))
    elevenlabs_speed: float = field(default_factory=lambda: _env_float("ELEVENLABS_SPEED", 1.0))
    say_voice: str = field(default_factory=lambda: _env("SAY_VOICE", "Daniel"))
    say_rate: int = field(default_factory=lambda: _env_int("SAY_RATE", 170))
    # Kokoro (локален невронен TTS, Apache 2.0)
    kokoro_voice: str = field(default_factory=lambda: _env("KOKORO_VOICE", "am_michael"))
    kokoro_lang: str = field(default_factory=lambda: _env("KOKORO_LANG", "a"))  # a = US English, b = UK English
    kokoro_speed: float = field(default_factory=lambda: _env_float("KOKORO_SPEED", 0.95))
    tts_workers: int = field(default_factory=lambda: _env_int("TTS_WORKERS", 2))

    # ---------- Изображения ----------
    image_engine: str = field(default_factory=lambda: _env("IMAGE_ENGINE", "mflux"))  # mflux | comfyui | placeholder | fal | replicate
    # mflux (Flux върху Apple MLX, локално)
    mflux_model: str = field(default_factory=lambda: _env("MFLUX_MODEL", "schnell"))  # schnell | dev
    mflux_steps: int = field(default_factory=lambda: _env_int("MFLUX_STEPS", 0))      # 0 = авто (schnell 4, dev 25)
    mflux_quantize: int = field(default_factory=lambda: _env_int("MFLUX_QUANTIZE", 8))  # 8 | 4 | 0 (без)
    mflux_width: int = field(default_factory=lambda: _env_int("MFLUX_WIDTH", 1920))
    mflux_height: int = field(default_factory=lambda: _env_int("MFLUX_HEIGHT", 1088))
    fal_key: str = field(default_factory=lambda: _env("FAL_KEY"))
    fal_model: str = field(default_factory=lambda: _env("FAL_MODEL", "fal-ai/flux/dev"))
    replicate_token: str = field(default_factory=lambda: _env("REPLICATE_API_TOKEN"))
    replicate_model: str = field(default_factory=lambda: _env("REPLICATE_MODEL", "black-forest-labs/flux-1.1-pro"))
    comfyui_url: str = field(default_factory=lambda: _env("COMFYUI_URL", "http://127.0.0.1:8188"))
    comfyui_workflow: Path = field(default_factory=lambda: Path(
        _env("COMFYUI_WORKFLOW", str(BASE_DIR / "comfyui_workflow_api.json"))))
    image_workers: int = field(default_factory=lambda: _env_int("IMAGE_WORKERS", 4))
    # Общ стилов суфикс -> визуално единство между всички сцени
    style_suffix: str = field(default_factory=lambda: _env(
        "STYLE_SUFFIX",
        "dark fantasy digital painting, epic cinematic composition, dramatic rim lighting, "
        "volumetric atmosphere, rich color grading, painterly brushwork, highly detailed"))
    negative_prompt: str = ("text, letters, watermark, logo, signature, ui, frame, blurry, lowres, "
                            "deformed hands, extra fingers, extra limbs, jpeg artifacts")
    grade_contrast: float = 1.06
    grade_saturation: float = 1.05
    grade_vignette: float = 0.22

    # ---------- Субтитри ----------
    burn_subs_master: bool = True
    burn_subs_shorts: bool = True
    subs_font: str = field(default_factory=lambda: _env("SUBS_FONT", "Arial"))
    subs_master_size: int = 52           # px при 1080p (скалира се автоматично за 4K)
    subs_master_max_chars: int = 64      # до 2 реда
    subs_short_size: int = 88
    subs_short_max_words: int = 3
    subs_short_uppercase: bool = True
    subs_short_highlight: bool = True    # TikTok стил: текущата дума светва в жълто

    # ---------- Shorts ----------
    shorts_layout: str = field(default_factory=lambda: _env("SHORTS_LAYOUT", "blur"))  # blur | crop
    shorts_title_overlay: bool = True
    shorts_max_sec: float = field(default_factory=lambda: _env_float("SHORTS_MAX_SEC", 60.0))

    # ---------- Произношение ----------
    pronunciations_file: Path = BASE_DIR / "pronunciations.json"

    # ---------- Скрипт ----------
    words_per_minute: float = 150.0      # за оценка на дължина преди генерация
    target_minutes: float = 10.0

    # ---------- Шрифтове за Pillow (thumbnail / placeholder) ----------
    pil_fonts: tuple = (
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )

    def finalize(self) -> "Config":
        """Изчислява производни стойности след CLI override-ите."""
        if self.resolution.lower() in ("4k", "2160", "2160p"):
            self.width, self.height = 3840, 2160
            self.master_bitrate = "40M"
        else:
            self.width, self.height = 1920, 1080
        if self.sample_rate % self.fps:
            raise ValueError("sample_rate трябва да се дели на fps без остатък")
        self.output_dir = Path(self.output_dir)
        return self
