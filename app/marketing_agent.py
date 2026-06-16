"""The marketing-manager agent (OpenClaw-style, GPT-4o brain).

A small autonomous "marketing manager" for each project. It researches the venue
(real web search via Tavily when a key is present, otherwise GPT-4o fabricates
plausible context), invents the missing brand facts and LOCKS them in as durable
assumptions (so they stay consistent across every future video), and writes a
creative brief that tells the Videographer which uploaded assets to use, in what
order, with which music mood and TTS voice, and in what format.

Modeled on OpenClaw's split of Brain (GPT reasoning) / Hands (tools: web search,
dossier writes) / Memory (the persisted brand dossier) / Heartbeat (the on-demand
``run`` orchestrator we can later schedule). Each capability is an independent,
persisted function so it can be driven by hand from the terminal today and chained
autonomously later. Every step degrades gracefully without an API key.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Callable

from . import brand, config, listings

# Allowed OpenAI TTS voices and music moods the agent may choose from.
_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
_MOODS = ("warm", "calm", "uplifting", "energetic", "luxury", "moody")

StepFn = Callable[[str, str], None]


def _noop(icon: str, text: str) -> None:  # default on_step
    pass


def _gpt_json(system: str, user_content, max_tokens: int = 1200, temperature: float = 0.6) -> dict:
    """Call GPT-4o for a STRICT-JSON response. Returns {} on any failure."""
    if not config.OPENAI_API_KEY:
        return {}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001
        print(f"[marketing_agent] GPT call failed: {exc}")
        return {}


# --- Hands: web search -------------------------------------------------

def _tavily_search(query: str, max_results: int = 4) -> list[dict]:
    """Real web search via Tavily. Returns [{query, source, snippet, kind}]."""
    if not config.TAVILY_API_KEY:
        return []
    try:
        body = json.dumps(
            {
                "api_key": config.TAVILY_API_KEY,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        out = []
        for r in data.get("results", [])[:max_results]:
            out.append(
                {
                    "query": query,
                    "source": r.get("url") or "tavily",
                    "snippet": (r.get("content") or "")[:300],
                    "kind": "real",
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[marketing_agent] Tavily search failed: {exc}")
        return []


def _fabricate_research(location: str, facts: dict) -> list[dict]:
    """No-key fallback: GPT-4o invents plausible neighborhood/market context."""
    system = (
        "You are a marketing researcher. Invent PLAUSIBLE, specific local/market "
        "context for THIS brand's marketing (its audience, who it's for, nearby or "
        "relevant context, and one competitive angle) given its use-case. It's fine "
        "to make things up, but keep them realistic and self-consistent. Return "
        'STRICT JSON: {"findings": [{"query": "...", "source": "assumed", '
        '"snippet": "...", "kind": "assumed"}]} with 4-6 findings.'
    )
    user = f"Location: {location or 'a desirable area'}\nKnown facts: {json.dumps(facts)[:1500]}"
    data = _gpt_json(system, user, max_tokens=900, temperature=0.8)
    out = []
    for f in data.get("findings", [])[:6]:
        if isinstance(f, dict) and f.get("snippet"):
            out.append(
                {
                    "query": str(f.get("query") or location),
                    "source": "assumed",
                    "snippet": str(f.get("snippet"))[:300],
                    "kind": "assumed",
                }
            )
    return out


def research(listing: listings.Listing, on_step: StepFn = _noop) -> dict:
    """Gather (real or fabricated) market context into the dossier."""
    dossier = brand.load_or_seed(listing)
    facts = dossier.get("facts", {})
    location = facts.get("location") or listing.name

    on_step("🔎", f"Researching the market around {location}")
    findings: list[dict] = []
    if config.TAVILY_API_KEY:
        for q in (
            f"things to do near {location}",
            f"{location} neighborhood guide for travelers",
            f"public transit and parking near {location}",
        ):
            findings += _tavily_search(q)
        source_note = "Tavily web search"
    if not findings:  # no key, or search returned nothing -> fabricate
        findings = _fabricate_research(location, facts)
        source_note = "fabricated (no live search)"

    # Append, de-duplicating on snippet text.
    existing = {r.get("snippet") for r in dossier.get("research", [])}
    added = [f for f in findings if f.get("snippet") not in existing]
    dossier.setdefault("research", []).extend(added)
    brand.save(listing.id, dossier)
    brand.log_activity(
        listing.id, "🔎", f"Researched {location} via {source_note} ({len(added)} findings)", "research"
    )
    on_step("🔎", f"Logged {len(added)} findings ({source_note})")
    return brand.load(listing.id) or dossier


# --- Brain: brand positioning + durable assumptions --------------------

def build_brand(listing: listings.Listing, on_step: StepFn = _noop) -> dict:
    """Write brand positioning and LOCK IN missing facts as durable assumptions."""
    dossier = brand.load_or_seed(listing)
    facts = dossier.get("facts", {})
    research = dossier.get("research", [])

    on_step("🧠", "Defining the brand positioning")
    use_case = dossier.get("use_case") or "this brand"
    system = (
        "You are a senior brand/marketing manager who can position ANY local "
        f"business, venue, product, or creator (here: {use_case}). From the facts "
        "and research, define a crisp brand and FILL IN any missing facts with "
        "plausible, self-consistent assumptions (it's fine to make things up for the "
        "demo, but keep them realistic). Also write a short call-to-action that fits "
        "the use-case (e.g. 'Book now', 'Visit us', 'Shop the drop', 'Follow along'). "
        f"Choose a TTS voice from {list(_VOICES)} and a music mood from {list(_MOODS)}. "
        'Return STRICT JSON: {"oneliner": "...", "audience": "...", "tone": "...", '
        '"voice": "nova", "music": "warm", "cta": "...", '
        '"selling_points": ["...", "..."], '
        '"assumptions": [{"field": "price", "value": "..."}, '
        '{"field": "highlight", "value": "..."}]}. '
        "Only include assumptions for facts that are currently missing/empty."
    )
    user = (
        f"Facts: {json.dumps(facts)[:2000]}\n\n"
        f"Research: {json.dumps(research)[:2000]}"
    )
    data = _gpt_json(system, user, max_tokens=900, temperature=0.7)

    b = dossier.setdefault("brand", {})
    if data.get("oneliner"):
        b["oneliner"] = str(data["oneliner"]).strip()
    if data.get("audience"):
        b["audience"] = str(data["audience"]).strip()
    if data.get("tone"):
        b["tone"] = str(data["tone"]).strip()
    if str(data.get("voice", "")).lower() in _VOICES:
        b["voice"] = str(data["voice"]).lower()
    if str(data.get("music", "")).lower() in _MOODS:
        b["music"] = str(data["music"]).lower()
    if str(data.get("cta", "")).strip():
        b["cta"] = str(data["cta"]).strip()[:60]
    sp = [str(s).strip() for s in (data.get("selling_points") or []) if str(s).strip()]
    if sp:
        b["selling_points"] = sp[:6]
    # Keep the brief's audio in sync with the brand's chosen voice/music.
    brief = dossier.setdefault("brief", {})
    brief.setdefault("voice", b.get("voice"))
    brief.setdefault("music", b.get("music"))
    brand.save(listing.id, dossier)

    # Lock in assumptions append-only (never overwrites -> consistency).
    locked = 0
    for a in data.get("assumptions", []) or []:
        if not isinstance(a, dict):
            continue
        field, value = str(a.get("field") or "").strip(), str(a.get("value") or "").strip()
        if field and value:
            brand.add_assumption(listing.id, field, value)
            locked += 1

    brand.log_activity(listing.id, "🧠", f"Set positioning and locked {locked} assumptions", "brand")
    on_step("🧠", f"Positioning set; {locked} assumptions locked")
    return brand.load(listing.id) or dossier


# --- Brain: the creative brief (with vision) ---------------------------

def _encode_photos(listing: listings.Listing, limit: int = 12) -> list[tuple[int, str]]:
    """Base64 data URIs for up to ``limit`` photos (uses cached JPEG thumbs)."""
    from .vision import encode_image as _encode_image

    work = config.OUTPUT_DIR / "agent_work"
    work.mkdir(parents=True, exist_ok=True)
    out: list[tuple[int, str]] = []
    for i in range(min(limit, len(listing.photos))):
        thumb = listings.thumb(listing, i)
        if thumb is None:
            continue
        uri = _encode_image(str(thumb), work, i)
        if uri:
            out.append((i, uri))
    return out


def build_brief(listing: listings.Listing, fmt: str = "", on_step: StepFn = _noop) -> dict:
    """Produce the creative brief: ordered assets, hooks, captions, VO, music, voice."""
    dossier = brand.load_or_seed(listing)
    fmt = (fmt or dossier.get("brief", {}).get("format") or config.DEFAULT_FORMAT).lower()
    facts = dossier.get("facts", {})
    b = dossier.get("brand", {})

    on_step("🎬", f"Writing the {fmt} creative brief")
    photos = _encode_photos(listing)
    n = len(photos)

    system = (
        "You are a marketing manager briefing a videographer for a social cut. You "
        "see the venue's photos (labeled 'Photo index N'). Pick the strongest 5-7 and "
        "ORDER them like a tour (hook shot first, signature shot last). Write the brief "
        f"for a '{fmt}' format. Choose a TTS voice from {list(_VOICES)} and music mood "
        f"from {list(_MOODS)} matching the brand. Ground all facts in what's provided; "
        "you may rely on the locked assumptions. Return STRICT JSON: "
        '{"assets": [{"index": 0, "reason": "..."}], "hooks": ["..."], '
        '"captions": ["..."], "voiceover": "~75 word script", "music": "warm", '
        '"voice": "nova", "pitch": "one-line angle"}.'
    )
    user_content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Brand: {json.dumps(b)[:1200]}\nFacts: {json.dumps(facts)[:1500]}\n"
                f"There are {n} photos, indices 0..{max(0, n - 1)}."
            ),
        }
    ]
    for idx, uri in photos:
        user_content.append({"type": "text", "text": f"Photo index {idx}:"})
        user_content.append({"type": "image_url", "image_url": {"url": uri, "detail": "low"}})

    data = _gpt_json(system, user_content, max_tokens=1400, temperature=0.6)

    brief = dossier.setdefault("brief", {})
    brief["format"] = fmt
    assets = []
    for a in data.get("assets", []) or []:
        if not isinstance(a, dict):
            continue
        try:
            idx = int(a.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(listing.photos):
            assets.append({"index": idx, "reason": str(a.get("reason") or "").strip()})
    if assets:
        brief["assets"] = assets
    brief["hooks"] = [str(h).strip() for h in (data.get("hooks") or []) if str(h).strip()][:5]
    brief["captions"] = [str(c).strip() for c in (data.get("captions") or []) if str(c).strip()][:8]
    if data.get("voiceover"):
        brief["voiceover"] = str(data["voiceover"]).strip()
    if str(data.get("voice", "")).lower() in _VOICES:
        brief["voice"] = str(data["voice"]).lower()
    elif not brief.get("voice"):
        brief["voice"] = b.get("voice") or config.TTS_VOICE
    if str(data.get("music", "")).lower() in _MOODS:
        brief["music"] = str(data["music"]).lower()
    elif not brief.get("music"):
        brief["music"] = b.get("music") or "warm"
    if data.get("pitch"):
        brief["pitch"] = str(data["pitch"]).strip()

    brand.save(listing.id, dossier)
    brand.log_activity(
        listing.id, "🎬", f"Drafted the {fmt} brief ({len(brief.get('assets', []))} assets)", "brief"
    )
    on_step("🎬", f"Brief ready: {len(brief.get('assets', []))} assets, {fmt}")
    return brand.load(listing.id) or dossier


# --- Heartbeat: on-demand full pass ------------------------------------

def run(listing: listings.Listing, fmt: str = "", on_step: StepFn = _noop) -> dict:
    """Chain research -> brand -> brief in one on-demand marketing pass."""
    brand.log_activity(listing.id, "🚀", "Marketing manager started a pass", "run")
    on_step("🚀", "Marketing manager on the case")
    research(listing, on_step=on_step)
    build_brand(listing, on_step=on_step)
    dossier = build_brief(listing, fmt=fmt, on_step=on_step)
    brand.log_activity(listing.id, "✨", "Marketing pass complete — brief is ready", "run")
    on_step("✨", "Pass complete — brief ready to film")
    return dossier
