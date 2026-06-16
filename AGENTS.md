# AGENTS.md — Cosmos Claw

Internal working notes + agent context for this repo. Kept updated as we build so
we (human + agent) stay organized. Not user-facing docs — see `README.md` for that.

## What this is

**Cosmos Claw** — "Your Always-On AI-native Videographer." An *experiment*: two
agents (a marketing manager + a videographer) that run locally 24/7, for months,
chasing north-star goals (followers / views / community) and getting less sloppy
over time via a human feedback loop. **Use-case agnostic** (any venue/brand, not
just rentals) and **model agnostic** (Cosmos 3 by default, any `ClipGenerator`
plug-in). Architecture follows planner / doer / checker ("loops and goals").

## Architecture (key modules)

- `app/config.py` — central config (Cosmos params, motion/best-of-N knobs, prompts,
  pluggable `VIDEO_*` backend, durability caps: `ACTIVITY_CAP`/`VERSION_RETENTION`/
  `REFLECT_EVERY`).
- **Planner** — `app/marketing_agent.py` + `app/brand.py`: GPT-4o manager. Persistent
  brand dossier (memory, durable assumptions, lessons, chronicle, posts), research,
  goal-driven briefs. Use-case agnostic (free-form facts + `use_case`; hashtags/CTA
  derived from the dossier).
- **Doer** — `app/videographer.py`: `make_reel()` is the canonical skill (auto frame
  count → best-of-N film → cut → voice → publish version). `app/vision.py` does the
  GPT-4o photo analysis (labels + per-shot prompts). `app/transitions.py` +
  `app/audio.py` for xfade montage + TTS voiceover/mood music.
- **Checker** — `app/feedback.py`: `record_decision` (post/discard), `record_performance`,
  `pending/due_reviews`, `what_performed`/`slop_to_avoid`, `derive_lessons` (GPT-4o).
- **Goals** — `app/goals.py`: targets/progress, `ingest_performance`, `all_met`, and
  `gap_hint` (biggest gap → the next ideation focus).
- `app/generation/` — pluggable clip backends:
  - `base.py` — `Scene` dataclass + `ClipGenerator` ABC (`generate_clip` + `available`/`live`).
  - `cosmos.py` — live Cosmos 3 via vLLM-Omni (`/v1/videos/sync`); seed/flow_shift per take.
  - `openai_video.py` — generic OpenAI-compatible img→video server (`VIDEO_*`).
  - `providers/` — optional hosted/open adapters (runway, luma, kling, veo, pika, ltx,
    wan, svd) with lazy SDK imports + a shared `_common.py`.
  - `stub.py` — local ffmpeg fallback (zoompan motion), no GPU needed.
  - `factory.py` — `BACKENDS` registry + dotted-path "bring your own model" loader.
- `app/curation.py` — scores best-of-N takes (motion energy + smoothness) → `pick_best`.
- `app/agent.py` — terminal CLI: `run`, `assume`, `generate`, `goal`, `feedback`.
- `app/main.py` — FastAPI app + routes (generate, versions, goals, feedback decision/perf).
- `app/static/` — UI (Soul / Images & Videos / Memory / Human Drive / Agent Loop tabs +
  north-star header + per-cut feedback block).
- `scripts/marketing_loop.py` — the always-on driver: study → ideate → film → publish →
  learn, `--until-goals` stop, per-cut hygiene (compact + prune + weekly reflect).
- `scripts/cosmos_montage.py` — thin terminal wrapper around `videographer.make_reel`.
- `deploy/run_local.sh` + `com.cosmosclaw.loop.plist` + `cosmosclaw.service` — 24/7 daemon.

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

### 2026-06-15 (the experiment: loops, goals, feedback, pluggable model)
- Reframed the product as an experiment — two agents running 24/7 for months toward
  north-star goals, improving via a feedback loop. Implemented planner/doer/checker:
  - **Doer**: extracted `app/videographer.py` (`make_reel`) + `app/vision.py` from the
    old trailer/director/pipeline cluster; deleted `trailer.py`, `director.py`,
    `pipeline.py`, `storyboard.py`, `decorate.py`, `text_overlay.py`, `infocards.py`,
    `assembly.py` and the obsolete seed/test scripts.
  - **Checker**: new `app/feedback.py` (post/discard + performance → `derive_lessons`),
    wired into the loop, the CLI (`feedback` subcommands), and the UI (per-cut block).
  - **Goals**: new `app/goals.py` (north-star targets, progress, `gap_hint`); steers
    ideation, adds `--until-goals` stop, CLI (`goal`), API, and a north-star UI header.
- **Pluggable model**: `factory.py` is now a registry + dotted-path loader; added a
  generic `openai_video` adapter + optional provider adapters (runway/luma/kling/veo/
  pika/ltx/wan/svd) with lazy SDK imports; `base.ClipGenerator` gained a `live()` probe.
- **Use-case agnostic**: dropped the rental `_FACT_KEYS` schema for free-form facts +
  `use_case`; hashtags/CTA/handle/grounding now derive from the dossier.
- **Long-horizon durability**: atomic dossier writes, activity/research compaction into
  a bounded chronicle, disk retention (`prune_versions`, never deletes posted cuts), and
  a weekly `reflect()`; plus a 24/7 `deploy/run_local.sh` + launchd/systemd supervisors.
- **Repo hygiene**: re-authored 5 commits + stripped `Co-authored-by: Cursor` trailers;
  downscaled screenshots to JPG.

### 2026-06-14
- Brought a stopped Nebius H200 VM back online; fresh disk (no image/weights), so
  re-pulled `vllm/vllm-omni:cosmos3` and re-downloaded Cosmos3-Nano weights (~33 GB).
- Hit the gated-guardrail 401 at startup; fixed by launching with the bundled
  `cosmos3_no_guardrails.yaml` deploy config (documented above). Server now serves
  `/v1/videos/sync`.
- Opened the SSH tunnel and validated the full montage path on live Cosmos with a
  3-photo smoke run (GPT-4o vision labels + fast transitions + music → 5.5s).
- Ran the full 14-photo `la-house-1` montage on live Cosmos → `uploads/montage.mp4`
  (24.0s, 14 clips).
- Started keeping this AGENTS.md worklog + committing to GitHub as we go.
- New project **`hacker-house`** = Alamo Square House (Accelr8), SF co-living for
  founders/builders. Source files in `../Airbnbs/Hacker house/` (photos split across
  co-working/downstairs/gym/private rooms/Upper Floor; venue details in `website/`).
  Mined the website HTML → `website/alamo-square-facts.md` (positioning, $2.5–3k/mo,
  location, spaces, gym/coworking/makerspace, community/events, house values).
  Curated 16 photos into a narrative order (top floor → downstairs/garden → private
  rooms → gym → coworking) at the project root and kicked off a live-Cosmos montage
  → `uploads/montage_hacker_house.mp4`.

## Projects (source folders live in ../Airbnbs/, outside the repo)
- `la-house-1` — "LA House 1" Airbnb listing (original demo).
- `hacker-house` — Alamo Square House co-living (Accelr8).

### 2026-06-14 (project list cleanup)
- Sidebar was showing 4 entries (House Rental, LA House 1, backyard, Cafe SOON). Root
  causes: (1) `Airbnbs/backyard/` was a misplaced Hacker-house *area* (3 backyard
  photos) sitting at the listings root → moved into `Airbnbs/Hacker house/backyard/`;
  (2) leftover rebrand hacks in app.js — an `i===0 → "House Rental"` relabel (which
  mislabeled Hacker house since it sorts first) and a hardcoded "Cafe (SOON)"
  placeholder. Replaced the relabel with a stable `DISPLAY_NAMES` map
  (`la-house-1 → "House Rental"`) and removed the placeholder. Now exactly two real
  projects: Hacker house + House Rental (la-house-1). Did NOT rename the la-house-1
  folder — its generated versions in outputs/ are keyed to that id.

### 2026-06-14 (montages land in the Agent Loop + duration target)
- cosmos_montage.py now (a) auto-sizes frames/clip from --target-seconds (default 26,
  clamped to the 20-30s sweet spot) and (b) --install-listing registers the finished
  cut as a proper listing version (copy to outputs/, poster, brand caption/handle/
  hashtags, meta, activity log) so it shows up in the Agent Loop feed as a ready-to-
  post card. Defaults install to --listing when given.
- Installed the good la-house montage (14 clips, 24s) as a House Rental version.
- Tunnel went zombie mid-run (forwarding dead while the ssh proc stayed alive) which
  truncated the first hacker-house montage to 5 clips/8.8s. Restarted the tunnel with
  tighter keepalives (ServerAliveInterval=15, CountMax=3) and re-ran hacker-house.
