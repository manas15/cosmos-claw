"""Select the active clip generator — pluggable, bring-your-own-model.

The backend is chosen by ``LIVEHERE_BACKEND`` (or the ``--backend`` flag). It can
be either:

  * a short name in ``BACKENDS`` below ("stub", "cosmos", "runway", "luma", …), or
  * a dotted import path to ANY ``ClipGenerator`` subclass, e.g.
        LIVEHERE_BACKEND=mypkg.my_model:AwesomeClipGenerator
    so a forker can drop in a better model without editing this repo.

Every backend only has to implement ``generate_clip`` (+ optional ``available`` /
``live`` health checks). The whole pipeline (vision -> film -> cut -> publish)
is identical regardless of which model is plugged in.
"""

from __future__ import annotations

import importlib

from .. import config
from .base import ClipGenerator

# Short name -> "module.path:ClassName". Adapters are lazy-imported on use so a
# missing optional SDK for one provider never breaks the others.
BACKENDS: dict[str, str] = {
    "stub": "app.generation.stub:StubClipGenerator",
    # Cosmos / any OpenAI-compatible vLLM-Omni or NVIDIA-hosted endpoint.
    "cosmos": "app.generation.cosmos:CosmosClipGenerator",
    "nebius": "app.generation.cosmos:CosmosClipGenerator",
    # Generic OpenAI-compatible image->video server (VIDEO_* config).
    "openai_video": "app.generation.openai_video:OpenAICompatibleVideoClipGenerator",
    # Hosted providers (need each provider's optional SDK + API key).
    "runway": "app.generation.providers.runway:RunwayClipGenerator",
    "luma": "app.generation.providers.luma:LumaClipGenerator",
    "kling": "app.generation.providers.kling:KlingClipGenerator",
    "veo": "app.generation.providers.veo:VeoClipGenerator",
    "pika": "app.generation.providers.pika:PikaClipGenerator",
    # Open / self-hostable models.
    "ltx": "app.generation.providers.ltx:LTXClipGenerator",
    "wan": "app.generation.providers.wan:WanClipGenerator",
    "svd": "app.generation.providers.svd:SVDClipGenerator",
}


def _load(spec: str) -> type[ClipGenerator]:
    """Import ``module:Class`` (or ``module.Class``) and return the class."""
    if ":" in spec:
        mod_name, _, cls_name = spec.partition(":")
    else:
        mod_name, _, cls_name = spec.rpartition(".")
    if not mod_name or not cls_name:
        raise ValueError(f"Bad backend path {spec!r}; expected 'module:Class'")
    module = importlib.import_module(mod_name)
    obj = getattr(module, cls_name, None)
    if not (isinstance(obj, type) and issubclass(obj, ClipGenerator)):
        raise TypeError(f"{spec!r} is not a ClipGenerator subclass")
    return obj


def get_generator(backend: str | None = None) -> ClipGenerator:
    backend = (backend or config.GENERATION_BACKEND or "stub").strip()
    spec = BACKENDS.get(backend.lower())
    if spec is None:
        # Treat the value itself as a dotted import path (bring-your-own-model).
        if ":" in backend or "." in backend:
            spec = backend
        else:
            known = ", ".join(sorted(BACKENDS))
            raise ValueError(
                f"Unknown generation backend {backend!r}. "
                f"Use one of: {known} — or a dotted path 'module:ClipGeneratorClass'."
            )
    return _load(spec)()
