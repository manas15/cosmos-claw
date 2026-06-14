"""Central configuration for Cosmos Claw.

All tunables live here so switching the generation backend (stub <-> Nebius
Cosmos) is a single change driven by environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    # override=True so the local .env is the source of truth even if stale
    # vars linger in the shell environment.
    load_dotenv(override=True)
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

# Directory of Airbnb listing folders (each subfolder = one listing: photos + a
# PDF of the listing page). Defaults to ../Airbnbs next to the repo.
LISTINGS_DIR = Path(os.environ.get("LISTINGS_DIR", str(BASE_DIR.parent / "Airbnbs")))
# Web-servable JPEG thumbnails for listing photos (AVIF/HEIC normalized here).
THUMBS_DIR = OUTPUT_DIR / "thumbs"

# Which clip generator to use: "stub" (local FFmpeg, free) or "cosmos" (Cosmos 3).
GENERATION_BACKEND = os.environ.get("LIVEHERE_BACKEND", "stub").lower()

# Video render settings (kept modest so generation is fast on a laptop).
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 24
SCENE_DURATION = 4.0  # seconds per generated scene clip
END_CARD_DURATION = 5.0

# Upload limits.
MAX_FILES = 12
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif", ".avif"}
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
# Listing PDFs can be uploaded too; their text is auto-extracted as facts.
ALLOWED_DOC_EXTS = {".pdf"}
# Formats Pillow/ffmpeg can't reliably read; normalized to JPEG on upload.
NORMALIZE_IMAGE_EXTS = {".avif", ".heic", ".heif"}

# --- Listing trailer mode (landscape 16:9 property promo) ---
# Default experience now: a ~30s landscape Airbnb-style listing trailer (desktop).
DEFAULT_MODE = os.environ.get("LIVEHERE_MODE", "trailer").lower()
TRAILER_WIDTH = int(os.environ.get("TRAILER_WIDTH", "1920"))
TRAILER_HEIGHT = int(os.environ.get("TRAILER_HEIGHT", "1080"))
TRAILER_TARGET_BEATS = int(os.environ.get("TRAILER_TARGET_BEATS", "6"))
TRAILER_END_CARD_DURATION = float(os.environ.get("TRAILER_END_CARD_DURATION", "4.5"))
# OpenAI TTS voiceover.
TTS_MODEL = os.environ.get("TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.environ.get("TTS_VOICE", "nova")

# --- Info cards (map, price, rating) interleaved with the room b-roll ---
INFO_CARDS_ENABLED = os.environ.get("INFO_CARDS_ENABLED", "1") not in ("0", "false", "False")
INFO_CARD_DURATION = float(os.environ.get("INFO_CARD_DURATION", "3.6"))
# Map tiles (Carto dark — permissive, matches the dark UI). Attribution required.
MAP_TILE_URL = os.environ.get(
    "MAP_TILE_URL", "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"
)
# Nominatim (OSM geocoder) needs a descriptive User-Agent per its usage policy.
GEOCODER_USER_AGENT = os.environ.get("GEOCODER_USER_AGENT", "CosmosClaw-hackathon/0.2")
# Optional custom music bed (mp3/wav); if unset we synthesize a soft pad.
MUSIC_PATH = os.environ.get("MUSIC_PATH", "")
MUSIC_VOLUME = float(os.environ.get("MUSIC_VOLUME", "0.16"))

# Font used for on-screen captions. macOS ships these by default.
_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def find_font() -> str | None:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


FONT_PATH = find_font()

# --- Cosmos 3 settings (only used when GENERATION_BACKEND == "cosmos") ---
# Works with the NVIDIA-hosted free endpoint (build.nvidia.com) or any
# self-hosted vLLM-Omni server (RunPod/Modal/Nebius/local).
COSMOS_BASE_URL = os.environ.get("COSMOS_BASE_URL", "").rstrip("/")
COSMOS_API_KEY = os.environ.get("COSMOS_API_KEY", "")
COSMOS_MODEL = os.environ.get("COSMOS_MODEL", "nvidia/Cosmos3-Nano")

# API style: "auto" | "nvidia_infer" (build.nvidia.com) | "vllm_omni" (self-hosted)
COSMOS_API_STYLE = os.environ.get("COSMOS_API_STYLE", "auto")
COSMOS_INFER_PATH = os.environ.get("COSMOS_INFER_PATH", "/infer")  # nvidia_infer
COSMOS_VIDEOS_PATH = os.environ.get("COSMOS_VIDEOS_PATH", "/videos/sync")  # vllm_omni

# Cosmos generation params.
COSMOS_NUM_FRAMES = int(os.environ.get("COSMOS_NUM_FRAMES", "97"))
COSMOS_FPS = int(os.environ.get("COSMOS_FPS", "24"))
# Realism preset: more steps refines detail; LOW guidance keeps the model
# faithful to the reference photo (high guidance over-follows the text and
# invents/warps things); LOW flow-shift keeps motion small and steady.
COSMOS_STEPS = int(os.environ.get("COSMOS_STEPS", "50"))
COSMOS_GUIDANCE = float(os.environ.get("COSMOS_GUIDANCE", "4.0"))
COSMOS_FLOW_SHIFT = float(os.environ.get("COSMOS_FLOW_SHIFT", "7.0"))
COSMOS_RESOLUTION = os.environ.get("COSMOS_RESOLUTION", "720_16_9")  # nvidia_infer
COSMOS_SIZE = os.environ.get("COSMOS_SIZE", "1280x720")  # vllm_omni
COSMOS_TIMEOUT = int(os.environ.get("COSMOS_TIMEOUT", "900"))  # seconds
# Retry a clip this many times if the tunnel blips mid-transfer.
COSMOS_MAX_RETRIES = int(os.environ.get("COSMOS_MAX_RETRIES", "3"))

# Appended to every generation prompt to anchor output to the real photo.
COSMOS_FIDELITY_SUFFIX = os.environ.get(
    "COSMOS_FIDELITY_SUFFIX",
    " Real footage shot on a professional camera — live-action photography, NOT a "
    "video game, NOT CGI, NOT a 3D render, NOT animation. The scene stays "
    "photorealistic and identical to the reference photo: the same room, "
    "furniture, objects, colors, materials, and lighting. Nothing is added, "
    "removed, moved, or transformed. Only the camera moves — very slowly, "
    "smoothly, and steadily, with a small motion. Real estate b-roll, true to life.",
)
# Strong negative prompt to suppress the usual i2v failure modes AND the
# synthetic "video game / rendered" look the user explicitly wants to avoid.
COSMOS_NEGATIVE_PROMPT = os.environ.get(
    "COSMOS_NEGATIVE_PROMPT",
    "video game, video game screenshot, game engine, unreal engine, unity, "
    "3D render, CGI, computer graphics, rendered, raytraced, animation, animated, "
    "cartoon, anime, cel shaded, plastic, waxy, glossy, synthetic, fake, "
    "videogame, simulation, virtual, digital art, illustration, painting, "
    "people, person, human, hands, blurry, distorted, low quality, text, "
    "watermark, morphing, warping, melting, bending walls, deforming, flickering, "
    "shifting layout, extra objects, new furniture, duplicated objects, "
    "hallucination, surreal, oversaturated, fisheye, wobbling, shaking, "
    "fast motion, zoom blur",
)

# --- GPT-4o "director": turns raw uploads into a first-person POV storyboard ---
# When OPENAI_API_KEY is set, the pipeline uses the director; otherwise it falls
# back to the simple morning/day/evening storyboard.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

# --- Tavily: hackathon search partner (location / nearby / POI lookups) ---
# Badge shows "connected" when this key is present.
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
DIRECTOR_TARGET_BEATS = int(os.environ.get("DIRECTOR_TARGET_BEATS", "7"))
