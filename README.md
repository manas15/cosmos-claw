# LiveHere

> See the lease, not just the layout.

Turn listing photos + a short prompt into a shareable **“day in the life”** MP4
preview of what living in a place could feel like — morning light, afternoon
calm, evening wind-down, with a closing lease/availability card.

This is the **simplest first version**: a local web app that runs entirely on
your Mac with **no GPU and no cloud cost**. Video is produced by a local
FFmpeg-based generator (Ken Burns motion + time-of-day color grade + captions).

The real world-model backend (**NVIDIA Cosmos 3 Nano on Nebius GPU**) is wired
in behind a clean adapter — see [Switching to Cosmos](#switching-to-cosmos).

---

## Why not run Cosmos locally?

Cosmos 3 Nano (16B) requires an **NVIDIA CUDA GPU** (Ampere/Hopper/Blackwell)
and ~17+ GiB of VRAM. It **cannot run on Apple Silicon**. The project is
designed to run Cosmos on a Nebius cloud GPU and call it over an
OpenAI-compatible API. The local FFmpeg generator lets us build and demo the
entire product experience first, then flip a switch for real generation.

---

## Quick start

Requires Python 3.9+ and FFmpeg.

```bash
cd LiveHere

# 1. FFmpeg (one time)
brew install ffmpeg

# 2. Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Run
python -m app
#   or: uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>, add a few photos, describe the vibe, and click
**Generate**.

---

## How it works

```
photos/video + instructions
        │
        ▼
  storyboard.py   → timed scenes (morning → day → evening)
        │
        ▼
  generation/     → one clip per scene  (adapter: stub | nebius)
        │
        ▼
  assembly.py     → end card + concat → day_in_the_life.mp4
```

| File | Role |
|------|------|
| `app/main.py` | FastAPI server + UI + `/api/generate` |
| `app/storyboard.py` | Builds the timed scene plan |
| `app/generation/base.py` | `ClipGenerator` adapter interface |
| `app/generation/stub.py` | Local FFmpeg generator (default, free) |
| `app/generation/cosmos.py` | NVIDIA Cosmos 3 generator (hosted or self-hosted) |
| `app/decorate.py` | Shared finish: grade, captions, fades, normalize |
| `app/assembly.py` | End card + clip concatenation |
| `app/pipeline.py` | Orchestrates the whole run |

---

## Switching to real Cosmos 3 generation

The backend is selected via env vars — **no code change** to the UI/pipeline.
Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
# edit .env:
LIVEHERE_BACKEND=cosmos
COSMOS_BASE_URL=https://integrate.api.nvidia.com/v1   # from the model page snippet
COSMOS_API_KEY=nvapi-...                              # your key (keep secret)
```

The same adapter (`app/generation/cosmos.py`) works against either:

- **NVIDIA-hosted free endpoint** — on `build.nvidia.com`, open the `cosmos3-nano`
  model, click **Get API Key** (`nvapi-...`), and copy the base URL from the code
  snippet. Free credits, no GPU to manage. Cheapest path.
- **Self-hosted vLLM-Omni** — on any NVIDIA GPU (RunPod/Modal/Nebius/local):

```bash
vllm serve nvidia/Cosmos3-Nano --omni --host 0.0.0.0 --port 8000 --no-guardrails
# then COSMOS_BASE_URL=http://<host>:8000/v1
```

Cosmos can't run on Apple Silicon. Cost varies by host (free on NVIDIA's hosted
endpoint within credits; ~$0.3–4/hr on rented GPUs — shut it down when idle).

**Is the hosted endpoint live yet?** As of last test, NVIDIA's hosted
`cosmos3-nano` generation route (`https://ai.api.nvidia.com/v1/infer`) still
returns 404 (mid-rollout). Check anytime with:

```bash
.venv/bin/python -m app.probe
```

When it prints `LIVE!`, set `LIVEHERE_BACKEND=cosmos` in `.env` and restart.

**Don't want to wait?** Self-host Cosmos3-Nano and point LiveHere at it.
Cheapest path is **free**: deploy `deploy/modal_app.py` on Modal's $30/mo free
tier (no credit card). Full walkthrough — Modal (free), Vast.ai, RunPod — in
[`deploy/DEPLOY.md`](deploy/DEPLOY.md).

### Architecture note

Every backend returns a *raw* clip; `app/decorate.py` then finishes all clips
identically (time-of-day grade, caption chips, fades, normalize) so stub and
Cosmos output look consistent and concatenate cleanly.

---

## Status / roadmap

- [x] Local web UI (upload + instructions + playback/download)
- [x] FFmpeg stub generator (motion, grade, captions, end card)
- [x] Backend adapter seam for Cosmos
- [x] Cosmos 3 image→video call (`/v1/videos/sync`, hosted or self-hosted)
- [ ] Verify against the live NVIDIA free endpoint (needs API key)
- [ ] Tavily neighborhood enrichment → caption facts
- [ ] Geocoding + season inference from lease dates
