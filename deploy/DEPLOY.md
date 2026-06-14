# Self-hosting Cosmos3-Nano cheaply (real generation)

Cosmos Claw already speaks the right API (`/v1/videos/sync`, `vllm_omni` style).
You just need a GPU box serving the model, then paste its URL into `.env`.

## Reality check (cost)
- Cosmos3-Nano = **15.75B params, BF16 only, ~31.5 GB of weights**. It is **not
  gated**, so no Hugging Face token is required to download it.
- 31.5 GB of weights means **free tiers (Colab/Kaggle = 16 GB) cannot run it.**
  The realistic floor is a few dollars on a rented GPU, spun up only to demo.
- Minimum **48 GB VRAM** (weights are 31.5 GB). 80 GB is comfortable/fast.

| Where | GPU | ~Cost/hr | Notes |
|---|---|---|---|
| **Modal (free)** | L40S 48 GB | **$0 (within $30/mo free credit)** | scale-to-zero; ~15 free GPU-hrs/mo, no card |
| **Vast.ai (cheapest paid)** | 48 GB A40/A6000/L40S | $0.30–0.50 | add `--enable-layerwise-offload`; slower |
| Vast.ai / RunPod community | A100 80 GB | $0.8–1.1 | full speed, no offload needed |

> Paid options: spin up → demo for 1–2 hrs → **destroy**. Total ~$1–2.

---

## Option 0 — Modal (FREE: $30/mo credits, no credit card) ★ recommended

Modal gives every account **$30/month** of free compute, auto-applied, no card.
That's ~15 GPU-hours on a 48 GB L40S — plenty for a hackathon — and it scales to
zero between requests so you only spend while generating.

```bash
pip install modal
modal token new                      # opens browser to authenticate
modal deploy deploy/modal_app.py     # prints a public URL
```

Modal prints a URL like `https://<workspace>--cosmos-nano-serve.modal.run`.
Put `<that-url>/v1` into `.env` as `COSMOS_BASE_URL` (see below). The first
request downloads ~32 GB of weights (cached on a Volume afterward), so expect a
slow first run, then fast.

> Tip: keep `COSMOS_SIZE` small (832x480) and frames/steps low so each request
> finishes well within Modal's web-request window.

---

## Option A — Vast.ai (cheapest)

1. Sign up at https://vast.ai and add a few dollars of credit.
2. **Search** for an offer: filter GPU RAM ≥ 48 GB (or ≥ 80 GB for full speed),
   sort by price. Pick a reliable host (high reliability score).
3. Launch with:
   - **Docker image:** `vllm/vllm-omni:cosmos3`
   - **Disk:** 60 GB+ (weights are ~32 GB)
   - **On-start / Docker command:**
     ```
     vllm serve nvidia/Cosmos3-Nano --omni --model-class-name Cosmos3OmniDiffusersPipeline --allowed-local-media-path / --host 0.0.0.0 --port 8000 --init-timeout 1800 --enable-layerwise-offload
     ```
     (drop `--enable-layerwise-offload` if you picked an 80 GB GPU)
   - **Expose port:** 8000
4. When the instance is running, open it and use the **"Open Ports"** mapping to
   get the public host\:port (e.g. `http://<ip>:<mapped_port>`).

## Option B — RunPod community cloud (pay-as-you-go)

Same idea: deploy a pod (A100 80 GB or L40S 48 GB), image
`vllm/vllm-omni:cosmos3`, expose HTTP port `8000`, 60 GB+ disk, and the same
start command above. RunPod gives a proxy URL:
`https://<POD_ID>-8000.proxy.runpod.net`.

## Option C — your own GPU box
`scp` this folder up and run:
```bash
bash deploy/start_cosmos_server.sh           # 80 GB GPU
EXTRA_FLAGS=--enable-layerwise-offload bash deploy/start_cosmos_server.sh   # 48 GB
```

---

## Wait for ready
In the instance logs, wait for `Application startup complete` /
`Uvicorn running on http://0.0.0.0:8000`. The **first boot downloads ~32 GB** of
weights — give it several minutes; the first generation also warms up slowly.

## Point Cosmos Claw at it
Edit `LiveHere/.env`:
```
LIVEHERE_BACKEND=cosmos
COSMOS_API_STYLE=vllm_omni
COSMOS_BASE_URL=<paste public URL>/v1     # e.g. https://<POD>-8000.proxy.runpod.net/v1
COSMOS_API_KEY=local                       # any non-empty value; server has no auth
COSMOS_VIDEOS_PATH=/videos/sync
COSMOS_MODEL=nvidia/Cosmos3-Nano

# Cheap/fast demo preset (smaller + fewer frames/steps = faster + cheaper):
COSMOS_SIZE=832x480
COSMOS_NUM_FRAMES=61
COSMOS_FPS=24
COSMOS_STEPS=20
COSMOS_GUIDANCE=6.0
```
Bump `COSMOS_SIZE=1280x720`, `COSMOS_NUM_FRAMES=121+`, `COSMOS_STEPS=35` for
final quality once you've confirmed it works.

## Verify + run
```bash
.venv/bin/python -m app.probe     # expect HTTP 200 -> LIVE!
.venv/bin/python -m app           # generate from the UI at http://127.0.0.1:8000
```

## Cost control
Billing runs while the instance is **Running**. **Destroy/stop it** the moment
you're done. On Vast, "Stop" still bills storage — **Destroy** to stop all charges.
