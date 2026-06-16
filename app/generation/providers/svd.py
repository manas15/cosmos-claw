"""Stable Video Diffusion (Stability AI) open image->video adapter.

Optional deps: ``pip install "diffusers>=0.25" transformers accelerate torch``.
Runs on your own GPU; no API key. SVD is image-conditioned only (no text
prompt), so the scene prompt is ignored — motion comes from the photo.
"""

from __future__ import annotations

from pathlib import Path

from ... import config
from ..base import ClipGenerator, Scene
from ._common import scene_image


class SVDClipGenerator(ClipGenerator):
    name = "svd (Stable Video Diffusion, local)"

    def __init__(self) -> None:
        self._pipe = None

    def available(self) -> tuple[bool, str]:
        try:
            import diffusers  # noqa: F401
            import torch  # noqa: F401
        except ImportError:
            return False, "needs `pip install diffusers transformers accelerate torch`"
        return True, f"configured ({config.SVD_MODEL}, local GPU)"

    def _pipeline(self):
        if self._pipe is None:
            import torch
            from diffusers import StableVideoDiffusionPipeline

            self._pipe = StableVideoDiffusionPipeline.from_pretrained(
                config.SVD_MODEL, torch_dtype=torch.float16, variant="fp16"
            ).to("cuda" if torch.cuda.is_available() else "cpu")
        return self._pipe

    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        from diffusers.utils import export_to_video, load_image

        img = load_image(scene_image(scene, Path(out_path).parent))
        frames = min(config.COSMOS_NUM_FRAMES, 25)  # SVD-XT tops out at 25 frames
        result = self._pipeline()(
            img, num_frames=frames, decode_chunk_size=8,
            motion_bucket_id=int(127 * float(scene.motion_strength or 0.5) * 2),
        )
        export_to_video(result.frames[0], out_path, fps=config.COSMOS_FPS)
        return out_path
