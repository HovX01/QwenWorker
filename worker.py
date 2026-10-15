import base64
import io
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request

from PIL import Image


SERVER_URL = "http://127.0.0.1:1234"
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 1024 * 1024
TURBO_SIGMAS = "1.0,0.978453,0.95418,0.926626,0.89508,0.845148,0.704534,0.414568,0.0"
MODELS = {
    "--diffusion-model": (
        "AtomicChat/Qwen-Image-2.1-Turbo-GGUF",
        "Qwen-Image-2.1-Turbo-AD-Q4_K.gguf",
        "f5996597b053664d0885cfaa0dcc88afb47f92f6",
    ),
    "--llm": (
        "AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF",
        "Qwen-Image-2.1-Turbo-Uncensored-Q8_0.gguf",
        "df96693dbdfb8738c0bf1bbd0337d149808db948",
    ),
    "--vae": (
        "Comfy-Org/Qwen-Image-2.1",
        "vae/qwen_image_2.1_vae_bf16.safetensors",
        "a8342359c61979b06a8dafb1a47991fe1f30e994",
    ),
    "--llm_vision": (
        "AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF",
        "mmproj-Qwen-Image-2.1-Turbo-Uncensored-F16.gguf",
        "df96693dbdfb8738c0bf1bbd0337d149808db948",
    ),
}


def handler(job):
    data = job.get("input") if isinstance(job, dict) else None
    if not isinstance(data, dict):
        return {"error": "input must be an object"}
    if set(data) - {"prompt", "width", "height", "seed", "image_base64"}:
        return {"error": "Allowed input fields: prompt, width, height, seed, image_base64"}

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        return {"error": "prompt must contain 1 to 4000 characters"}
    # sd-server interprets this marker as JSON overrides, bypassing input limits.
    if "sd_cpp_extra_args" in prompt.lower():
        return {"error": "Backend configuration markers are not allowed in prompt"}

    width, height = data.get("width", 1024), data.get("height", 1024)
    for name, value in (("width", width), ("height", height)):
        if type(value) is not int or not 256 <= value <= 1536 or value % 32:
            return {"error": f"{name} must be an integer multiple of 32 from 256 to 1536"}
    # shortcut: inline results stay at 1 megapixel; add object storage before raising this limit.
    if width * height > MAX_IMAGE_PIXELS:
        return {"error": "width * height must not exceed 1048576 pixels"}

    seed = data.get("seed", secrets.randbelow(2**32))
    if type(seed) is not int or not 0 <= seed < 2**32:
        return {"error": "seed must be an integer from 0 to 4294967295"}

    payload = {
        "prompt": prompt,
        "width": width,
        "height": height,
        "seed": seed,
        "steps": 8,
        "cfg_scale": 1.0,
        "sampler_name": "Euler",
        "batch_size": 1,
    }
    if "image_base64" in data:
        reference = data["image_base64"]
        if not isinstance(reference, str) or not reference:
            return {"error": "image_base64 must be a nonempty raw base64 string"}
        if len(reference) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            return {"error": "Input image must not exceed 4 MiB"}
        try:
            decoded = base64.b64decode(reference, validate=True)
            if len(decoded) > MAX_IMAGE_BYTES:
                return {"error": "Input image must not exceed 4 MiB"}
            with Image.open(io.BytesIO(decoded), formats=("PNG", "JPEG")) as source:
                if max(source.size) > 1536 or source.width * source.height > MAX_IMAGE_PIXELS:
                    return {"error": "Input image must not exceed 1536 pixels per side or 1048576 pixels total"}
                source.load()
        except (ValueError, OSError, SyntaxError, Image.DecompressionBombError):
            return {"error": "image_base64 must contain a valid PNG or JPEG"}
        payload["extra_images"] = [reference]

    request = urllib.request.Request(
        SERVER_URL + "/sdapi/v1/txt2img",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            result = json.load(response)
        image = result["images"][0]
        if not isinstance(image, str) or not image:
            raise ValueError("Backend returned no image")
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        return {"error": f"Inference backend failed: {exc}", "refresh_worker": True}

    return {
        "image_base64": image,
        "mime_type": "image/png",
        "seed": seed,
        "width": width,
        "height": height,
    }


def start_backend():
    from huggingface_hub import hf_hub_download

    command = [
        "sd-server",
        "--listen-ip", "127.0.0.1",
        "--listen-port", "1234",
        "--steps", "8",
        "--cfg-scale", "1.0",
        "--sampling-method", "euler",
        "--sigmas", TURBO_SIGMAS,
        "--diffusion-fa",
        "--rng", "cpu",
    ]
    for flag, (repo, filename, revision) in MODELS.items():
        print(f"Preparing {repo}/{filename}", flush=True)
        path = hf_hub_download(
            repo_id=repo,
            filename=filename,
            revision=revision,
            cache_dir=os.environ.get("HF_HUB_CACHE", "/runpod-volume/huggingface/hub"),
        )
        command.extend((flag, path))

    process = subprocess.Popen(command)
    deadline = time.monotonic() + 600
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(SERVER_URL + "/", timeout=2):
                return process
        except OSError:
            time.sleep(1)
    process.terminate()
    process.wait(timeout=10)
    raise RuntimeError("sd-server failed to start; inspect worker logs")


if __name__ == "__main__":
    import runpod

    backend = start_backend()
    try:
        runpod.serverless.start({"handler": handler})
    finally:
        backend.terminate()
        backend.wait(timeout=10)
