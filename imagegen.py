"""
ComfyUI image generation module.
Provides a REST endpoint and helper functions for generating scene images
via a ComfyUI instance.

ComfyUI API flow:
  1. POST /prompt  →  {prompt_id, number}
  2. Poll GET /history/{prompt_id}  →  outputs with image filenames
  3. GET /view?filename=...&subfolder=...&type=output  →  image bytes
"""
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db

router = APIRouter()

POLL_INTERVAL = 1.0  # seconds between history polls
MAX_POLL_TIME = 120   # max seconds to wait for generation


def _default_workflow(prompt_text: str, negative: str = "") -> dict:
    """Build a minimal txt2img ComfyUI workflow (API format, node IDs as string keys)."""
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(time.time()) % (2**32),
                "steps": db.IMAGEGEN_STEPS,
                "cfg": db.IMAGEGEN_CFG_SCALE,
                "sampler_name": db.IMAGEGEN_SAMPLER,
                "scheduler": db.IMAGEGEN_SCHEDULER,
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": db.IMAGEGEN_WIDTH,
                "height": db.IMAGEGEN_HEIGHT,
                "batch_size": 1,
            },
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt_text, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative or "bad quality, low resolution, blurry", "clip": ["4", 1]},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["8", 0], "filename_prefix": "cs_scene"},
        },
    }


async def _poll_history(base_url: str, prompt_id: str, timeout: float = MAX_POLL_TIME) -> dict:
    """Poll ComfyUI /history/{prompt_id} until the result is ready."""
    start = time.time()
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() - start < timeout:
            try:
                resp = await client.get(f"{base_url}/history/{prompt_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    if prompt_id in data:
                        return data[prompt_id]
            except (httpx.RequestError, json.JSONDecodeError):
                pass
            await asyncio.sleep(POLL_INTERVAL)
    raise TimeoutError(f"ComfyUI generation timed out after {timeout}s (prompt_id={prompt_id})")


def _extract_output_filename(history_entry: dict):
    """Extract the first output image filename + subfolder from a history entry."""
    outputs = history_entry.get("outputs", {})
    for node_id, node_output in outputs.items():
        images = node_output.get("images", [])
        if images:
            img = images[0]
            return img.get("filename"), img.get("subfolder", ""), img.get("type", "output")
    return None, None, None


async def generate_image(prompt_text: str, negative_prompt: str = "") -> dict:
    """
    Call ComfyUI to generate an image from a text prompt.
    Returns dict with:
      - success: bool
      - image_path: str (relative filename in UPLOAD_DIR) on success
      - error: str on failure
    """
    base_url = db.IMAGEGEN_BASE_URL.rstrip("/")
    if not db.IMAGEGEN_ENABLED:
        return {"success": False, "error": "Image generation is disabled. Enable it in Settings → Image Generation."}

    # Build workflow (custom or default)
    if db.IMAGEGEN_WORKFLOW and isinstance(db.IMAGEGEN_WORKFLOW, dict):
        # Inject prompt/negative via %positive% / %negative% placeholders.
        # Serialize → replace → parse so placeholders can appear anywhere in the workflow.
        neg_text = negative_prompt or db.IMAGEGEN_NEGATIVE or "bad quality, low resolution, blurry"
        workflow_json = json.dumps(db.IMAGEGEN_WORKFLOW)
        workflow_json = workflow_json.replace("%positive%", prompt_text.replace("\\", "\\\\").replace('"', '\\"'))
        workflow_json = workflow_json.replace("%negative%", neg_text.replace("\\", "\\\\").replace('"', '\\"'))
        workflow = json.loads(workflow_json)
    else:
        workflow = _default_workflow(prompt_text, negative_prompt or db.IMAGEGEN_NEGATIVE)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Queue the prompt
            resp = await client.post(
                f"{base_url}/prompt",
                json={"prompt": workflow, "client_id": str(uuid.uuid4())},
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"ComfyUI /prompt returned {resp.status_code}: {resp.text[:200]}"}
            result = resp.json()
            prompt_id = result.get("prompt_id")
            if not prompt_id:
                return {"success": False, "error": f"ComfyUI did not return prompt_id: {result}"}

        # 2. Poll for completion
        history_entry = await _poll_history(base_url, prompt_id)

        # 3. Extract image info
        filename, subfolder, img_type = _extract_output_filename(history_entry)
        if not filename:
            return {"success": False, "error": "No image output found in ComfyUI history"}

        # 4. Download the image
        async with httpx.AsyncClient(timeout=60) as client:
            params = {"filename": filename}
            if subfolder:
                params["subfolder"] = subfolder
            if img_type:
                params["type"] = img_type
            img_resp = await client.get(f"{base_url}/view", params=params)
            if img_resp.status_code != 200:
                return {"success": False, "error": f"Failed to download image: {img_resp.status_code}"}

            # 5. Save to UPLOAD_DIR with a unique name
            ext = Path(filename).suffix or ".png"
            safe_name = f"gen_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
            save_path = db.UPLOAD_DIR / safe_name
            save_path.write_bytes(img_resp.content)

        return {"success": True, "image_path": safe_name}

    except TimeoutError as e:
        return {"success": False, "error": str(e)}
    except httpx.RequestError as e:
        return {"success": False, "error": f"Cannot connect to ComfyUI at {base_url}: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {e}"}


# ─── REST endpoints ────────────────────────────────────────────────

@router.post("/api/generate-image")
async def api_generate_image(request: Request):
    """
    Generate a scene image via ComfyUI.
    Request body: {"prompt": "...", "negative_prompt": "...", "session_id": N, "mode": "rp"|"school"}
    Returns: {"success": true, "image_path": "..."} or {"success": false, "error": "..."}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body"}, status_code=400)

    prompt_text = body.get("prompt", "").strip()
    if not prompt_text:
        return JSONResponse({"success": False, "error": "Prompt is required"}, status_code=400)

    negative = body.get("negative_prompt", "") or db.IMAGEGEN_NEGATIVE

    result = await generate_image(prompt_text, negative)
    if not result["success"]:
        return JSONResponse(result, status_code=500)
    return JSONResponse(result)


@router.get("/api/imagegen/status")
async def api_imagegen_status():
    """Check if ComfyUI is reachable and image generation is enabled."""
    if not db.IMAGEGEN_ENABLED:
        return JSONResponse({"enabled": False, "reachable": False, "base_url": db.IMAGEGEN_BASE_URL})

    base_url = db.IMAGEGEN_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base_url}/system_stats")
            reachable = resp.status_code == 200
        return JSONResponse({"enabled": True, "reachable": reachable, "base_url": db.IMAGEGEN_BASE_URL})
    except Exception:
        return JSONResponse({"enabled": True, "reachable": False, "base_url": db.IMAGEGEN_BASE_URL})
