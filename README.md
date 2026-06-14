<div align="center">

# 🪐 Cosmos Claw

### Your always-on, AI-native videographer.

**It shoots, directs, and edits social videos for your venue — on autopilot, in every format, without a film crew.**

*Built for the **Yacht Hackathon** — by [@ComposioHQ](https://github.com/ComposioHQ), [@nebius](https://github.com/nebius), [@tavily-ai](https://github.com/tavily-ai) & [@openclaw](https://github.com/openclaw).*

[![Watch the demo](assets/demo-poster.jpg)](https://x.com/i/status/2065370878519468221)

▶️ **[Watch the 60s demo on X](https://x.com/i/status/2065370878519468221)** &nbsp;·&nbsp; or play it inline below 👇

<video src="https://github.com/manas15/cosmos-claw/raw/main/assets/demo.mp4" controls muted loop playsinline width="80%"></video>

</div>

---

## The pitch

Every venue needs a steady stream of video, but a videographer is expensive,
slow, and one-shoot-at-a-time. **Cosmos Claw is the always-on, AI-native
alternative** — a videographer that never sleeps.

Point it at any venue — a short-let, a restaurant or cafe, a bar or club — and it
does the whole job:

- **Directs** the shoot — GPT-4o reads your photos and writes the storyboard.
- **Films** real motion — NVIDIA Cosmos 3 *generates* video frame-by-frame (not a
  slideshow), so the space actually moves.
- **Edits** the cut — captions, neighborhood context, voiceover, and music.
- **Delivers everywhere** — one source, every feed (Reels/TikTok 9:16, IG 1:1 &
  4:5, YouTube 16:9).

No crew, no call sheet, no edit bay. Just your existing photos in, a full social
calendar out — on repeat.

> Looking forward to expanding this on the Yacht — SF is *soooo* amazing 🌉⛵️

---

## The stack

| Layer | What we used |
|-------|--------------|
| 🎥 **Video model** | **NVIDIA Cosmos 3 Nano** — image→video, **self-deployed by us** |
| ⚡ **Compute** | **[Nebius AI Cloud](https://nebius.com)** — NVIDIA® **H200 NVLink** GPUs |
| 🔎 **Neighborhood research** | **[Tavily](https://tavily.com)** — enriches each property with real local context |
| 🎬 **Director** | **GPT-4o (vision)** — reads the photos and writes the storyboard |
| 🗺️ **Maps & info cards** | **OpenStreetMap** — location, transit & nearby spots |
| 🔊 **Audio** | **OpenAI TTS** voiceover + a soft synthesized music bed |
| 🧩 **App** | **FastAPI** + a per-venue Studio UI, FFmpeg for finishing/stitching |

We didn't just call a hosted API — we **stood up Cosmos 3 Nano ourselves** on
Nebius H200 NVLink GPUs (vLLM-Omni, OpenAI-compatible) and drove it end-to-end.
Tavily researches the surrounding neighborhood so every second of the video
carries the context a viewer needs to say *yes*.

<sub>Shout-out to the partners: **@ship_builders · @nebiusai · @nvidia · @composio · @tavilyai · @openclaw**</sub>

---

## How it works

```
listing photos + PDF facts
        │
        ▼
  GPT-4o director  ──→  storyboard + which info beats to show
        │                     │
        │                     ├─ Tavily ─→ neighborhood research
        │                     └─ OSM ────→ map / transit / nearby cards
        ▼
  NVIDIA Cosmos 3 Nano  ──→  one photorealistic clip per beat
   (self-hosted on Nebius H200)
        │
        ▼
  decorate + assembly  ──→  grade · captions · voiceover · music · stitch
        │
        ▼
   30s landscape trailer.mp4
```

| File | Role |
|------|------|
| `app/main.py` | FastAPI server + Studio UI + generation API |
| `app/trailer.py` | GPT-4o "director" — storyboard + info-beat planning |
| `app/infocards.py` | Map / price / neighborhood cards (OpenStreetMap) |
| `app/generation/cosmos.py` | NVIDIA Cosmos 3 image→video adapter |
| `app/generation/stub.py` | Free local FFmpeg fallback generator |
| `app/decorate.py` | Shared finish: grade, captions, fades, normalize |
| `app/audio.py` | TTS voiceover + music bed + mux |
| `app/assembly.py` | Info beats + clip concatenation |
| `app/pipeline.py` | Orchestrates the whole run |
| `app/brand.py` | Per-project brand dossier (memory + consistency) |
| `app/marketing_agent.py` | GPT-4o marketing manager: research → brand → brief |
| `app/agent.py` | Terminal CLI to drive the agent + fire renders |

---

## The marketing manager agent

Cosmos Claw isn't a one-shot tool — it's an **always-on, AI-native videographer**
with a marketing manager working alongside it. For every project there's a
persistent **brand dossier** (`outputs/listing_{id}_brand.json`) that is the single
source of truth across every video:

- **Research** — Tavily web search (or GPT-4o fabrication when there's no key) for
  neighborhood, transit and competitive context.
- **Brand** — GPT-4o writes the positioning (one-liner, audience, tone, voice,
  music) and **locks in any missing facts as durable assumptions** that are *never
  overwritten*, so price/amenities/host story stay identical in every future cut.
- **Brief** — GPT-4o (vision) picks the strongest uploaded photos, **orders them
  like a tour**, and writes hooks, captions, a ~75-word voiceover, the music mood,
  the TTS voice, and the target format. Generation honors this brief.

```
research ─→ build_brand (durable assumptions) ─→ build_brief (assets/order/VO/music)
                                   │
                                   ▼
                    brand dossier  ──→  every generation grounds on it
```

Drive it from the **Agent Loop** tab in the UI (Run marketing manager → review the
brief → pick a format → Generate), or entirely from the terminal:

```bash
python -m app.agent list                                  # projects + dossier status
python -m app.agent run la-house-1 --format reel          # research → brand → brief
python -m app.agent dossier show la-house-1               # inspect the dossier
python -m app.agent assume la-house-1 price "$245/night"  # lock a consistent fact
python -m app.agent brief auto la-house-1 --format story  # regenerate the brief (GPT)
python -m app.agent generate la-house-1 --format youtube  # render via the live API
```

Formats: `reel`, `tiktok`, `shorts`, `story`, `snap` (9:16), `youtube` (16:9),
`square` (1:1), `portrait` (4:5). The render canvas switches automatically and the
map/price/neighborhood info cards re-flow to match.

---

## Quick start

Requires Python 3.9+ and FFmpeg.

```bash
cd LiveHere

brew install ffmpeg                 # one time

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                # add your keys (OpenAI, Tavily, Cosmos)

python -m app                       # → http://127.0.0.1:8000
```

Open <http://127.0.0.1:8000>, pick a listing, tweak the auto-filled details, and
hit **Generate**. With no GPU configured it runs on the free local FFmpeg stub;
point it at Cosmos for the real thing (below).

---

## Running the real Cosmos 3 backend

The generation backend is swapped purely via env vars — **no code change** to the
UI or pipeline.

```bash
# .env
LIVEHERE_BACKEND=cosmos
COSMOS_API_STYLE=vllm_omni
COSMOS_BASE_URL=http://<your-gpu-host>:8000/v1
COSMOS_API_KEY=...
```

We self-hosted it on a **Nebius H200 NVLink** instance with vLLM-Omni:

```bash
vllm serve nvidia/Cosmos3-Nano --omni --host 0.0.0.0 --port 8000 --no-guardrails
```

Full deploy walkthrough (Nebius / Modal / RunPod) is in
[`deploy/DEPLOY.md`](deploy/DEPLOY.md). Cosmos can't run on Apple Silicon — keep
the GPU instance up only while generating, and tear it down when idle.

---

<div align="center">
<sub>Cosmos Claw · made with ☕ for the Yacht Hackathon · Composio × Nebius × Tavily</sub>
</div>
