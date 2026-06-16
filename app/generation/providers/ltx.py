"""LTX-Video (Lightricks) open image->video adapter — local diffusers pipeline.

Optional deps: ``pip install "diffusers>=0.32" transformers accelerate torch``.
Runs on your own GPU; no API key. Set ``LTX_MODEL`` to pick a checkpoint.
The pipeline is built once and cached on the instance.
"""

from __future__ import annotations

from pathlib import Path

from ... import config
from ..base import ClipGenerator, Scene
from ._common import full_prompt, scene_image


class LTXClipGenerator(ClipGenerator):
    name = "ltx (LTX-Video, local)"

    def __init__(self) -> None:
        self._pipe = None

    def available(self) -> tuple[bool, str]:
        try:
            import diffusers  # noqa: F401
            import torch  # noqa: F401
        except ImportError:
            return False, "needs `pip install diffusers transformers accelerate torch`"
        return True, f"configured ({config.LTX_MODEL}, local GPU)"

    def _pipeline(self):
        if self._pipe is None:
            import torch
            from diffusers import LTXImageToVideoPipeline

            self._pipe = LTXImageToVideoPipeline.from_pretrained(
                config.LTX_MODEL, torch_dtype=torch.bfloat16
            ).to("cuda" if torch.cuda.is_available() else "cpu")
        return self._pipe

    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        from diffusers.utils import export_to_video, load_image

        img = scene_image(scene, Path(out_path).parent)
        w, h = (int(x) for x in config.VIDEO_SIZE.split("x"))
        result = self._pipeline()(
            image=load_image(img),
            prompt=full_prompt(scene),
            negative_prompt=config.COSMOS_NEGATIVE_PROMPT,
            width=w, height=h,
            num_frames=config.COSMOS_NUM_FRAMES,
            num_inference_steps=config.COSMOS_STEPS,
        )
        export_to_video(result.frames[0], out_path, fps=config.COSMOS_FPS)
        return out_path
