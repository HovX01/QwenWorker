# RunPod image worker

Queue-based RunPod Serverless worker for
[AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF](https://huggingface.co/AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF).
Uses a persistent `stable-diffusion.cpp` server, so weights load once per worker.
No ComfyUI, PyTorch, public inference port, or separate web service.

The requested repository contains the **text encoder**, not the complete image model.
Worker downloads these three pinned artifacts at startup:

| Component | Repository | File | Download size |
| --- | --- | --- | --- |
| Denoiser | `AtomicChat/Qwen-Image-2.1-Turbo-GGUF` | `Qwen-Image-2.1-Turbo-AD-Q4_K.gguf` | 4.20 GB |
| Text encoder | `AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF` | `Qwen-Image-2.1-Turbo-Uncensored-Q8_0.gguf` | 8.71 GB |
| VAE | `Comfy-Org/Qwen-Image-2.1` | `vae/qwen_image_2.1_vae_bf16.safetensors` | 0.68 GB |

Turbo sampling is fixed to the publisher's 8 steps, CFG 1, Euler, and exact sigma schedule.
Text-to-image only. Vision projector, image editing, and LoRAs are omitted.

## Choose GPU and endpoint settings

For occasional personal use, start with **one RTX 3090 24 GB**, keeping the current
AD-Q4_K denoiser and Q8_0 encoder. The 13.6 GB weight files leave more headroom on 24 GB
than on 16 GB, but file size is not peak VRAM. This configuration still needs a real GPU test.
The engine's automatic memory fitting is enabled by default.

| Choice | RunPod pool | VRAM | Documented GPU rate | Equivalent billed hour |
| --- | --- | --- | --- | --- |
| Lower rate, less headroom | `AMPERE_16`: A4000 / A4500 / RTX 4000 | 16 GB | $0.00016/s | $0.576 |
| Recommended budget trial | `AMPERE_24`: RTX 3090 / A5000 / L4 | 24 GB | $0.00019/s | $0.684 |
| Compare for speed | `ADA_24`: RTX 4090 | 24 GB | $0.00031/s | $1.116 |

Rates are from [RunPod's Serverless documentation](https://docs.runpod.io/serverless/pricing),
checked 2026-10-15. Confirm the console's rate before deployment. An equivalent billed hour
is 3600 billed seconds, not a charge for a wall-clock hour when the worker is stopped.
Startup, generation, and idle time contribute to billing. Storage is separate.

The 16 GB tier saves only about 16% per second against `AMPERE_24`; CPU offload or slower
generation can erase that saving. RTX 4090 must complete total billed work over 1.63 times
faster to cost less than the 24 GB budget pool. No consumer-GPU timing comparison was found
in the publisher's measurements, which used an A100 80 GB.

Pool selection may assign any listed GPU, so `AMPERE_24` does not guarantee RTX 3090
performance. RTX 3090 and A5000 match `sm_86`; L4 and RTX 4090 match `sm_89`.
48 GB A40 / RTX A6000 / L40 / L40S remain options if a measured workload needs more memory.
The current image does not target A100, H100, RTX 5090, or other Blackwell GPUs.

| Setting | Value |
| --- | --- |
| Endpoint type | Queue |
| GPU count | 1 per worker |
| GPU pool | `AMPERE_24` |
| CUDA compatibility | 12.8 and all newer available versions |
| Active workers | 0 |
| Max workers | 1 |
| Idle timeout | 30 seconds |
| Execution timeout | 900 seconds |
| Container disk | 20 GB |
| Network volume | 20 GB at `/runpod-volume` |
| FlashBoot | Enabled |
| Start command | Keep image default |
| Exposed ports | None |

Create the network volume in a data center offering the selected GPUs. Attaching one volume
restricts workers to that data center. The model cache uses about 13.6 GB of the volume.
Keep only this pinned model set in the 20 GB volume; extra quantizations need more storage.
At RunPod's documented standard rate of $0.07/GB/month, 20 GB costs about $1.40/month
even when workers scale to zero. The persistent cache avoids repeated downloads on fresh workers.

Start at 1024 x 1024, one image per job, 8 steps, CFG 1. These sampling settings are already
fixed by the worker. If memory is insufficient, try 768 x 768 before changing quantizations.
The AD-Q4_K encoder is a smaller fallback, but changes images and is not the current default.

## Deploy without using local disk

Use [RunPod's GitHub integration](https://docs.runpod.io/serverless/workers/github-integration).
RunPod builds the CUDA image remotely; model downloads happen on RunPod at worker startup.
No local Docker build, CUDA image, model download, or Docker registry account is needed.

1. Push these project files to a GitHub repository, with `Dockerfile` in its root.
2. In RunPod **Storage**, create a 20 GB network volume in a data center offering the selected GPUs.
3. Open [RunPod Serverless](https://console.runpod.io/serverless), choose **New Endpoint**, then **Import Git Repository**. Connect GitHub if needed and select that repository, its branch, and the root `Dockerfile`.
4. Apply the endpoint settings in the table above. Select the network volume in **Advanced > Network Volumes**. For an existing endpoint, use **Manage > Edit Endpoint** first. Leave the image's start command unchanged.
5. Click **Deploy Endpoint**. RunPod builds the image before starting workers; check the **Builds** tab and then worker logs. Each worker handles one image at a time. Active workers 0 allows scale-to-zero; idle workers still incur charges until they stop.

Space budget: project source uses only tens of KB locally. RunPod needs about **13.6 GB for weights**,
plus the CUDA runtime image and temporary build space. Do not build this image on a nearly full Mac.
Do not put RunPod or Hugging Face tokens in source or build arguments.

Public model downloads need no Hugging Face token. If your account requires authentication, add `HF_TOKEN` as a RunPod endpoint secret.

RunPod limits the Docker build step to 30 minutes. If CUDA compilation exceeds that limit,
use the remote Linux/CI build option below, then import its image. Neither route needs a local build.

## First test in RunPod console

Open the deployed endpoint's **Requests** tab. Paste the contents of `test_input.json` and click **Run**:

```json
{
  "input": {
    "prompt": "A small wooden boat on a quiet Cambodian lake at sunrise, natural photography",
    "width": 1024,
    "height": 1024,
    "seed": 42
  }
}
```

The first job downloads weights and loads the engine. Watch **Workers** logs for the three
`Preparing ...` messages, downloads, and `listening on: http://127.0.0.1:1234`.
Successful job status is `COMPLETED`, with PNG base64 at `output.image_base64`.
Run the same job again before the idle timeout to check warm-worker reuse.

## Send a request

Set `RUNPOD_API_KEY` securely in your shell and `RUNPOD_ENDPOINT_ID` to the deployed endpoint ID.
Use asynchronous `/run` for cold starts, then poll the returned job ID:

```sh
curl --fail-with-body "https://api.runpod.ai/v2/${RUNPOD_ENDPOINT_ID}/run" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
  -H 'Content-Type: application/json' \
  --data-binary @test_input.json

export RUNPOD_JOB_ID=REPLACE_WITH_RETURNED_JOB_ID
curl --fail-with-body "https://api.runpod.ai/v2/${RUNPOD_ENDPOINT_ID}/status/${RUNPOD_JOB_ID}" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
  -o result.json
```

Poll until `status` is `COMPLETED`. Save the PNG from the completed response:

```sh
python3 - <<'PY'
import base64, json
from pathlib import Path

result = json.loads(Path("result.json").read_text())
assert result["status"] == "COMPLETED", result
Path("output.png").write_bytes(base64.b64decode(result["output"]["image_base64"], validate=True))
print("Saved output.png; seed:", result["output"]["seed"])
PY
```

If status is `IN_QUEUE` or `IN_PROGRESS`, repeat the status request after a few seconds.
For `FAILED` or `TIMED_OUT`, inspect the response's `error` and worker logs.
Asynchronous results expire 30 minutes after completion; save the PNG before then.
The API key belongs in your application backend or local shell, never browser/frontend code.

For later GitHub deployments, create a **new GitHub release** after pushing changes;
RunPod's GitHub integration does not redeploy on every commit alone.

Input fields: required `prompt` (1–4000 characters), optional `width` and `height`
(default 1024, multiples of 32 from 256 to 1536, at most 1,048,576 pixels total),
and optional `seed` (0–4294967295; random if omitted).
Output contains `image_base64`, `mime_type`, `width`, `height`, and `seed`.
Validation errors fail the job. Backend failures also request worker recycling.
RunPod's API key protects access; the model server listens only on container loopback.

## Checks

No dependencies, models, or GPU needed for the local contract checks:

```sh
python3 -m unittest -v test_worker.py
```

These checks use a fake local inference server. They cover input limits, blocked backend
configuration injection, request/response handling, worker recycling, and startup sampling arguments.
They do not prove CUDA compilation or image generation.

After deployment, submit `test_input.json`, verify the saved image, then submit a second job
to verify the same worker remains usable. Stop workers and start again to verify volume cache reuse.
CUDA compilation and GPU image generation still require these remote checks.

## Optional: build on another machine

Use a Linux build machine or CI runner with enough space for CUDA build layers.
GPU is not required to build. Replace `YOUR_DOCKERHUB_USER` below with your registry namespace.

```sh
export WORKER_IMAGE=docker.io/YOUR_DOCKERHUB_USER/qwen-image-turbo-runpod:v1
docker login
docker buildx build --platform linux/amd64 -t "$WORKER_IMAGE" --push .
```

Import the published image using RunPod's **Import from Docker Registry** option and the
same endpoint settings above. On a GPU machine, test the image directly with:

```sh
docker run --rm --gpus all \
  -v qwen-image-cache:/runpod-volume \
  "$WORKER_IMAGE" python -u worker.py \
  --test_input '{"input":{"prompt":"A red ceramic cup on a wooden table","seed":42}}'
```

## Model terms

The encoder's model card lists Apache-2.0. The paired Turbo denoiser uses the
[Qwen Research License](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo/blob/main/LICENSE),
which permits research/evaluation use and requires a separate license for commercial use.
The encoder's license does not replace the denoiser's terms.
