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

# Upload limits.
MAX_FILES = 12
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif", ".avif"}
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
# Listing PDFs can be uploaded too; their text is auto-extracted as facts.
ALLOWED_DOC_EXTS = {".pdf"}
# Formats Pillow/ffmpeg can't reliably read; normalized to JPEG on upload.
NORMALIZE_IMAGE_EXTS = {".avif", ".heic", ".heif"}

# Default render canvas (vertical 9:16 for reels/TikTok). Used as the fallback
# size when a format preset isn't passed explicitly.
TRAILER_WIDTH = int(os.environ.get("TRAILER_WIDTH", "1080"))
TRAILER_HEIGHT = int(os.environ.get("TRAILER_HEIGHT", "1920"))

# Social delivery formats the videographer renders to. Vertical short-form only
# (the two platforms the agents grow). Each maps to an output canvas; clips are
# scaled/cropped to fit. Add your own preset here to target another surface.
FORMAT_PRESETS: dict[str, dict] = {
    "reel": {"label": "Instagram Reel", "w": 1080, "h": 1920, "ratio": "9:16"},
    "tiktok": {"label": "TikTok", "w": 1080, "h": 1920, "ratio": "9:16"},
}
DEFAULT_FORMAT = os.environ.get("DEFAULT_FORMAT", "reel").lower()
# OpenAI TTS voiceover.
TTS_MODEL = os.environ.get("TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.environ.get("TTS_VOICE", "nova")

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
# Cinematic motion (v2): a beat's motion_strength (0..1) scales flow_shift around
# the base above so bold moves get more motion. effective = base * (1 + gain*(s-0.5)).
# Tune the base via the Phase F sweep; this just spreads it per beat.
COSMOS_MOTION_FLOW_GAIN = float(os.environ.get("COSMOS_MOTION_FLOW_GAIN", "0.6"))
COSMOS_FLOW_SHIFT_MIN = float(os.environ.get("COSMOS_FLOW_SHIFT_MIN", "5.0"))
COSMOS_FLOW_SHIFT_MAX = float(os.environ.get("COSMOS_FLOW_SHIFT_MAX", "15.0"))
# Best-of-N: render this many takes per beat (varied seed/flow), auto-rank, keep
# all of them for the Studio take picker. 1 = single take (default / cheapest).
COSMOS_BEST_OF_N = int(os.environ.get("COSMOS_BEST_OF_N", "1"))
COSMOS_RESOLUTION = os.environ.get("COSMOS_RESOLUTION", "720_16_9")  # nvidia_infer
COSMOS_SIZE = os.environ.get("COSMOS_SIZE", "1280x720")  # vllm_omni
COSMOS_TIMEOUT = int(os.environ.get("COSMOS_TIMEOUT", "900"))  # seconds
# Retry a clip this many times if the tunnel blips mid-transfer.
COSMOS_MAX_RETRIES = int(os.environ.get("COSMOS_MAX_RETRIES", "3"))

# Appended to every generation prompt to anchor output to the real photo.
# v2: softened to ALLOW deliberate, cinematic camera movement and natural ambient
# motion (curtains, water, steam, foliage) while keeping the architecture and
# contents identical and photoreal. The old wording ("only the camera moves very
# slowly, small motion") is what made v1 clips feel static.
COSMOS_FIDELITY_SUFFIX = os.environ.get(
    "COSMOS_FIDELITY_SUFFIX",
    " Real footage shot on a professional cinema camera — live-action photography, "
    "NOT a video game, NOT CGI, NOT a 3D render, NOT animation. The space stays "
    "photorealistic and identical to the reference photo: the same room, furniture, "
    "objects, colors, materials, and lighting — nothing is added, removed, or "
    "transformed. The camera move is smooth, deliberate, and cinematic, with real "
    "parallax and depth; any in-scene motion is natural and subtle. Architecture and "
    "layout stay perfectly stable. Premium real-estate b-roll, true to life.",
)
# Negative prompt to suppress i2v failure modes AND the synthetic "video game /
# rendered" look. v2: dropped "fast motion"/"zoom blur" (they fought the cinematic
# motion goal); kept the anti-CGI and anti-warp/deform terms that protect realism.
COSMOS_NEGATIVE_PROMPT = os.environ.get(
    "COSMOS_NEGATIVE_PROMPT",
    "video game, video game screenshot, game engine, unreal engine, unity, "
    "3D render, CGI, computer graphics, rendered, raytraced, animation, animated, "
    "cartoon, anime, cel shaded, plastic, waxy, glossy, synthetic, fake, "
    "videogame, simulation, virtual, digital art, illustration, painting, "
    "people, person, human, hands, blurry, distorted, low quality, text, "
    "watermark, morphing, warping, melting, bending walls, deforming, flickering, "
    "shifting layout, extra objects, new furniture, duplicated objects, "
    "hallucination, surreal, oversaturated, fisheye, wobbling, shaking",
)

# --- Pluggable video backend (VIDEO_*) ----------------------------------
# Generic OpenAI-compatible image->video server (LIVEHERE_BACKEND=openai_video).
# Anything that speaks a vLLM-Omni-style multipart /videos endpoint works here
# without writing a new adapter; point it at your own model.
VIDEO_BASE_URL = os.environ.get("VIDEO_BASE_URL", "").rstrip("/")
VIDEO_API_KEY = os.environ.get("VIDEO_API_KEY", "")
VIDEO_MODEL = os.environ.get("VIDEO_MODEL", "")
VIDEO_VIDEOS_PATH = os.environ.get("VIDEO_VIDEOS_PATH", "/videos/sync")
VIDEO_SIZE = os.environ.get("VIDEO_SIZE", "1280x720")
VIDEO_TIMEOUT = int(os.environ.get("VIDEO_TIMEOUT", "900"))
VIDEO_MAX_RETRIES = int(os.environ.get("VIDEO_MAX_RETRIES", "3"))
# Extra provider-specific form fields as a JSON object, merged into the request.
VIDEO_EXTRA_PARAMS = os.environ.get("VIDEO_EXTRA_PARAMS", "")
# API keys for the optional hosted-provider adapters (only the one you use
# needs to be set; each adapter's SDK is an optional/lazy import).
RUNWAY_API_KEY = os.environ.get("RUNWAY_API_KEY", "")
LUMA_API_KEY = os.environ.get("LUMA_API_KEY", "")
KLING_API_KEY = os.environ.get("KLING_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")  # Veo (via google-genai)
PIKA_API_KEY = os.environ.get("PIKA_API_KEY", "")
# Open / self-hostable models (LTX-Video, Wan, Stable Video Diffusion) are run
# through a local diffusers pipeline or your own server URL.
LTX_MODEL = os.environ.get("LTX_MODEL", "Lightricks/LTX-Video")
WAN_MODEL = os.environ.get("WAN_MODEL", "Wan-AI/Wan2.1-I2V-14B-480P-Diffusers")
SVD_MODEL = os.environ.get("SVD_MODEL", "stabilityai/stable-video-diffusion-img2vid-xt")

# --- GPT-4o brain: vision (photo analysis), ideation, captions, voiceover ---
# When OPENAI_API_KEY is set the agents use GPT-4o; otherwise they fall back to
# generic prompts/labels so the app still runs offline.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

# --- Tavily: hackathon search partner (location / nearby / POI lookups) ---
# Badge shows "connected" when this key is present.
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# --- Long-horizon durability (the loop runs for months) -----------------
# The dossier's append-only lists (activity, research) are compacted once they
# pass these caps: the trimmed portion is folded into a one-line "chronicle"
# milestone so nothing meaningful is lost while the file stays bounded.
ACTIVITY_CAP = int(os.environ.get("ACTIVITY_CAP", "200"))
RESEARCH_CAP = int(os.environ.get("RESEARCH_CAP", "60"))
CHRONICLE_CAP = int(os.environ.get("CHRONICLE_CAP", "120"))
# Weekly reflection cadence (seconds): distil lessons + write a chronicle entry.
REFLECT_EVERY = int(os.environ.get("REFLECT_EVERY", str(7 * 24 * 3600)))
# Disk retention: keep at most this many recent video versions per project on
# disk; older, un-posted cuts are pruned. 0 = keep everything (no pruning).
VERSION_RETENTION = int(os.environ.get("VERSION_RETENTION", "60"))
