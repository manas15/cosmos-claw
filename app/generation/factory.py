"""Select the active clip generator from configuration."""

from __future__ import annotations

from .. import config
from .base import ClipGenerator
from .cosmos import CosmosClipGenerator
from .stub import StubClipGenerator


def get_generator(backend: str | None = None) -> ClipGenerator:
    backend = (backend or config.GENERATION_BACKEND).lower()
    # "nebius" kept as an alias: any hosted/self-hosted Cosmos endpoint.
    if backend in ("cosmos", "nebius"):
        return CosmosClipGenerator()
    if backend == "stub":
        return StubClipGenerator()
    raise ValueError(f"Unknown generation backend: {backend!r}")
