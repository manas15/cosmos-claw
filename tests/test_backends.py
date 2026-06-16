"""Contract tests for the pluggable generation backends.

Most providers need a paid SDK + key we can't run in CI, so we test the seams
that matter: every registered backend resolves to a real ``ClipGenerator``
subclass, a dotted-path "bring-your-own-model" string imports, unconfigured
adapters degrade with a clear reason (never crash on import), and the generic
OpenAI-compatible HTTP adapter writes the bytes it's handed (HTTP mocked).

Run:  .venv/bin/python -m unittest tests.test_backends -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.generation import factory
from app.generation.base import ClipGenerator, Scene


class TestRegistry(unittest.TestCase):
    def test_every_backend_resolves_to_clipgenerator(self):
        for name, spec in factory.BACKENDS.items():
            cls = factory._load(spec)
            self.assertTrue(issubclass(cls, ClipGenerator), f"{name} -> {spec}")

    def test_dotted_path_byo_model(self):
        gen = factory.get_generator("app.generation.stub:StubClipGenerator")
        self.assertIsInstance(gen, ClipGenerator)

    def test_unknown_backend_raises(self):
        with self.assertRaises(ValueError):
            factory.get_generator("definitely-not-a-backend")

    def test_unconfigured_providers_report_not_available(self):
        # With no keys/SDKs set, each adapter should say so rather than blow up.
        for name in ("openai_video", "runway", "luma", "kling", "veo", "pika"):
            gen = factory.get_generator(name)
            ok, why = gen.available()
            self.assertIsInstance(ok, bool)
            self.assertTrue(why)


class TestOpenAICompatibleAdapter(unittest.TestCase):
    def _scene(self, tmp: Path) -> Scene:
        img = tmp / "in.png"
        # 1x1 PNG
        img.write_bytes(bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
            "53de0000000c4944415408d763f8cfc0f01f0005000100ff8f2b990000000049454e44ae426082"
        ))
        return Scene(index=0, source_path=str(img), prompt="walk in", caption="",
                     time_label="", time_of_day="day", duration=2.0)

    def test_generate_clip_writes_mp4_bytes(self):
        import tempfile

        from app import config
        from app.generation.openai_video import OpenAICompatibleVideoClipGenerator

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            scene = self._scene(tmp)
            out = str(tmp / "out.mp4")
            fake = mock.Mock()
            fake.status_code = 200
            fake.headers = {"content-type": "video/mp4"}
            fake.content = b"\x00\x00\x00\x18ftypmp42FAKEMP4BYTES"
            with mock.patch.object(config, "VIDEO_BASE_URL", "http://x/v1"), \
                 mock.patch("app.generation.openai_video.requests.post", return_value=fake):
                gen = OpenAICompatibleVideoClipGenerator()
                self.assertTrue(gen.available()[0])
                gen.generate_clip(scene, out)
            self.assertTrue(Path(out).exists() and Path(out).stat().st_size > 0)


if __name__ == "__main__":
    unittest.main()
