"""
Character School — School mode routes + WebSocket + character generation.
Split from server.py.
"""
import json
import re
import asyncio
import base64
import io
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from json_repair import repair_json
import db
import engine
import lorebook

router = APIRouter()

@router.get("/api/cards")
async def api_list_cards():
    return JSONResponse(engine.list_cards())


@router.get("/api/cards/{filename}")
async def api_get_card(filename: str):
    try:
        card = engine.load_card(filename)
        return JSONResponse(card)
    except FileNotFoundError:
        return JSONResponse({"error": "Card not found"}, status_code=404)
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)


@router.delete("/api/cards/{filename}")
async def api_delete_card(filename: str):
    """Delete a character card JSON and its associated avatar PNG."""
    path = db.CHARACTERS_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "Card not found"}, status_code=404)
    try:
        path.unlink()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    # Also delete avatar PNG if it exists
    stem = Path(filename).stem
    avatar_path = db.CHARACTERS_DIR / f"{stem}_avatar.png"
    if avatar_path.exists():
        try:
            avatar_path.unlink()
        except Exception:
            pass  # non-fatal if avatar deletion fails
    return JSONResponse({"status": "deleted", "filename": filename})


@router.put("/api/cards/{filename}")
async def api_save_card(filename: str, data: dict):
    try:
        engine.save_card(filename, data)
        return JSONResponse({"status": "saved", "filename": filename})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── PNG Character Card Support ───────────────────────────────────
# PNG character cards (SillyTavern / Chub.ai format) embed the full
# character card JSON as a base64-encoded string in a tEXt chunk with
# the key "chara". The image itself serves as the character's avatar.

def _parse_png_text_chunks(png_bytes: bytes) -> dict[str, str]:
    """Manually parse all tEXt and iTXt chunks from a PNG file.
    Returns a dict of keyword → text value."""
    chunks = {}
    if png_bytes[:8] != b'\x89PNG\r\n\x1a\n':
        return chunks
    pos = 8
    while pos + 8 <= len(png_bytes):
        length = int.from_bytes(png_bytes[pos:pos+4], 'big')
        chunk_type = png_bytes[pos+4:pos+8]
        data = png_bytes[pos+8:pos+8+length]
        pos += 8 + length + 4  # length + type + data + CRC
        if chunk_type == b'tEXt':
            null_idx = data.find(b'\x00')
            if null_idx > 0:
                key = data[:null_idx].decode('latin-1')
                val = data[null_idx+1:].decode('latin-1')
                chunks[key] = val
        elif chunk_type == b'iTXt':
            null_idx = data.find(b'\x00')
            if null_idx > 0:
                key = data[:null_idx].decode('utf-8')
                # iTXt format: keyword\x00 compression_flag(1) compression_method(1) lang_tag\x00 translated_keyword\x00 text
                rest = data[null_idx+1:]
                if len(rest) >= 2:
                    comp_flag = rest[0]
                    comp_method = rest[1]
                    # Find lang tag null
                    lang_end = rest.find(b'\x00', 2)
                    if lang_end > 0:
                        trans_end = rest.find(b'\x00', lang_end+1)
                        if trans_end > 0:
                            text_data = rest[trans_end+1:]
                            if comp_flag == 0:
                                val = text_data.decode('utf-8')
                            else:
                                try:
                                    import zlib
                                    val = zlib.decompress(text_data).decode('utf-8')
                                except Exception:
                                    continue
                            chunks[key] = val
        if chunk_type == b'IEND':
            break
    return chunks


def extract_chara_from_png(png_bytes: bytes) -> dict | None:
    """Extract character card JSON from a PNG's tEXt/iTXt chunk.
    Checks for 'chara' (SillyTavern V2) and 'ccv3' (V3) keys.
    Returns the parsed dict, or None if no chara chunk found."""
    # First try Pillow
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes))
        for key in ('chara', 'ccv3', 'character_card'):
            chara_b64 = img.info.get(key)
            if chara_b64:
                raw = base64.b64decode(chara_b64)
                return json.loads(raw)
    except Exception:
        pass

    # Fallback: manually parse PNG chunks (catches iTXt that Pillow might miss)
    chunks = engine._parse_png_text_chunks(png_bytes)
    for key in ('chara', 'ccv3', 'character_card'):
        val = chunks.get(key)
        if val:
            try:
                raw = base64.b64decode(val)
                return json.loads(raw)
            except Exception:
                # Maybe it's raw JSON, not base64
                try:
                    return json.loads(val)
                except Exception:
                    continue
    return None


def create_chara_png(card_data: dict, avatar_bytes: bytes | None = None) -> bytes:
    """Create a PNG image with the character card JSON embedded as a
    base64 tEXt 'chara' chunk (SillyTavern-compatible).
    If avatar_bytes is a valid image, use it; otherwise generate a
    placeholder with the character name."""
    from PIL import Image, ImageDraw, ImageFont
    from PIL.PngImagePlugin import PngInfo

    # Try to use provided avatar, else make placeholder
    if avatar_bytes:
        try:
            img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
        except Exception:
            img = None
    else:
        img = None

    if img is None:
        # Generate a 400x600 placeholder with character name
        d = card_data.get("data", card_data)
        name = d.get("name", "Unknown")
        img = Image.new("RGBA", (400, 600), (30, 33, 40, 255))
        draw = ImageDraw.Draw(img)
        # Try a font, fall back to default
        try:
            font = ImageFont.truetype("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 28)
        except Exception:
            font = ImageFont.load_default()
        # Center the name
        bbox = draw.textbbox((0, 0), name, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((400 - tw) / 2, (600 - th) / 2), name, fill=(200, 200, 210, 255), font=font)

    # Embed chara data as base64 tEXt chunk
    json_str = json.dumps(card_data, ensure_ascii=False)
    chara_b64 = base64.b64encode(json_str.encode("utf-8")).decode("ascii")

    pnginfo = PngInfo()
    pnginfo.add_text("chara", chara_b64)

    out = io.BytesIO()
    img.save(out, format="PNG", pnginfo=pnginfo)
    return out.getvalue()


@router.post("/api/cards/upload")
async def api_upload_card(file: UploadFile = File(...)):
    """Upload a character card JSON or PNG file."""
    try:
        content = await file.read()
        filename_in = file.filename or ""

        # Detect PNG files by magic bytes or extension
        is_png = content[:8] == b'\\x89PNG\\r\\n\\x1a\\n' or filename_in.lower().endswith(".png")

        if is_png:
            # Extract chara JSON from PNG tEXt/iTXt chunk
            data = engine.extract_chara_from_png(content)
            if data is None:
                # Check if it's a valid PNG image without chara data
                from PIL import Image
                try:
                    img = Image.open(io.BytesIO(content))
                    return JSONResponse({
                        "error": "No 'chara' metadata found in PNG. This appears to be a regular image, not a character card PNG. Character card PNGs must have embedded JSON metadata (tEXt 'chara' chunk)."
                    }, status_code=400)
                except Exception:
                    return JSONResponse({"error": "File has .png extension but is not a valid PNG image."}, status_code=400)
        else:
            text = content.decode("utf-8")
            data = json.loads(text)

        # Validate basic structure
        d = data.get("data", data)
        name = d.get("name", "")
        if not name:
            return JSONResponse({"error": "Invalid character card: missing 'name' field"}, status_code=400)

        # Derive filename from card name
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name.lower()).strip('_')
        if not safe_name:
            safe_name = "uploaded_card"
        filename = f"{safe_name}.json"

        engine.save_card(filename, data)

        # If it was a PNG, also save the avatar image
        if is_png:
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(content))
                avatar_path = db.CHARACTERS_DIR / f"{safe_name}_avatar.png"
                # Save just the image without metadata to keep it small
                img.convert("RGBA").save(str(avatar_path), format="PNG")
            except Exception:
                pass  # avatar save is best-effort

        return JSONResponse({"status": "uploaded", "filename": filename, "name": name, "format": "png" if is_png else "json"})
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/cards/{filename}/png")
async def api_download_card_png(filename: str):
    """Download a character card as a PNG with embedded chara metadata."""
    try:
        path = db.CHARACTERS_DIR / filename
        if not path.exists():
            return JSONResponse({"error": "Card not found"}, status_code=404)
        data = json.loads(path.read_text(encoding="utf-8"))

        # Check for an existing avatar image
        stem = path.stem
        avatar_bytes = None
        avatar_path = db.CHARACTERS_DIR / f"{stem}_avatar.png"
        if avatar_path.exists():
            avatar_bytes = avatar_path.read_bytes()

        png_bytes = engine.create_chara_png(data, avatar_bytes)

        d = data.get("data", data)
        name = d.get("name", stem)
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name.lower()).strip('_') or "card"
        download_name = f"{safe_name}.png"

        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{download_name}"'}
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/cards/{filename}/json")
async def api_download_card_json(filename: str):
    """Download a character card as raw JSON."""
    try:
        path = db.CHARACTERS_DIR / filename
        if not path.exists():
            return JSONResponse({"error": "Card not found"}, status_code=404)
        data = path.read_text(encoding="utf-8")
        return Response(
            content=data,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/cards/{filename}/validate")
async def api_validate_card(filename: str, data: dict):
    """Validate a card's JSON structure and check for common issues."""
    issues = []
    d = data.get("data", data)
    version = engine.detect_card_version(data)

    # Check required fields
    required = ["name", "description", "system_prompt"]
    for field in required:
        val = engine.get_card_field(d, field, version)
        if not val:
            issues.append({"severity": "error", "field": field, "message": f"Missing required field: {field}"})

    # Check for banned divine name references
    banned_words = ["Allah", "Alhamdulillah", "Insya-Allah", "Insya Allah", "Masya-Allah",
                    "Masya Allah", "Subhanallah", "Subhan Allah", "Insha Allah", "Insha-Allah",
                    "Astaghfirullah", "Allahu Akbar", "Bismillah"]
    raw = json.dumps(data)
    for word in banned_words:
        if word.lower() in raw.lower():
            idx = raw.lower().find(word.lower())
            ctx = raw[max(0, idx-40):idx+len(word)+40]
            issues.append({"severity": "warning", "field": "general", "message": f"Found '{word}' in card: ...{ctx}..."})

    # Check for Chinese Manglish particles outside "NOT" context
    import re
    for particle in ["lah", "lor", "meh", "kah"]:
        for m in re.finditer(r'\b' + particle + r'\b', raw):
            pos = m.start()
            ctx = raw[max(0, pos-60):pos+60]
            if "NOT" in ctx or "not Chinese" in ctx or "no lah" in ctx.lower():
                continue
            issues.append({"severity": "warning", "field": "speech", "message": f"Found '{particle}' outside negation context: ...{ctx}..."})

    return JSONResponse({"valid": len([i for i in issues if i["severity"] == "error"]) == 0, "issues": issues})


# ─── Persona API Routes ───────────────────────────────────────────
@router.get("/api/personas")
async def api_list_personas():
    return JSONResponse(engine.list_personas())


@router.get("/api/personas/{filename}")
async def api_get_persona(filename: str):
    try:
        persona = engine.load_persona(filename)
        return JSONResponse(persona)
    except FileNotFoundError:
        return JSONResponse({"error": "Persona not found"}, status_code=404)
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)


@router.put("/api/personas/{filename}")
async def api_save_persona(filename: str, data: dict):
    try:
        engine.save_persona(filename, data)
        return JSONResponse({"status": "saved", "filename": filename})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/personas/upload")
async def api_upload_persona(file: UploadFile = File(...)):
    """Upload a persona JSON file."""
    try:
        content = await file.read()
        text = content.decode("utf-8")
        data = json.loads(text)

        # Validate basic structure
        name = data.get("name", "")
        if not name:
            return JSONResponse({"error": "Invalid persona: missing 'name' field"}, status_code=400)

        # Derive filename from persona name
        import re as _re
        safe_name = _re.sub(r'[^a-zA-Z0-9_-]', '_', name.lower()).strip('_')
        if not safe_name:
            safe_name = "uploaded_persona"
        filename = f"{safe_name}.json"

        engine.save_persona(filename, data)
        return JSONResponse({"status": "uploaded", "filename": filename, "name": name})
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/personas/{filename}/validate")
async def api_validate_persona(filename: str, data: dict):
    """Validate a persona's structure."""
    issues = []
    required = ["name", "description", "personality"]
    for field in required:
        if not data.get(field):
            issues.append({"severity": "error", "field": field, "message": f"Missing required field: {field}"})

    return JSONResponse({"valid": len([i for i in issues if i["severity"] == "error"]) == 0, "issues": issues})


# ─── School Session REST Routes ────────────

@router.get("/api/school/sessions")
async def api_school_list_sessions():
    sessions = db.db_school_list_sessions()
    return JSONResponse({"sessions": sessions})


@router.get("/api/school/sessions/{session_id}")
async def api_school_get_session(session_id: int):
    sess = db.db_school_get_session(session_id)
    if not sess:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    messages = db.db_school_get_messages(session_id)
    stack_config = db.get_stack_config(sess)
    return JSONResponse({
        "session": sess,
        "messages": messages,
        "stack_config": stack_config,
    })


@router.delete("/api/school/sessions/{session_id}")
async def api_school_delete_session(session_id: int):
    deleted = db.db_school_delete_session(session_id)
    return JSONResponse({"deleted": deleted})


@router.post("/api/school/sessions/{session_id}/fork")
async def api_school_fork_session(session_id: int):
    result = db.db_school_fork_session(session_id)
    if not result:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return JSONResponse(result)


@router.put("/api/school/sessions/{session_id}/stack")
async def api_school_update_stack(session_id: int, data: dict):
    stack_config = json.dumps(data.get("stack_config", {}))
    db.db_school_update_settings(session_id, stack_config=stack_config)
    return JSONResponse({"updated": True})


# ─── School Auto-Summary Check ─────────────

async def check_school_auto_summary(session_id: int, ws: WebSocket) -> dict | None:
    """Auto-summarize by growing the anchor block (School mode).
    Finds the LAST summary_chunk with auto=true (the "anchor").
    When enough new messages accumulate past its end, summarizes ONLY
    the new batch (tail_n messages) and APPENDS the summary to the
    anchor's text. The anchor's end boundary expands — no new chunks.
    """
    sess = db.db_school_get_session(session_id)
    if not sess:
        return None
    stack_cfg = db.get_stack_config(sess)
    blocks = stack_cfg.get("blocks", [])

    # Normalize legacy types
    for blk in blocks:
        if blk.get("type") in ("early_summary", "late_summary", "summary"):
            blk["type"] = "summary_chunk"

    # Find the last summary_chunk with auto enabled (the anchor).
    # Also find the last enabled tail block to get tail_n.
    anchor_block = None
    anchor_idx = None
    tail_n = 0
    for i, b in enumerate(blocks):
        if b.get("type") == "summary_chunk" and b.get("enabled", True) and b.get("auto"):
            anchor_block = b
            anchor_idx = i
        if b.get("type") == "tail" and b.get("enabled", True):
            tail_n = b.get("n", 3)

    if not anchor_block or tail_n <= 0:
        return None

    all_msgs = db.db_school_get_messages(session_id)
    total_msgs = len(all_msgs)
    anchor_end = anchor_block.get("end", 0)

    # Enough unsummarized messages past the anchor?
    if total_msgs - anchor_end < tail_n:
        return None

    # Summarize ONLY the next batch (bounded input — never re-summarizes old ranges)
    batch_start = anchor_end
    batch_end = min(anchor_end + tail_n, total_msgs)
    batch_msgs = all_msgs[batch_start:batch_end]
    sum_msgs = [{"role": m["role"], "content": m["content"]} for m in batch_msgs]
    if not sum_msgs:
        return None

    await ws.send_json({"type": "block_summarizing", "block_index": anchor_idx, "auto": True})
    new_summary = await engine.rp_summarize(session_id, sum_msgs, "", ws=ws)

    # Grow the anchor: expand end boundary + append summary text
    anchor_block["end"] = batch_end
    existing_text = anchor_block.get("text", "")
    if existing_text:
        anchor_block["text"] = existing_text + "\n\n" + new_summary
    else:
        anchor_block["text"] = new_summary

    db.db_school_update_settings(session_id, stack_config=json.dumps(stack_cfg))

    await ws.send_json({
        "type": "block_summary_updated",
        "block_index": anchor_idx,
        "summary": new_summary,
        "stack_config": stack_cfg,
        "auto": True,
    })
    return stack_cfg


# ─── WebSocket Chat ───────────────────────────────────────────────
@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    """
    WebSocket protocol (persistent sessions + stack):
    → {"type": "start", "card_filename": "...", "persona_filename": "...", "stack_config": {...}}
    ← {"type": "session_started", "session_id": N, ...}
    ← {"type": "character_message", "content": "...", "analysis": null, "is_first_mes": true}
    → {"type": "resume", "session_id": N}
    ← {"type": "session_resumed", "session_id": N, "messages": [...], ...}
    → {"type": "user_message", "content": "..."}
    ← {"type": "character_message", "content": "...", "analysis": {...}}
    → {"type": "reset"}  (creates new session, old stays in DB)
    → {"type": "update_stack", "stack_config": {...}}
    → {"type": "summarize_block", "block_index": N}
    → {"type": "save_console_events", "events": [...]}
    → {"type": "delete_message", "message_id": N}
    → {"type": "get_report"}
    → {"type": "stop"}
    """
    await ws.accept()

    card = None
    card_filename = None
    persona = None
    persona_filename = None
    system_prompt = ""
    analysis_prompt = ""
    session_id = None
    current_gen_task = None
    school_response_style = "moderate"

    def _rebuild_prompts():
        """Rebuild system_prompt and analysis_prompt from card+persona."""
        nonlocal system_prompt, analysis_prompt
        _user_name = persona.get("name", "User") if persona else "User"
        system_prompt = engine.build_system_prompt(card, user_name=_user_name, response_style=school_response_style)
        analysis_prompt = engine.build_analysis_prompt(card)
        if persona:
            system_prompt = system_prompt + "\n\n" + engine.build_persona_context(persona)
            analysis_prompt = analysis_prompt + "\n\n" + engine.build_analysis_persona_context(persona)

    try:
        while True:
            data = await ws.receive_json()

            if data["type"] == "stop":
                if current_gen_task and not current_gen_task.done():
                    current_gen_task.cancel()
                    try:
                        await current_gen_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    try:
                        await ws.send_json({"type": "character_typing_stopped"})
                        await ws.send_json({"type": "analysis_typing_stopped"})
                        await ws.send_json({"type": "generation_stopped"})
                    except Exception:
                        pass
                continue

            elif data["type"] == "start":
                card_filename = data["card_filename"]
                persona_filename = data.get("persona_filename")
                try:
                    card = engine.load_card(card_filename)

                    if persona_filename:
                        try:
                            persona = engine.load_persona(persona_filename)
                        except Exception as pe:
                            await ws.send_json({"type": "error", "message": f"Persona error: {pe}"})
                            persona = None
                    else:
                        persona = None

                    _rebuild_prompts()

                    # Parse stack config from client or use default
                    stack_config_json = data.get("stack_config")
                    stack_config_str = json.dumps(stack_config_json) if stack_config_json else None

                    # Build session title: "CharName" or "CharName vs PersonaName"
                    _char_name = card.get("data", card).get("name", Path(card_filename).stem)
                    _persona_name = persona.get("name") if persona else None
                    _title = f"{_char_name} vs {_persona_name}" if _persona_name else _char_name

                    # Create school session
                    session_id = db.db_school_create_session(
                        card_filename, persona_filename, stack_config_str, title=_title
                    )

                    # Get the effective stack config
                    sess = db.db_school_get_session(session_id)
                    stack_cfg = db.get_stack_config(sess)

                    await ws.send_json({
                        "type": "session_started",
                        "session_id": session_id,
                        "card": card_filename,
                        "persona": persona_filename,
                        "stack_config": stack_cfg,
                        "lorebooks": [],
                    })

                    # Send first_mes
                    _user_name = persona.get("name", "User") if persona else "User"
                    first_mes = engine.get_first_mes(card, user_name=_user_name)
                    if first_mes:
                        msg_id = db.db_school_add_message(
                            session_id, "assistant", first_mes, is_first_mes=True
                        )
                        await ws.send_json({
                            "type": "character_message",
                            "content": first_mes,
                            "analysis": None,
                            "is_first_mes": True,
                            "message_id": msg_id,
                        })
                except Exception as e:
                    await ws.send_json({"type": "error", "message": str(e)})

            elif data["type"] == "resume":
                resume_id = data.get("session_id")
                sess = db.db_school_get_session(resume_id)
                if not sess:
                    await ws.send_json({"type": "error", "message": "Session not found"})
                    continue

                session_id = resume_id
                card_filename = sess["card_filename"]
                persona_filename = sess.get("persona_filename")

                try:
                    card = engine.load_card(card_filename)
                except Exception as e:
                    await ws.send_json({"type": "error", "message": f"Card load error: {e}"})
                    continue

                if persona_filename:
                    try:
                        persona = engine.load_persona(persona_filename)
                    except:
                        persona = None
                else:
                    persona = None

                _rebuild_prompts()

                # Restore response style from DB
                school_response_style = sess.get("response_style") or "moderate"
                _rebuild_prompts()  # rebuild with restored style

                messages = db.db_school_get_messages(session_id)
                stack_cfg = db.get_stack_config(sess)

                # Parse lorebooks
                school_lb = []
                if sess.get("lorebooks"):
                    try: school_lb = json.loads(sess["lorebooks"])
                    except: pass

                await ws.send_json({
                    "type": "session_resumed",
                    "session_id": session_id,
                    "card": card_filename,
                    "persona": persona_filename,
                    "stack_config": stack_cfg,
                    "lorebooks": school_lb,
                    "messages": [{"id": m["id"], "seq": m["seq"], "role": m["role"],
                                  "content": m["content"], "is_first_mes": m["is_first_mes"],
                                  "analysis": json.loads(m["analysis_json"]) if m["analysis_json"] else None}
                                 for m in messages],
                    "console_events": json.loads(sess.get("console_events", "[]")) if sess.get("console_events") else [],
                    "response_style": school_response_style,
                })

            elif data["type"] == "user_message":
                user_content = data["content"]
                client_msg_id = data.get("client_msg_id")
                user_msg_id = db.db_school_add_message(session_id, "user", user_content)

                await ws.send_json({"type": "user_message_stored", "message_id": user_msg_id, "client_msg_id": client_msg_id})

                async def _school_gen():
                    try:
                        # ── Auto-summary check (before LLM call) ──
                        updated_cfg = await check_school_auto_summary(session_id, ws)
                        if updated_cfg:
                            await ws.send_json({"type": "stack_updated", "stack_config": updated_cfg})

                        # Build LLM context from stack
                        sess = db.db_school_get_session(session_id)
                        stack_cfg = db.get_stack_config(sess)
                        all_msgs = db.db_school_get_messages(session_id)
                        # Stack builder expects 0-indexed list; school messages are seq-ordered
                        llm_messages, block_markers = db.build_llm_messages_from_stack(
                            stack_cfg, system_prompt, all_msgs,
                        )

                        # ── Lorebook injection ──
                        lb_filenames = []
                        if sess.get("lorebooks"):
                            try: lb_filenames = json.loads(sess["lorebooks"])
                            except: pass
                        if lb_filenames:
                            lorebooks_data = lorebook.load_lorebooks_for_session(lb_filenames)
                            lb_injection = lorebook.build_lorebook_injection(
                                lorebooks_data, all_msgs, scan_depth=10
                            )
                            if lb_injection:
                                # Inject into the system message (first in llm_messages)
                                if llm_messages and llm_messages[0]["role"] == "system":
                                    llm_messages[0]["content"] += "\n\n" + lb_injection
                                await ws.send_json({
                                    "type": "console_event", "event": "lorebook_injection",
                                    "content": lb_injection, "timestamp": engine._now_iso(),
                                })

                        # ── Notify frontend: character is responding ──
                        await ws.send_json({"type": "character_typing", "character_name": card.get("data", card).get("name", "Character")})

                        style_cap = engine.response_style_max_tokens(school_response_style, db.CHAT_MAX_TOKENS)
                        effective_max_tokens = min(db.CHAT_MAX_TOKENS, style_cap)

                        # ── Console: log the request ──
                        await ws.send_json({
                            "type": "console_event",
                            "event": "request",
                            "llm": "character",
                            "model": db.CHAT_MODEL,
                            "temperature": db.CHAT_TEMPERATURE,
                            "max_tokens": effective_max_tokens,
                            "messages": [{"role": m["role"], "content": m["content"]} for m in llm_messages],
                            "block_markers": block_markers,
                            "timestamp": engine._now_iso(),
                        })

                        kwargs = dict(
                            model=db.CHAT_MODEL,
                            messages=llm_messages,
                            temperature=db.CHAT_TEMPERATURE,
                            max_tokens=effective_max_tokens,
                            extra_body={"enable_thinking": db.CHAT_ENABLE_THINKING},
                        )
                        if db.CHAT_TOP_P is not None:
                            kwargs["top_p"] = db.CHAT_TOP_P
                        if db.CHAT_TOP_K is not None:
                            kwargs["top_k"] = db.CHAT_TOP_K
                        await ws.send_json({
                            "type": "console_event", "event": "request_kwargs", "llm": "character",
                            "label": "Character", "kwargs": kwargs, "timestamp": engine._now_iso(),
                        })
                        resp = await db.chat_client.chat.completions.create(**kwargs)
                        char_content = resp.choices[0].message.content
                        usage = resp.usage

                        await ws.send_json({"type": "character_typing_stopped"})

                        # ── Console: log the response ──
                        await ws.send_json({
                            "type": "console_event",
                            "event": "response",
                            "llm": "character",
                            "model": db.CHAT_MODEL,
                            "content": char_content,
                            "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                            "finish_reason": resp.choices[0].finish_reason,
                            "timestamp": engine._now_iso(),
                        })

                        # Store in school DB (no auto-analysis)
                        msg_id = db.db_school_add_message(
                            session_id, "assistant", char_content,
                            is_first_mes=False, analysis_json=None
                        )

                        await ws.send_json({
                            "type": "character_message",
                            "content": char_content,
                            "analysis": None,
                            "is_first_mes": False,
                            "message_id": msg_id,
                        })

                        # ── Auto-summary check (after character message) ──
                        updated_cfg = await check_school_auto_summary(session_id, ws)
                        if updated_cfg:
                            await ws.send_json({"type": "stack_updated", "stack_config": updated_cfg})

                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        await ws.send_json({"type": "character_typing_stopped"})
                        await ws.send_json({"type": "error", "message": f"LLM error: {e}"})

                current_gen_task = asyncio.create_task(_school_gen())
                continue

            elif data["type"] == "set_persona":
                persona_filename = data.get("persona_filename")
                if card:
                    if persona_filename:
                        try:
                            persona = engine.load_persona(persona_filename)
                        except Exception as pe:
                            await ws.send_json({"type": "error", "message": f"Persona error: {pe}"})
                            persona = None
                    else:
                        persona = None

                    _rebuild_prompts()

                    # Update current session's persona + title (no new session)
                    if session_id:
                        _char_name = card.get("data", card).get("name", Path(card_filename).stem)
                        _persona_name = persona.get("name") if persona else None
                        _title = f"{_char_name} vs {_persona_name}" if _persona_name else _char_name
                        db.db_school_update_session_meta(
                            session_id, persona_filename=persona_filename, title=_title
                        )

                    await ws.send_json({
                        "type": "persona_updated",
                        "persona": persona_filename,
                        "persona_name": persona.get("name") if persona else None,
                        "session_id": session_id,
                    })
                else:
                    await ws.send_json({"type": "error", "message": "No card loaded"})

            elif data["type"] == "reset":
                if card:
                    # Create new school session (old one stays in DB)
                    _char_name = card.get("data", card).get("name", Path(card_filename).stem)
                    _persona_name = persona.get("name") if persona else None
                    _title = f"{_char_name} vs {_persona_name}" if _persona_name else _char_name
                    session_id = db.db_school_create_session(
                        card_filename, persona_filename, None, title=_title
                    )

                    sess = db.db_school_get_session(session_id)
                    stack_cfg = db.get_stack_config(sess)

                    await ws.send_json({
                        "type": "reset_complete",
                        "session_id": session_id,
                        "stack_config": stack_cfg,
                    })

                    _user_name = persona.get("name", "User") if persona else "User"
                    first_mes = engine.get_first_mes(card, user_name=_user_name)
                    if first_mes:
                        msg_id = db.db_school_add_message(
                            session_id, "assistant", first_mes, is_first_mes=True
                        )
                        await ws.send_json({
                            "type": "character_message",
                            "content": first_mes,
                            "analysis": None,
                            "is_first_mes": True,
                            "message_id": msg_id,
                        })
                else:
                    await ws.send_json({"type": "error", "message": "No card loaded"})

            elif data["type"] == "delete_message":
                target_id = data.get("message_id")
                if target_id is not None and session_id is not None:
                    deleted = db.db_school_delete_message(session_id, target_id)
                    await ws.send_json({"type": "message_deleted", "message_id": target_id, "success": deleted})

            elif data["type"] == "regenerate_message":
                target_id = data.get("message_id")
                if target_id is not None and session_id is not None:
                    db.db_school_delete_message(session_id, target_id)
                    # Re-run generation with remaining messages (no new user message)
                    async def _school_regen():
                        try:
                            # ── Auto-summary check (before LLM call) ──
                            updated_cfg = await check_school_auto_summary(session_id, ws)
                            if updated_cfg:
                                await ws.send_json({"type": "stack_updated", "stack_config": updated_cfg})

                            sess = db.db_school_get_session(session_id)
                            stack_cfg = db.get_stack_config(sess)
                            all_msgs = db.db_school_get_messages(session_id)
                            llm_messages, block_markers = db.build_llm_messages_from_stack(
                                stack_cfg, system_prompt, all_msgs,
                            )

                            # ── Lorebook injection ──
                            lb_filenames = []
                            if sess.get("lorebooks"):
                                try: lb_filenames = json.loads(sess["lorebooks"])
                                except: pass
                            if lb_filenames:
                                lorebooks_data = lorebook.load_lorebooks_for_session(lb_filenames)
                                lb_injection = lorebook.build_lorebook_injection(
                                    lorebooks_data, all_msgs, scan_depth=10
                                )
                                if lb_injection:
                                    if llm_messages and llm_messages[0]["role"] == "system":
                                        llm_messages[0]["content"] += "\n\n" + lb_injection
                                    await ws.send_json({
                                        "type": "console_event", "event": "lorebook_injection",
                                        "content": lb_injection, "timestamp": engine._now_iso(),
                                    })

                            await ws.send_json({"type": "character_typing", "character_name": card.get("data", card).get("name", "Character")})

                            style_cap = engine.response_style_max_tokens(school_response_style, db.CHAT_MAX_TOKENS)
                            effective_max_tokens = min(db.CHAT_MAX_TOKENS, style_cap)

                            await ws.send_json({
                                "type": "console_event",
                                "event": "request",
                                "llm": "character",
                                "model": db.CHAT_MODEL,
                                "temperature": db.CHAT_TEMPERATURE,
                                "max_tokens": effective_max_tokens,
                                "messages": [{"role": m["role"], "content": m["content"]} for m in llm_messages],
                                "block_markers": block_markers,
                                "timestamp": engine._now_iso(),
                            })

                            kwargs = dict(
                                model=db.CHAT_MODEL,
                                messages=llm_messages,
                                temperature=db.CHAT_TEMPERATURE,
                                max_tokens=effective_max_tokens,
                                extra_body={"enable_thinking": db.CHAT_ENABLE_THINKING},
                            )
                            if db.CHAT_TOP_P is not None:
                                kwargs["top_p"] = db.CHAT_TOP_P
                            if db.CHAT_TOP_K is not None:
                                kwargs["top_k"] = db.CHAT_TOP_K
                            await ws.send_json({
                                "type": "console_event", "event": "request_kwargs", "llm": "character",
                                "label": "Character", "kwargs": kwargs, "timestamp": engine._now_iso(),
                            })
                            resp = await db.chat_client.chat.completions.create(**kwargs)
                            char_content = resp.choices[0].message.content
                            usage = resp.usage

                            await ws.send_json({"type": "character_typing_stopped"})

                            await ws.send_json({
                                "type": "console_event",
                                "event": "response",
                                "llm": "character",
                                "model": db.CHAT_MODEL,
                                "content": char_content,
                                "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                                "finish_reason": resp.choices[0].finish_reason,
                                "timestamp": engine._now_iso(),
                            })

                            msg_id = db.db_school_add_message(
                                session_id, "assistant", char_content,
                                is_first_mes=False, analysis_json=None
                            )

                            await ws.send_json({
                                "type": "character_message",
                                "content": char_content,
                                "analysis": None,
                                "is_first_mes": False,
                                "message_id": msg_id,
                            })

                            updated_cfg = await check_school_auto_summary(session_id, ws)
                            if updated_cfg:
                                await ws.send_json({"type": "stack_updated", "stack_config": updated_cfg})

                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            await ws.send_json({"type": "character_typing_stopped"})
                            await ws.send_json({"type": "error", "message": f"Regenerate error: {e}"})

                    current_gen_task = asyncio.create_task(_school_regen())
                    continue

            elif data["type"] == "update_stack":
                if session_id:
                    stack_cfg_data = data.get("stack_config")
                    if stack_cfg_data:
                        stack_cfg_str = json.dumps(stack_cfg_data)
                        db.db_school_update_settings(session_id, stack_config=stack_cfg_str)
                        await ws.send_json({"type": "stack_updated", "stack_config": stack_cfg_data})

            elif data["type"] == "summarize_block":
                if session_id:
                    block_index = data.get("block_index")
                    sess = db.db_school_get_session(session_id)
                    if sess:
                        stack_cfg = db.get_stack_config(sess)
                        blocks = stack_cfg.get("blocks", [])
                        if block_index is not None and 0 <= block_index < len(blocks):
                            block = blocks[block_index]
                            if block.get("type") in ("summary_chunk", "early_summary", "late_summary", "summary"):
                                all_msgs = db.db_school_get_messages(session_id)
                                start = block.get("start", 0)
                                end = block.get("end", len(all_msgs) - 1)
                                msgs_to_summarize = [
                                    {"role": m["role"], "content": m["content"]}
                                    for m in all_msgs if start <= m["seq"] <= end
                                ]
                                existing = block.get("text", "")
                                new_text = await engine.rp_summarize(
                                    session_id, msgs_to_summarize, existing, ws=ws
                                )
                                block["text"] = new_text
                                if block.get("type") in ("early_summary", "late_summary", "summary"):
                                    block["type"] = "summary_chunk"
                                db.db_school_update_settings(
                                    session_id, stack_config=json.dumps(stack_cfg)
                                )
                                await ws.send_json({
                                    "type": "block_summarized",
                                    "block_index": block_index,
                                    "text": new_text,
                                    "stack_config": stack_cfg,
                                })

            elif data["type"] == "save_console_events":
                if session_id:
                    events = data.get("events", [])
                    db.db_school_save_console_events(session_id, json.dumps(events))

            elif data["type"] == "set_response_style":
                school_response_style = data.get("style", "moderate")
                if card:
                    _rebuild_prompts()
                if session_id:
                    db.db_school_update_settings(session_id, response_style=school_response_style)
                await ws.send_json({"type": "response_style_updated", "style": school_response_style})

            elif data["type"] == "get_report":
                assistant_msgs = db.db_school_get_assistant_messages(session_id)
                responses = [m["content"] for m in assistant_msgs if not m["is_first_mes"]]
                analyses = [m["analysis"] for m in assistant_msgs if not m["is_first_mes"] and m["analysis"]]

                report = await engine.generate_report(
                    analysis_prompt, responses, analyses, card, persona, persona_filename, ws=ws
                )
                await ws.send_json({"type": "report", "content": report})

            elif data["type"] == "analyze":
                # Manual analysis triggered by "Analyze Character" button
                if not card:
                    await ws.send_json({"type": "error", "message": "No card loaded"})
                    continue
                if not session_id:
                    await ws.send_json({"type": "error", "message": "No active session"})
                    continue

                guidance = data.get("guidance", "").strip()

                # Get all non-first_mes character responses
                assistant_msgs = db.db_school_get_assistant_messages(session_id)
                responses = [m for m in assistant_msgs if not m["is_first_mes"]]
                if not responses:
                    await ws.send_json({"type": "error", "message": "No character responses to analyze yet. Chat with the character first."})
                    continue

                # Analyze the latest response with previous responses as context
                latest = responses[-1]
                prev_texts = [m["content"] for m in responses[:-1]]

                await ws.send_json({"type": "analysis_typing"})

                try:
                    analysis = await engine.analyze_response(
                        analysis_prompt, latest["content"], prev_texts, card,
                        guidance=guidance, ws=ws
                    )

                    await ws.send_json({"type": "analysis_typing_stopped"})

                    if analysis:
                        # Store on the latest character message for report use
                        analysis_str = json.dumps(analysis)
                        db.db_school_update_message_analysis(latest["id"], analysis_str)

                    await ws.send_json({
                        "type": "analysis_result",
                        "analysis": analysis,
                        "message_id": latest["id"],
                    })
                except Exception as e:
                    await ws.send_json({"type": "analysis_typing_stopped"})
                    await ws.send_json({"type": "error", "message": f"Analysis error: {e}"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except:
            pass

GEN_V2_SYSTEM = """You are a character card generator. You create rich, compelling character cards for roleplay.

Output ONLY valid JSON, no markdown fences, no commentary.

Generate a chara_card_v2 character card with this exact structure:
{
  "spec": "chara_card_v2",
  "spec_version": "2.0",
  "data": {
    "name": "",
    "description": "",
    "personality": "",
    "scenario": "",
    "first_mes": "",
    "mes_example": "",
    "creator_notes": "",
    "system_prompt": "",
    "post_history_instructions": "",
    "tags": [],
    "creator": "{creator}",
    "character_version": "",
    "alternate_greetings": [],
    "extensions": {}
  }
}

Field guidelines:
- **name**: The character's full name.
- **description**: Physical appearance, background, and core identity. 2-4 paragraphs. Use {{char}} for the character's name.
- **personality**: Traits, quirks, speech style, values, flaws. 2-3 paragraphs. Use {{char}}.
- **scenario**: The current situation / context where the roleplay begins. 1-2 paragraphs.
- **first_mes**: An opening message in character, 2-4 sentences. Use {{char}} and {{user}}. Write in third person narrative + dialogue.
- **mes_example**: 2-4 example dialogue exchanges showing the character's voice. Use {{char}} and {{user}}. Format as dialogue.
- **creator_notes**: Brief notes for the user about the character. 1-2 sentences.
- **system_prompt**: A brief system instruction for the AI playing this character (e.g. "Stay in character as {{char}}. Maintain their personality and speech patterns.").
- **tags**: 3-8 relevant tags (e.g. ["original", "female", "teacher", "shy"]).
- **alternate_greetings**: Leave empty array [] unless multiple greetings are clearly warranted.
- **extensions**: Leave empty object {}.

Be creative but coherent. Every field must be filled with quality content."""

GEN_V1_SYSTEM = """You are a character card generator. You create rich, compelling character cards for roleplay.

Output ONLY valid JSON, no markdown fences, no commentary.

Generate a V1 character card with this exact flat structure:
{
  "name": "",
  "description": "",
  "personality": "",
  "scenario": "",
  "first_mes": "",
  "mes_example": "",
  "creator": "{creator}",
  "character_version": "",
  "tags": []
}

Field guidelines:
- **name**: The character's full name.
- **description**: Physical appearance, background, and core identity. 2-4 paragraphs. Use {{char}} for the character's name.
- **personality**: Traits, quirks, speech style, values, flaws. 2-3 paragraphs. Use {{char}}.
- **scenario**: The current situation / context where the roleplay begins. 1-2 paragraphs.
- **first_mes**: An opening message in character, 2-4 sentences. Use {{char}} and {{user}}. Write in third person narrative + dialogue.
- **mes_example**: 2-4 example dialogue exchanges showing the character's voice. Use {{char}} and {{user}}. Format as dialogue.
- **tags**: 3-8 relevant tags (e.g. ["original", "female", "teacher", "shy"]).

Be creative but coherent. Every field must be filled with quality content."""


@router.post("/api/generate-character")
async def generate_character(req: Request):
    """Generate a character card using the chat LLM endpoint."""
    body = await req.json()
    concept = (body.get("concept") or "").strip()
    if not concept:
        return JSONResponse({"error": "Concept is required"}, status_code=400)

    version = body.get("version", 2)
    name_hint = (body.get("name") or "").strip()
    age_hint = (body.get("age") or "").strip()
    personality_hint = (body.get("personality") or "").strip()
    scenario_hint = (body.get("scenario") or "").strip()
    appearance_hint = (body.get("appearance") or "").strip()
    nsfw = body.get("nsfw", False)
    creator_name = (body.get("creator") or "").strip() or "Richard"

    # Build user prompt from hints
    parts = [f"Concept: {concept}"]
    if name_hint:
        parts.append(f"Name: {name_hint}")
    if age_hint:
        parts.append(f"Age: {age_hint}")
    if personality_hint:
        parts.append(f"Personality hints: {personality_hint}")
    if scenario_hint:
        parts.append(f"Scenario/setting hints: {scenario_hint}")
    if appearance_hint:
        parts.append(f"Appearance hints: {appearance_hint}")
    if nsfw:
        parts.append("Content rating: NSFW (adult content is acceptable in the card)")
    else:
        parts.append("Content rating: SFW (keep all content safe for work)")

    user_prompt = "\n".join(parts)
    system_prompt = (GEN_V2_SYSTEM if version == 2 else GEN_V1_SYSTEM).replace("{creator}", creator_name)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    console_events = [{
        "type": "console_event", "event": "request", "llm": "character",
        "model": db.CHAT_MODEL, "label": "Character Generation",
        "temperature": 0.9, "max_tokens": 4000,
        "messages": messages, "timestamp": engine._now_iso(),
    }]

    try:
        completion = await db.chat_client.chat.completions.create(
            model=db.CHAT_MODEL,
            messages=messages,
            temperature=0.9,
            max_tokens=4000,
            extra_body={"enable_thinking": db.CHAT_ENABLE_THINKING},
        )
        raw = completion.choices[0].message.content or ""

        usage = completion.usage
        console_events.append({
            "type": "console_event", "event": "response", "llm": "character",
            "model": db.CHAT_MODEL, "label": "Character Generation",
            "content": raw,
            "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
            "finish_reason": completion.choices[0].finish_reason, "timestamp": engine._now_iso(),
        })

        card = json.loads(repair_json(raw))

        # Normalize: ensure correct spec for V2
        if version == 2:
            if "data" not in card:
                # LLM put everything at top level — wrap it
                card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": card}
            card["spec"] = "chara_card_v2"
            card["spec_version"] = "2.0"
            card.setdefault("data", {}).setdefault("creator", creator_name)
        else:
            # V1: flatten if accidentally nested
            if "data" in card:
                card = card["data"]
            card.pop("spec", None)
            card.pop("spec_version", None)
            card.setdefault("creator", creator_name)

        return {"card": card, "version": version, "console_events": console_events, "usage": getattr(completion, "usage", None) and completion.usage.model_dump()}

    except Exception as e:
        console_events.append({
            "type": "console_event", "event": "error", "llm": "character",
            "model": db.CHAT_MODEL, "label": "Character Generation",
            "message": str(e), "timestamp": engine._now_iso(),
        })
        return JSONResponse({"error": str(e), "console_events": console_events}, status_code=500)


@router.post("/api/generate-character/save")
async def save_generated_character(req: Request):
    """Save a generated/previewed character card to the characters directory."""
    body = await req.json()
    card = body.get("card")
    if not card or not isinstance(card, dict):
        return JSONResponse({"error": "Invalid card data"}, status_code=400)

    # Extract name for filename
    version = engine.detect_card_version(card)
    data = card.get("data", card) if version >= 2 else card
    name = (data.get("name") or "unnamed").strip()
    if not name:
        name = "unnamed"

    # Sanitize filename
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name).strip('_').lower() or "unnamed"
    filename = f"{safe_name}.json"

    # Avoid overwriting existing files
    if db.CHARACTERS_DIR.exists():
        counter = 1
        while (db.CHARACTERS_DIR / filename).exists():
            filename = f"{safe_name}_{counter}.json"
            counter += 1

    engine.save_card(filename, card)
    return {"status": "ok", "filename": filename, "name": name}

@router.post("/api/school/sessions/{session_id}/move-to-rp")
async def api_school_move_to_rp(session_id: int):
    """Convert a school session to an RP session (one-way)."""
    rp_sid = db.db_school_to_rp(session_id)
    if rp_sid is None:
        return JSONResponse({"error": "Failed to convert session"}, status_code=400)
    return JSONResponse({"rp_session_id": rp_sid})
