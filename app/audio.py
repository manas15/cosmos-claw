"""Audio for the listing trailer: a soft music bed + AI voiceover, mixed in.

- ``synth_music_bed`` builds a gentle, licensing-safe ambient pad with ffmpeg
  (a soft minor chord + slow tremolo + lowpass). Override with a real track via
  the ``MUSIC_PATH`` env var.
- ``tts_voiceover`` uses OpenAI text-to-speech to narrate the director's script.
- ``mux_audio`` layers the voiceover over the (ducked) music bed and attaches it
  to the silent trailer video, trimmed to the video's length.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .ffmpeg_utils import probe_duration, run_ffmpeg

# A calm A-minor pad (A3, C4, E4, A4). Soft sines, detuned slightly, lowpassed.
_CHORD_HZ = [220.0, 261.63, 329.63, 440.0]


def synth_music_bed(out_path: str, duration: float) -> str:
    """Render a soft ambient pad of ``duration`` seconds to ``out_path`` (wav)."""
    dur = max(1.0, duration)
    fade_out = max(0.0, dur - 2.0)

    args: list[str] = []
    for hz in _CHORD_HZ:
        args += ["-f", "lavfi", "-t", f"{dur:.2f}", "-i", f"sine=frequency={hz}:sample_rate=44100"]

    n = len(_CHORD_HZ)
    mix_inputs = "".join(f"[{i}:a]" for i in range(n))
    filtergraph = (
        f"{mix_inputs}amix=inputs={n}:normalize=0,"
        f"volume=0.22,tremolo=f=0.12:d=0.5,lowpass=f=1100,"
        f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_out:.2f}:d=2.0"
    )

    run_ffmpeg([*args, "-filter_complex", filtergraph, "-ac", "2", "-ar", "44100", out_path])
    return out_path


def tts_voiceover(script: str, out_path: str) -> str | None:
    """Synthesize the narration with OpenAI TTS. Returns path or None on failure."""
    script = (script or "").strip()
    if not script or not config.OPENAI_API_KEY:
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        try:
            with client.audio.speech.with_streaming_response.create(
                model=config.TTS_MODEL,
                voice=config.TTS_VOICE,
                input=script,
                instructions=(
                    "Warm, inviting, calm real-estate narrator. Natural pacing, "
                    "gentle enthusiasm, like a premium travel ad."
                ),
            ) as response:
                response.stream_to_file(out_path)
        except Exception:
            # Older/cheaper model fallback (no `instructions` support).
            with client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice=config.TTS_VOICE,
                input=script,
            ) as response:
                response.stream_to_file(out_path)
        if Path(out_path).exists() and Path(out_path).stat().st_size > 0:
            return out_path
    except Exception as exc:  # noqa: BLE001
        print(f"[audio] TTS voiceover failed ({exc}); continuing without narration")
    return None


def mux_audio(
    video_in: str,
    video_out: str,
    *,
    voiceover: str | None,
    music: str | None,
) -> str:
    """Attach music (+ optional voiceover) to ``video_in`` -> ``video_out``.

    The music bed is matched to the video length; the voiceover plays from the
    start at full level over the ducked music. If no audio is available the
    video is copied through unchanged.
    """
    if not music and not voiceover:
        run_ffmpeg(["-i", video_in, "-c", "copy", video_out])
        return video_out

    inputs = ["-i", video_in]
    music_idx = vo_idx = None
    if music:
        inputs += ["-i", music]
        music_idx = (len(inputs) // 2) - 1
    if voiceover:
        inputs += ["-i", voiceover]
        vo_idx = (len(inputs) // 2) - 1

    parts: list[str] = []
    mix_labels: list[str] = []
    if music_idx is not None:
        parts.append(f"[{music_idx}:a]volume={config.MUSIC_VOLUME}[m]")
        mix_labels.append("[m]")
    if vo_idx is not None:
        parts.append(f"[{vo_idx}:a]volume=1.0[v]")
        mix_labels.append("[v]")

    if len(mix_labels) == 2:
        parts.append(f"{''.join(mix_labels)}amix=inputs=2:duration=longest:dropout_transition=0[a]")
    else:
        # Only one source; rename it to [a].
        single = mix_labels[0]
        parts[-1] = parts[-1].replace(single, "[a]")
    filtergraph = ";".join(parts)

    run_ffmpeg(
        [
            *inputs,
            "-filter_complex", filtergraph,
            "-map", "0:v",
            "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            video_out,
        ]
    )
    return video_out
