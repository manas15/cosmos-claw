"""Terminal control for the marketing-manager agent + Videographer.

Lets you (and Cursor) drive everything by hand before the autonomous GPT loop is
switched on: author the brand dossier, record consistent assumptions, write the
creative brief, run the GPT marketing steps, and fire real generations in any
social format against the live API.

Usage:
  python -m app.agent list
  python -m app.agent dossier show <listing>
  python -m app.agent dossier path <listing>
  python -m app.agent dossier set <listing> brand.voice shimmer
  python -m app.agent assume <listing> price "$245 / night"
  python -m app.agent brief set <listing> --format reel --assets 0,3,5,2 \
      --voice nova --music uplifting --pitch "Your sunny Hollywood basecamp"
  python -m app.agent generate <listing> --format story
  python -m app.agent research <listing>          # GPT step (Phase 4)
  python -m app.agent brand <listing>             # GPT step (Phase 4)
  python -m app.agent brief auto <listing> --format reel   # GPT step (Phase 4)
  python -m app.agent run <listing> --format reel          # full agent pass
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

from . import brand, config, listings


def _resolve(listing_id: str) -> listings.Listing:
    listing = listings.get_listing(listing_id)
    if listing is None:
        ids = ", ".join(listings.get_listings().keys()) or "(none found)"
        sys.exit(f"Unknown listing '{listing_id}'. Available: {ids}")
    return listing


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _coerce(value: str):
    """Parse a CLI value as JSON when possible (lists/bools/numbers), else str."""
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


# --- commands ----------------------------------------------------------

def cmd_list(_args) -> None:
    items = listings.get_listings()
    if not items:
        print(f"No listings found under {config.LISTINGS_DIR}")
        return
    for lid, l in items.items():
        has = "✓ dossier" if brand.load(lid) else "· no dossier"
        print(f"{lid:28} {len(l.photos):>2} photos   {has}   ({l.name})")


def cmd_dossier(args) -> None:
    listing = _resolve(args.listing)
    if args.action == "path":
        print(brand.brand_path(listing.id))
        return
    if args.action == "show":
        _print_json(brand.load_or_seed(listing))
        return
    if args.action == "seed":
        brand.seed_from_listing(listing)
        print(f"Seeded dossier for {listing.id}")
        return
    if args.action == "set":
        if not args.dotpath or args.value is None:
            sys.exit("Usage: dossier set <listing> <dotpath> <value>")
        dossier = brand.load_or_seed(listing)
        brand.set_path(dossier, args.dotpath, _coerce(args.value))
        brand.save(listing.id, dossier)
        brand.log_activity(listing.id, "✏️", f"Edited {args.dotpath}", "edit")
        print(f"Set {args.dotpath} = {args.value}")
        return


def cmd_assume(args) -> None:
    listing = _resolve(args.listing)
    brand.load_or_seed(listing)
    brand.add_assumption(listing.id, args.field, args.value)
    print(f"Assumed (and locked) {args.field} = {args.value}")


def cmd_brief(args) -> None:
    listing = _resolve(args.listing)
    if args.action == "auto":
        # GPT step (Phase 4): delegate to the marketing agent.
        from . import marketing_agent

        dossier = marketing_agent.build_brief(listing, fmt=args.format)
        _print_json(dossier.get("brief", {}))
        return
    # action == "set": hand-write the brief.
    dossier = brand.load_or_seed(listing)
    b = dossier.setdefault("brief", {})
    if args.format:
        b["format"] = args.format
    if args.assets is not None:
        idxs = [int(x) for x in args.assets.split(",") if x.strip().isdigit()]
        b["assets"] = [{"index": i, "reason": ""} for i in idxs]
    if args.voice:
        b["voice"] = args.voice
    if args.music:
        b["music"] = args.music
    if args.pitch:
        b["pitch"] = args.pitch
    if args.voiceover:
        b["voiceover"] = args.voiceover
    brand.save(listing.id, dossier)
    brand.log_activity(listing.id, "📝", "Updated the creative brief", "brief")
    _print_json(b)


def cmd_research(args) -> None:
    listing = _resolve(args.listing)
    from . import marketing_agent

    dossier = marketing_agent.research(listing)
    _print_json(dossier.get("research", []))


def cmd_brand(args) -> None:
    listing = _resolve(args.listing)
    from . import marketing_agent

    dossier = marketing_agent.build_brand(listing)
    _print_json({"brand": dossier.get("brand"), "assumptions": dossier.get("assumptions")})


def cmd_run(args) -> None:
    listing = _resolve(args.listing)
    from . import marketing_agent

    def emit(icon: str, text: str) -> None:
        print(f"  {icon} {text}")

    marketing_agent.run(listing, fmt=args.format, on_step=emit)
    print("Agent pass complete. Dossier updated.")
    if args.generate:
        cmd_generate(args)


def cmd_generate(args) -> None:
    listing = _resolve(args.listing)
    base = args.server.rstrip("/")
    # Reuse the live, dossier-grounded generation path.
    body = urllib.parse.urlencode(
        {"instructions": args.note or "", "price": "", "include": args.include or "", "fmt": args.format}
    ).encode()
    req = urllib.request.Request(
        f"{base}/api/listings/{listing.id}/generate", data=body, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Could not reach the server at {base} (is `python -m app` running?). {exc}")

    job_id = data.get("job_id")
    if not job_id:
        sys.exit(f"Unexpected response: {data}")
    print(f"Started job {job_id} -> {args.format}. Polling…")

    last = ""
    while True:
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"{base}/api/job/{job_id}", timeout=30) as resp:
                job = json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"Lost the job: {exc}")
        label = f"{job.get('current', 0)}/{job.get('total', 0)} {job.get('label', '')}"
        if label != last:
            print(f"  {label}")
            last = label
        if job.get("status") == "done":
            print(f"Done. Video: {base}{job.get('video_url')}")
            return
        if job.get("status") == "error":
            sys.exit(f"Generation failed: {job.get('error')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.agent", description="Cosmos Claw agent CLI")
    p.add_argument("--server", default="http://127.0.0.1:8000", help="Running app base URL")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List listings + dossier status").set_defaults(func=cmd_list)

    d = sub.add_parser("dossier", help="Inspect/author the brand dossier")
    d.add_argument("action", choices=["show", "path", "seed", "set"])
    d.add_argument("listing")
    d.add_argument("dotpath", nargs="?")
    d.add_argument("value", nargs="?")
    d.set_defaults(func=cmd_dossier)

    a = sub.add_parser("assume", help="Lock in a consistent fabricated fact")
    a.add_argument("listing")
    a.add_argument("field")
    a.add_argument("value")
    a.set_defaults(func=cmd_assume)

    b = sub.add_parser("brief", help="Write (set) or generate (auto) the creative brief")
    b.add_argument("action", choices=["set", "auto"])
    b.add_argument("listing")
    b.add_argument("--format", default="")
    b.add_argument("--assets", default=None, help="Comma-separated photo indices, e.g. 0,3,5,2")
    b.add_argument("--voice", default="")
    b.add_argument("--music", default="")
    b.add_argument("--pitch", default="")
    b.add_argument("--voiceover", default="")
    b.set_defaults(func=cmd_brief)

    r = sub.add_parser("research", help="GPT/Tavily research pass (Phase 4)")
    r.add_argument("listing")
    r.set_defaults(func=cmd_research)

    br = sub.add_parser("brand", help="GPT brand/positioning + assumptions pass (Phase 4)")
    br.add_argument("listing")
    br.set_defaults(func=cmd_brand)

    rn = sub.add_parser("run", help="Full marketing-agent pass (research -> brand -> brief)")
    rn.add_argument("listing")
    rn.add_argument("--format", default=config.DEFAULT_FORMAT)
    rn.add_argument("--generate", action="store_true", help="Also fire a generation after")
    rn.add_argument("--note", default="")
    rn.add_argument("--include", default="")
    rn.set_defaults(func=cmd_run)

    g = sub.add_parser("generate", help="Fire a dossier-grounded generation via the live API")
    g.add_argument("listing")
    g.add_argument("--format", default=config.DEFAULT_FORMAT)
    g.add_argument("--note", default="", help="Extra director note for this cut")
    g.add_argument("--include", default="", help="Comma-separated include keys (blank = all)")
    g.set_defaults(func=cmd_generate)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
