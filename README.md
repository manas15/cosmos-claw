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

**Thirty seconds.** That's all a viewer gives your venue before they swipe. To
win those seconds you need a *constant* stream of video — but a human
videographer is expensive, slow, and shoots one thing at a time.

**Cosmos Claw is the always-on, AI-native alternative: a videographer _and_ a
marketing manager that never sleep.** Point it at any venue — a short-let, a café,
a bar — and it runs the whole studio on autopilot:

- **Studies** your space — GPT-4o (vision) labels every photo and learns what each
  room is.
- **Brands** it — invents the positioning and *locks in* the missing facts (price,
  story, amenities) so they stay identical across every video.
- **Ideates** like a manager — brainstorms a fresh campaign for each post (angle,
  hook, photo order, format, music, voice) that's different from everything it has
  shipped before.
- **Films** real motion — NVIDIA Cosmos 3, a **world model built for robotics**,
  doesn't pan over stills; it generates a first-person POV that physically *walks
  into the room*.
- **Voices & cuts** it — a unique GPT-written **voiceover** over a mood-matched
  music bed, cross-faded into the right aspect ratio.
- **Delivers everywhere** — ready-to-post cards (caption, hashtags, handle,
  recommended audio) in every feed: Reels/TikTok 9:16, IG 1:1 & 4:5, YouTube 16:9.

No crew, no call sheet, no edit bay. Just your existing photos in — a full,
on-brand social calendar out, on repeat.

### …and right now, it's running.

As you read this, two workers are filming **in parallel** — pumping out a stream
of ready-to-post Reels and TikToks for two San Francisco venues at once, each with
its own AI voiceover — all on a Cosmos 3 model **we deployed ourselves** on Nebius
H200s. It pauses when the network blips and resumes on its own. Truly always-on.

> Looking forward to expanding this on the Yacht — SF is *soooo* amazing 🌉⛵️

---

## See it in action

**1 — Raw photos in.** Drop a venue's existing images into the project. That's the
only input. *(Here: the Alamo Square Hacker House — bedrooms, gym, coworking.)*

![Raw images tab](assets/screens/raw-images.jpg)

**2 — The marketing manager's memory.** An OpenClaw-style GPT-4o manager researches
the venue, locks in a consistent brand (positioning, audience, tone, pitch), writes
the voiceover, and picks the assets & order — the durable memory every video is
grounded on.

![Memory tab — brand dossier](assets/screens/memory.jpg)

**3 — Ready-to-post cuts out.** The Agent Loop streams everything the videographer
does in real time, and each published cut is a ready-to-post package: video +
caption + recommended audio (music & voice) + handle, ready to download or push to
the channel.

![Agent Loop — published cut](assets/screens/agent-loop.jpg)

---

## The stack

| Layer | What we used |
|-------|--------------|
| 🎥 **Video model** | **NVIDIA Cosmos 3 Nano** — a *world model* (built for robotics/embodied POV), **self-deployed by us** for first-person walk-throughs |
| ⚡ **Compute** | **[Nebius AI Cloud](https://nebius.com)** — NVIDIA® **H200 NVLink** GPUs |
| 🧠 **Manager + director** | **GPT-4o (vision)** — studies the photos, brands the venue, ideates each campaign & storyboard |
| 🔎 **Neighborhood research** | **[Tavily](https://tavily.com)** — enriches each venue with real local context |
| 🗺️ **Maps & info cards** | **OpenStreetMap** — location, transit & nearby spots |
| 🔊 **Audio** | **OpenAI TTS** — a unique per-cut voiceover over a mood-matched music bed |
| 🧩 **App** | **FastAPI** Studio UI + an always-on `marketing_loop` driver, FFmpeg for cutting/transitions |

We didn't just call a hosted API — we **stood up Cosmos 3 Nano ourselves** on
Nebius H200 NVLink GPUs (vLLM-Omni, OpenAI-compatible) and drove it end-to-end.
Tavily researches the surrounding neighborhood so every second of the video
carries the context a viewer needs to say *yes*.

<sub>Shout-out to the partners: **@ship_builders · @nebiusai · @nvidia · @composio · @tavilyai · @openclaw**</sub>

---

## How it works

```
venue photos + facts
        │
        ▼
  GPT-4o manager  ──→  brand dossier (positioning + durable assumptions)
        │                     │
        │                     ├─ Tavily ─→ neighborhood research
        │                     └─ ideate ─→ one fresh campaign (angle · photos ·
        │                                   format · music · voice · caption · VO)
        ▼
  NVIDIA Cosmos 3 Nano  ──→  a short first-person POV clip per beat
   (world model, self-hosted on Nebius H200)
        │
        ▼
  transitions + audio  ──→  cross-fade · GPT voiceover · mood music · reframe
        │
        ▼
  ready-to-post cut.mp4  ──→  Agent Loop feed (caption · hashtags · audio)
        │
        └──────────────  loop: next idea, next venue (in parallel)
```

| File | Role |
|------|------|
| `scripts/marketing_loop.py` | **The always-on loop**: study → ideate → film → voice → publish, per venue (parallel-safe) |
| `scripts/cosmos_montage.py` | Terminal montage: GPT vision per photo → Cosmos clips → fast transitions |
| `app/marketing_agent.py` | GPT-4o marketing manager: research → brand → brief |
| `app/brand.py` | Per-venue brand dossier (memory, durable assumptions, social posts) |
| `app/main.py` | FastAPI server + Studio UI + generation API |
| `app/trailer.py` | GPT-4o "director" — storyboard, shot/motion + walk-through mode |
| `app/generation/cosmos.py` | NVIDIA Cosmos 3 image→video adapter (motion → flow-shift) |
| `app/generation/stub.py` | Free local FFmpeg fallback generator |
| `app/transitions.py` | Fast cross-fade montage into any aspect ratio (xfade) |
| `app/curation.py` | Best-of-N take scoring (motion energy + stability) |
| `app/audio.py` | TTS voiceover + mood music bed + duck-and-mux |
| `app/infocards.py` | Map / price / neighborhood cards (OpenStreetMap) |
| `app/pipeline.py` | Orchestrates a single run (best-of-N, info beats, finish) |
| `app/agent.py` | Terminal CLI to drive the manager + fire renders |
| `deploy/tunnel_keeper.sh` | Self-healing SSH tunnel to the Nebius GPU |

---

## The always-on marketing manager

Cosmos Claw isn't a one-shot tool — it's a **loop**. A persistent **brand dossier**
(`outputs/listing_{id}_brand.json`) is the single source of truth per venue, and an
autonomous manager works against it the way a real social-media manager would —
forever:

1. **Study** — GPT-4o vision builds an *asset index*: what every uploaded photo is
   (cached, so it's paid for once).
2. **Ideate** — brainstorms ONE fresh campaign *distinct from past themes*: the
   angle, which photos to use (ordered like a story), the social format, music
   mood, TTS voice, a ready-to-post caption + hashtags, and a ~25s **voiceover
   script**.
3. **Film** — turns the chosen photos into short, first-person Cosmos clips.
4. **Cut** — cross-fades them into the campaign's aspect ratio and mixes the GPT
   voiceover over a mood-matched music bed.
5. **Publish** — drops a ready-to-post card into the **Agent Loop** feed and logs
   every step to the dossier timeline.

…then it does it again, with a brand-new idea. Run **one worker per venue** and
they generate **in parallel**, so multiple feeds fill at once:

```bash
# one always-on worker per project, running concurrently
python scripts/marketing_loop.py --projects la-house-1   --tag la --max-videos 6
python scripts/marketing_loop.py --projects hacker-house --tag hh --max-videos 6
```

It's built to run unattended: a **live endpoint probe before every shot** means a
Wi-Fi/tunnel blip just *pauses* the shoot and resumes when the connection is back —
no babysitting, no half-burned campaigns. A self-healing SSH tunnel keeper
(`deploy/tunnel_keeper.sh`) keeps the link to the GPU alive underneath.

**Consistency is the trick.** Whatever the manager makes up, it makes up *once*:
`build_brand` writes the missing facts as **durable assumptions that are never
overwritten**, so price, amenities and host story stay identical across every cut.

```
study (vision asset index) ─→ ideate (fresh campaign) ─→ film ─→ voice + cut ─→ publish ─┐
        ▲                                                                                 │
        └───────────────────────  grounded on the brand dossier  ◀──────────────────────┘
```

Prefer to drive it by hand? The same brain runs from the **Agent Loop** tab in the
UI, or from the terminal:

```bash
python -m app.agent list                                  # projects + dossier status
python -m app.agent run la-house-1 --format reel          # research → brand → brief
python -m app.agent assume la-house-1 price "$245/night"  # lock a consistent fact
python -m app.agent generate la-house-1 --format youtube  # render via the live API
```

Formats: `reel`, `tiktok`, `shorts`, `story`, `snap` (9:16), `youtube` (16:9),
`square` (1:1), `portrait` (4:5). The render canvas switches automatically.

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
