#!/usr/bin/env bash
# Start a self-hosted NVIDIA Cosmos3-Nano video-generation server (vLLM-Omni),
# exposing the OpenAI-compatible /v1/videos/sync endpoint on port 8000.
#
# Run this INSIDE a GPU box (RunPod/Modal/bare metal) that has an NVIDIA GPU
# with ~80GB VRAM (H100/H200). For smaller GPUs add --enable-layerwise-offload.
#
# Prereqs on the host:
#   - NVIDIA driver + nvidia-container-toolkit (RunPod/Vast images have this)
#   - ~48GB+ VRAM (weights are ~31.5GB). 80GB = comfortable/fast.
#
# The model is NOT gated, so HF_TOKEN is optional (set it only to raise HF
# download rate limits).
#
# Usage:
#   bash start_cosmos_server.sh
#   EXTRA_FLAGS=--enable-layerwise-offload bash start_cosmos_server.sh   # 48GB GPU
set -euo pipefail

HF_TOKEN="${HF_TOKEN:-}"

# Extra flags: e.g. EXTRA_FLAGS="--enable-layerwise-offload" for <80GB GPUs.
EXTRA_FLAGS="${EXTRA_FLAGS:-}"

# Option A: prebuilt official image (recommended — every modality supported).
if command -v docker >/dev/null 2>&1; then
  echo "Starting Cosmos3-Nano via vllm/vllm-omni:cosmos3 ..."
  exec docker run --rm --runtime nvidia --gpus all \
    -e HF_TOKEN="$HF_TOKEN" \
    -v ~/.cache/huggingface:/root/.cache/huggingface \
    -p 8000:8000 --ipc=host \
    vllm/vllm-omni:cosmos3 \
    vllm serve nvidia/Cosmos3-Nano \
      --omni \
      --model-class-name Cosmos3OmniDiffusersPipeline \
      --allowed-local-media-path / \
      --host 0.0.0.0 --port 8000 \
      --init-timeout 1800 \
      $EXTRA_FLAGS
fi

# Option B: no docker (already inside the cosmos3 container / venv).
echo "docker not found — running vllm serve directly ..."
export HF_TOKEN
exec vllm serve nvidia/Cosmos3-Nano \
  --omni \
  --model-class-name Cosmos3OmniDiffusersPipeline \
  --allowed-local-media-path / \
  --host 0.0.0.0 --port 8000 \
  --init-timeout 1800 \
  $EXTRA_FLAGS
