"""Optional image->video provider adapters.

Each module here is a thin ``ClipGenerator`` subclass for a specific hosted or
open model. They are *lazy-imported by the factory* and each provider's SDK is
an OPTIONAL dependency (imported inside ``generate_clip``), so a missing SDK for
one provider never breaks the app or the other backends. Configure the one you
want via env vars and ``LIVEHERE_BACKEND=<name>`` (see app/generation/factory.py).
"""
