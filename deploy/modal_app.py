"""Serve NVIDIA Cosmos3-Nano on Modal's free $30/month tier.

This runs the official `vllm/vllm-omni:cosmos3` image as a Modal web server,
exposing the same OpenAI-compatible /v1/videos/sync endpoint Cosmos Claw already
talks to. No credit card required for the Starter plan's $30/mo credits.

Setup (one time):
    pip install modal
    modal token new          # opens browser to auth

Deploy:
    modal deploy deploy/modal_app.py

Modal prints a public URL like:
    https://<workspace>--cosmos-nano-serve.modal.run
Put `<that-url>/v1` into LiveHere/.env as COSMOS_BASE_URL, set
LIVEHERE_BACKEND=cosmos and COSMOS_API_STYLE=vllm_omni, then restart the app.

Cost: L40S (48GB) is ~$1.95/hr on Modal, so $30/mo ≈ ~15 GPU-hours. The server
scales to zero after `SCALEDOWN` seconds of no traffic, so you only burn credits
while actually generating (plus warm-up). Weights are cached on a Volume so you
download the ~32GB once.
"""

import subprocess

import modal

MODEL = "nvidia/Cosmos3-Nano"
PORT = 8000
MINUTES = 60
# L40S (48GB) is the cheapest GPU that fits; needs offload. Swap to "A100-80GB"
# for full speed (a bit more $/hr) and drop --enable-layerwise-offload below.
GPU = "L40S"
USE_OFFLOAD = GPU not in ("A100-80GB", "H100", "H200", "B200")

# Official vLLM-Omni image with the Cosmos3 pipeline preinstalled.
image = modal.Image.from_registry("vllm/vllm-omni:cosmos3").entrypoint([])

# Cache the ~32GB of weights so we download them only once.
hf_cache = modal.Volume.from_name("huggingface-cache", create_if_missing=True)

app = modal.App("cosmos-nano")


@app.function(
    image=image,
    gpu=GPU,
    volumes={"/root/.cache/huggingface": hf_cache},
    scaledown_window=10 * MINUTES,   # stay warm this long after the last request
    timeout=30 * MINUTES,            # allow slow first boot (weight download)
)
@modal.concurrent(max_inputs=1)      # one heavy video job at a time
@modal.web_server(port=PORT, startup_timeout=30 * MINUTES)
def serve():
    cmd = [
        "vllm", "serve", MODEL,
        "--omni",
        "--model-class-name", "Cosmos3OmniDiffusersPipeline",
        "--allowed-local-media-path", "/",
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--init-timeout", "1800",
    ]
    if USE_OFFLOAD:
        cmd.append("--enable-layerwise-offload")
    print("Launching:", " ".join(cmd), flush=True)
    subprocess.Popen(cmd)
