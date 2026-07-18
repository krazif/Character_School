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
from openai import AsyncOpenAI

import db

router = APIRouter()

POLL_INTERVAL = 1.0  # seconds between history polls
MAX_POLL_TIME = 120   # max seconds to wait for generation


def _resolve_seed(override: int | None = None) -> int:
    """Return the seed to use for this generation — explicit override, fixed, or random."""
    if override is not None:
        return override
    if db.IMAGEGEN_RANDOM_SEED:
        return __import__('random').randint(0, 2**32 - 1)
    return db.IMAGEGEN_SEED


def _default_workflow(prompt_text: str, negative: str = "", seed: int | None = None, width: int | None = None, height: int | None = None) -> dict:
    """Build a minimal txt2img ComfyUI workflow (API format, node IDs as string keys)."""
    s = _resolve_seed(seed)
    w = width if width is not None else db.IMAGEGEN_WIDTH
    h = height if height is not None else db.IMAGEGEN_HEIGHT
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": s,
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
                "width": w,
                "height": h,
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


async def generate_image(prompt_text: str, negative_prompt: str = "", seed: int | None = None, width: int | None = None, height: int | None = None) -> dict:
    """
    Call ComfyUI to generate an image from a text prompt.
    Returns dict with:
      - success: bool
      - image_path: str (relative filename in UPLOAD_DIR) on success
      - seed: int (the seed actually used)
      - error: str on failure
    """
    base_url = db.IMAGEGEN_BASE_URL.rstrip("/")
    if not db.IMAGEGEN_ENABLED:
        return {"success": False, "error": "Image generation is disabled. Enable it in Settings → Image Generation."}

    # Build workflow (custom or default)
    seed_val = _resolve_seed(seed)
    if db.IMAGEGEN_WORKFLOW and isinstance(db.IMAGEGEN_WORKFLOW, dict):
        # Inject prompt/negative via %positive% / %negative% placeholders.
        # Serialize → replace → parse so placeholders can appear anywhere in the workflow.
        neg_text = negative_prompt or db.IMAGEGEN_NEGATIVE or "bad quality, low resolution, blurry"
        # String placeholders: replaced as escaped JSON strings
        string_replacements = {
            "%positive%": prompt_text,
            "%negative%": neg_text,
        }
        # Numeric placeholders: must be injected as raw JSON numbers, not quoted strings.
        # We do this by replacing the quoted placeholder " %placeholder% " with the raw number.
        numeric_replacements = {
            "%width%": width if width is not None else db.IMAGEGEN_WIDTH,
            "%height%": height if height is not None else db.IMAGEGEN_HEIGHT,
            "%steps%": db.IMAGEGEN_STEPS,
            "%cfg%": db.IMAGEGEN_CFG_SCALE,
            "%seed%": seed_val,
        }
        workflow_json = json.dumps(db.IMAGEGEN_WORKFLOW)
        # String replacements (escape for JSON)
        for placeholder, value in string_replacements.items():
            workflow_json = workflow_json.replace(placeholder, value.replace("\\", "\\\\").replace('"', '\\"'))
        # Numeric replacements: replace quoted "%placeholder%" with raw number (unquoted)
        for placeholder, value in numeric_replacements.items():
            workflow_json = workflow_json.replace(f'"{placeholder}"', str(value))
            # Also replace bare %placeholder% for inline use in text strings
            workflow_json = workflow_json.replace(placeholder, str(value))
        workflow = json.loads(workflow_json)
    else:
        workflow = _default_workflow(prompt_text, negative_prompt or db.IMAGEGEN_NEGATIVE, seed=seed, width=width, height=height)

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

            # Save sidecar metadata JSON
            meta = {
                "prompt": prompt_text,
                "seed": seed_val,
                "negative_prompt": negative_prompt or "",
                "width": width if width is not None else db.IMAGEGEN_WIDTH,
                "height": height if height is not None else db.IMAGEGEN_HEIGHT,
                "timestamp": time.time(),
            }
            meta_path = db.UPLOAD_DIR / (safe_name + ".json")
            meta_path.write_text(json.dumps(meta, ensure_ascii=False))

        return {"success": True, "image_path": safe_name, "prompt": prompt_text, "seed": seed_val}

    except TimeoutError as e:
        return {"success": False, "error": str(e)}
    except httpx.RequestError as e:
        return {"success": False, "error": f"Cannot connect to ComfyUI at {base_url}: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {e}"}


# ─── Auto image prompt generation (Phase 3) ────────────────────────

AUTO_IMG_SYSTEM = """You are a scene image prompt generator for a roleplay/character chat application.
Your job: read the recent conversation and decide if this moment is worth illustrating.

If the scene is visually interesting (a dramatic moment, new location, emotional beat, character interaction with visual cues), generate a concise image generation prompt.

If the conversation is mundane (greetings, short exchanges with no visual change, OOC chatter), return SKIP.

Rules for the image prompt:
- Write a single comma-separated prompt, no sentences, no explanations.
- Describe: subjects, appearance, clothing, pose, expression, setting, lighting, mood, art style.
- Prefer "anime style" or "digital painting" as art style unless the scene is photorealistic.
- Keep it under 80 words.
- Do NOT include character speech or dialogue.
- Focus on the visual moment, not the history.

Respond in EXACTLY one of these two formats:
1. On the first line: PROMPT: <your image prompt here>
2. On the first line: SKIP"""


async def auto_generate_image_prompt(messages: list[dict], send_fn=None, force: bool = False) -> str | None:
    """
    Ask the auto-gen LLM whether the current scene is worth illustrating.
    Returns a prompt string if yes, or None if the LLM says SKIP or on error.
    `messages` is a list of {"role": ..., "content": ...} dicts (recent conversation).
    When `force=True`, bypass the enabled check (for manual on-demand use).
    """
    if not force and not db.IMAGEGEN_AUTO_ENABLED:
        return None

    # Build conversation snippet (last few messages)
    convo_lines = []
    for m in messages[-8:]:
        role = m.get("role", "unknown")
        speaker = m.get("speaker", "")
        content = m.get("content", "")
        label = speaker or role
        convo_lines.append(f"[{label}]: {content}")
    convo_text = "\n".join(convo_lines)

    llm_messages = [
        {"role": "system", "content": AUTO_IMG_SYSTEM},
        {"role": "user", "content": f"Recent conversation:\n{convo_text}\n\nDecide: is this moment worth illustrating?"},
    ]

    if send_fn:
        await send_fn({
            "type": "console_event", "event": "request", "llm": "imagegen_auto",
            "model": db.IMAGEGEN_AUTO_MODEL, "label": "Auto Image Prompt",
            "temperature": db.IMAGEGEN_AUTO_TEMPERATURE, "max_tokens": db.IMAGEGEN_AUTO_MAX_TOKENS,
            "messages": llm_messages, "timestamp": db._now_iso() if hasattr(db, "_now_iso") else _now_iso(),
        })

    try:
        kwargs = dict(
            model=db.IMAGEGEN_AUTO_MODEL,
            messages=llm_messages,
            temperature=db.IMAGEGEN_AUTO_TEMPERATURE,
            max_tokens=db.IMAGEGEN_AUTO_MAX_TOKENS,
        )
        resp = await db.imagegen_auto_client.chat.completions.create(**kwargs)
        raw = resp.choices[0].message.content.strip()
        usage = resp.usage

        if send_fn:
            await send_fn({
                "type": "console_event", "event": "response", "llm": "imagegen_auto",
                "model": db.IMAGEGEN_AUTO_MODEL, "label": "Auto Image Prompt",
                "content": raw,
                "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                "finish_reason": resp.choices[0].finish_reason, "timestamp": _now_iso(),
            })

        # Parse response
        first_line = raw.split("\n")[0].strip()
        if first_line.upper().startswith("SKIP"):
            return None
        if first_line.upper().startswith("PROMPT:"):
            return first_line[len("PROMPT:"):].strip()
        # Fallback: if the LLM didn't follow format, treat the whole thing as a prompt if not SKIP
        if "SKIP" in raw.upper():
            return None
        return raw.strip() or None

    except Exception as e:
        if send_fn:
            try:
                await send_fn({"type": "error", "message": f"Auto image prompt error: {e}"})
            except Exception:
                pass
        return None


async def maybe_auto_generate_image(session_id: int, messages: list[dict], send_fn, mode: str = "rp",
                                     image_add_fn=None) -> None:
    """
    Check if auto image generation should trigger (based on message count interval),
    ask the LLM for a prompt, generate the image, and insert it into the chat.

    Args:
        session_id: DB session ID
        messages: recent messages from the session (list of dicts with role/content)
        send_fn: async callable for sending WS events
        mode: "rp" or "school"
        image_add_fn: async callable(image_path: str) to insert the generated image
                      into the session's DB + send the WS message. If None, uses a default.
    """
    if not db.IMAGEGEN_AUTO_ENABLED or not db.IMAGEGEN_ENABLED:
        return

    interval = max(1, db.IMAGEGEN_AUTO_INTERVAL)
    msg_count = len(messages)
    if msg_count == 0 or msg_count % interval != 0:
        return

    # Ask LLM for a prompt
    prompt = await auto_generate_image_prompt(messages, send_fn=send_fn)
    if not prompt:
        return

    await send_fn({"type": "console_event", "event": "auto_image_prompt", "content": prompt, "timestamp": _now_iso()})

    # Generate the image via ComfyUI
    negative = db.IMAGEGEN_AUTO_NEGATIVE or db.IMAGEGEN_NEGATIVE or ""
    result = await generate_image(prompt, negative)
    if not result.get("success"):
        await send_fn({"type": "console_event", "event": "auto_image_error", "error": result.get("error", "unknown"), "timestamp": _now_iso()})
        return

    image_path = result["image_path"]
    await send_fn({"type": "console_event", "event": "auto_image_generated", "image_path": image_path, "prompt": prompt, "timestamp": _now_iso()})

    # Insert into session via callback
    if image_add_fn:
        await image_add_fn(image_path, prompt, result.get("seed"))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


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

    seed_override = body.get("seed")
    if seed_override is not None:
        try:
            seed_override = int(seed_override)
        except (ValueError, TypeError):
            seed_override = None

    result = await generate_image(prompt_text, negative, seed=seed_override,
                                  width=body.get("width"), height=body.get("height"))
    if not result["success"]:
        return JSONResponse(result, status_code=500)
    return JSONResponse(result)


@router.post("/api/imagegen/auto-prompt")
async def api_auto_image_prompt(request: Request):
    """
    Use the auto-gen LLM to produce a scene image prompt from recent messages.
    Request body: {"messages": [{"role": "...", "content": "...", ...}, ...]}
    Returns: {"success": true, "prompt": "..."} or {"success": false, "error": "..."}
    When the LLM decides SKIP, returns {"success": true, "prompt": null, "skipped": true}.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body"}, status_code=400)
    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"success": False, "error": "No messages provided"}, status_code=400)
    prompt = await auto_generate_image_prompt(messages, force=True)
    if prompt is None:
        return JSONResponse({"success": True, "prompt": None, "skipped": True})
    return JSONResponse({"success": True, "prompt": prompt, "skipped": False})


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
