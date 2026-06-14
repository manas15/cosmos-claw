# AGENTS.md — Cosmos Claw

Internal working notes + agent context for this repo. Kept updated as we build so
we (human + agent) stay organized. Not user-facing docs — see `README.md` for that.

## What this is

**Cosmos Claw** — "Your Always-On AI-native Videographer." Turns a venue's photos
(Airbnb listing, café, bar, etc.) into cinematic, scroll-stopping social videos.
GPT-4o directs; **NVIDIA Cosmos 3 Nano** (self-hosted, vLLM-Omni) generates the
motion; ffmpeg stitches with fast transitions, music, and voiceover.

## Architecture (key modules)

- `app/config.py` — central config (Cosmos params, motion/best-of-N knobs, prompts).
- `app/trailer.py` — GPT-4o vision storyboard "director": shot vocabulary, per-beat
  `shot`/`motion_strength`/`ambient`, walkthrough (first-person POV) mode.
- `app/generation/` — clip backends:
  - `base.py` — `Scene` dataclass + `ClipGenerator` ABC (`generate_clip(scene,out,variant)`).
  - `cosmos.py` — live Cosmos 3 via vLLM-Omni (`/v1/videos/sync`); seed/flow_shift per take.
  - `stub.py` — local ffmpeg fallback (zoompan motion), no GPU needed.
  - `factory.py` — picks backend from `LIVEHERE_BACKEND` / `--backend`.
- `app/curation.py` — scores best-of-N takes (motion energy + smoothness) → `pick_best`.
- `app/transitions.py` — `xfade_montage`: fast, varied cross-transitions between clips.
- `app/pipeline.py` — orchestrates plan → best-of-N render → curate → stitch + manifest.
- `app/marketing_agent.py` + `app/brand.py` — OpenClaw-style marketing manager:
  persistent brand dossier, research, briefs (assets/order/music/voice/format), posts.
- `app/agent.py` — terminal CLI to drive the agent manually.
- `app/main.py` — FastAPI app + routes (generate, versions, re-stitch).
- `app/static/` — UI (Soul / Images & Videos / Memory / Human Drive / Agent Loop tabs).
- `scripts/cosmos_montage.py` — terminal montage: GPT-4o vision per photo → Cosmos
  short clips → fast transitions + music. The main path we're running live now.
- `scripts/test_realism.py` — Cosmos param sweep → labeled ffmpeg contact sheet.

## Backend / infra status

- Live Cosmos 3 Nano served on a **Nebius H200** VM (`cosmos@195.242.31.145`) via
  Docker `vllm/vllm-omni:cosmos3`, OpenAI-compatible on `:8000`.
- Reached locally over an SSH tunnel: `127.0.0.1:8800 -> VM:8000`
  (`.env` `COSMOS_BASE_URL=http://127.0.0.1:8800/v1`).
- **Guardrail gotcha:** Cosmos3-Nano pulls the *gated* `nvidia/Cosmos-1.0-Guardrail`
  (401 without HF auth). The pinned image predates `--no-guardrails`, but ships
  `examples/online_serving/cosmos3/cosmos3_no_guardrails.yaml`. Launch with
  `--deploy-config <that yaml>` → guardrails off, no HF token required.
- Server launch script lives on the VM at `~/run_cosmos.sh` (detached via `setsid`,
  logs to `~/cosmos.log`). ~46s/clip at 50 steps on the H200.
- Secrets (`OPENAI_API_KEY`, NVIDIA key) live in `.env` only — gitignored.

## Worklog

### 2026-06-14
- Brought a stopped Nebius H200 VM back online; fresh disk (no image/weights), so
  re-pulled `vllm/vllm-omni:cosmos3` and re-downloaded Cosmos3-Nano weights (~33 GB).
- Hit the gated-guardrail 401 at startup; fixed by launching with the bundled
  `cosmos3_no_guardrails.yaml` deploy config (documented above). Server now serves
  `/v1/videos/sync`.
- Opened the SSH tunnel and validated the full montage path on live Cosmos with a
  3-photo smoke run (GPT-4o vision labels + fast transitions + music → 5.5s).
- Kicked off the full 14-photo `la-house-1` montage → `uploads/montage.mp4`.
- Started keeping this AGENTS.md worklog + committing to GitHub as we go.
