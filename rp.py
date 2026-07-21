"""
Character School — RP mode routes + WebSocket + config API.
Split from server.py.
"""
import asyncio
import json
import os
import sqlite3
import uuid
from pathlib import Path
from fastapi import APIRouter, File as FastAPIFile, Request, WebSocket, WebSocketDisconnect, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
import db
import engine
import lorebook
import imagegen

router = APIRouter()

# ── Image upload settings ──
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB

# Maximum number of console events to send in session_resumed (restored on demand via REST)
RESUME_CONSOLE_LIMIT = 50


def _resume_console_events(sess):
    """Return the last N console events for session_resumed to keep the WS payload small."""
    raw = sess.get("console_events", "[]") if sess else "[]"
    try:
        events = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    if len(events) <= RESUME_CONSOLE_LIMIT:
        return events
    return events[-RESUME_CONSOLE_LIMIT:]

# ─── RP REST API ──────────────────────────────────────────────────
@router.get("/api/rp/sessions")
async def api_rp_list_sessions():
    return JSONResponse(db.db_rp_list_sessions())


@router.get("/api/rp/sessions/{session_id}")
async def api_rp_get_session(session_id: int):
    sess = db.db_rp_get_session(session_id)
    if not sess:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    messages = db.db_rp_get_messages(session_id)
    sess["messages"] = [{"id": m["id"], "role": m["role"], "speaker": m["speaker"], "content": m["content"], "image_path": m.get("image_path"), "image_prompt": m.get("image_prompt"), "seed": m.get("seed")} for m in messages]
    return JSONResponse(sess)


@router.get("/api/rp/images/{image_path}")
async def api_rp_get_image(image_path: str):
    """Serve an uploaded image from outside the static directory."""
    from fastapi.responses import FileResponse
    safe = Path(image_path).name
    file = db.UPLOAD_DIR / safe
    if not file.exists():
        return JSONResponse({"error": "Image not found"}, status_code=404)
    return FileResponse(file)


@router.get("/api/rp/sessions/{session_id}/console_events")
async def api_rp_console_events(session_id: int, limit: int = 0):
    """Return all (or last N) console events for a session — used by frontend to fetch
    full console history after session_resumed sends only the last 50."""
    sess = db.db_rp_get_session(session_id)
    if not sess:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    raw = sess.get("console_events", "[]") if sess else "[]"
    try:
        events = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        events = []
    if limit and limit > 0:
        events = events[-limit:]
    return JSONResponse({"events": events})


@router.post("/api/rp/upload")
async def api_rp_upload(file: UploadFile = FastAPIFile(...)):
    """Upload an image and return a safe relative path token."""
    if not file.content_type or file.content_type.lower() not in ALLOWED_IMAGE_TYPES:
        return JSONResponse({"error": "Unsupported file type"}, status_code=400)
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        return JSONResponse({"error": "File too large"}, status_code=400)
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}.get(file.content_type.lower(), ".bin")
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = db.UPLOAD_DIR / filename
    dest.write_bytes(contents)
    return JSONResponse({"image_path": filename})


@router.delete("/api/rp/sessions/{session_id}")
async def api_rp_delete_session(session_id: int):
    deleted = db.db_rp_delete_session(session_id)
    return JSONResponse({"deleted": deleted})


@router.post("/api/rp/sessions/{session_id}/fork")
async def api_rp_fork_session(session_id: int):
    result = db.db_rp_fork_session(session_id)
    if not result:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return JSONResponse(result)


# ─── RP WebSocket ─────────────────────────────────────────────────
@router.websocket("/ws/rp")
async def ws_rp(ws: WebSocket):
    await ws.accept()

    session_id = None
    cards = {}          # filename -> card dict
    character_names = {}  # filename -> display name
    character_order = []  # list of filenames
    persona = None
    persona_filename = None
    turn_routing = 'auto'
    response_style = 'moderate'
    pov = None
    inner_monologue = False
    current_gen_task = None  # background generation task (for stop support)
    _ws_state = [True]  # [is_alive] — mutable container to avoid nonlocal issues

    async def _safe_send(payload):
        """Send JSON only if the websocket is still alive."""
        if not _ws_state[0]:
            return
        try:
            await ws.send_json(payload)
        except Exception:
            _ws_state[0] = False

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
                        await _safe_send({"type": "character_typing_stopped"})
                        await _safe_send({"type": "generation_stopped"})
                    except Exception:
                        pass
                continue

            elif data["type"] == "start":
                char_filenames = data.get("characters", [])
                persona_filename = data.get("persona_filename")
                turn_routing = data.get("turn_routing", "auto")
                response_style = data.get("response_style", "moderate")
                pov = data.get("pov")
                inner_monologue = data.get("inner_monologue", False)

                cards = {}
                character_names = {}
                character_order = []
                char_list = []

                for fn in char_filenames[:4]:
                    try:
                        card = engine.load_card(fn)
                        d = card.get("data", card)
                        name = d.get("name", fn)
                        cards[fn] = card
                        character_names[fn] = name
                        character_order.append(fn)
                        char_list.append({"filename": fn, "name": name})
                    except Exception as e:
                        await _safe_send({"type": "error", "message": f"Failed to load {fn}: {e}"})

                if not cards:
                    await _safe_send({"type": "error", "message": "No valid characters loaded"})
                    continue

                if persona_filename:
                    try:
                        persona = engine.load_persona(persona_filename)
                    except:
                        persona = None
                else:
                    persona = None

                stack_config_json = data.get("stack_config")
                stack_config_str = json.dumps(stack_config_json) if stack_config_json else None

                session_id = db.db_rp_create_session(char_list, persona_filename, turn_routing, response_style, stack_config_str)

                # Build system prompt for token estimation in stack builder
                try:
                    _sys_prompt = engine.build_rp_system_prompt(
                        [cards[fn] for fn in character_order],
                        persona, turn_routing, response_style,
                        pov=pov, inner_monologue=inner_monologue,
                    )
                except Exception:
                    _sys_prompt = ""

                await _safe_send({
                    "type": "session_started", "session_id": session_id,
                    "characters": [{"filename": fn, "name": character_names[fn]} for fn in character_order],
                    "persona": persona_filename,
                    "turn_routing": turn_routing, "response_style": response_style,
                    "pov": pov, "inner_monologue": inner_monologue,
                    "stack_config": stack_config_json or db.DEFAULT_STACK_CONFIG,
                    "lorebooks": [],
                    "console_events": [],
                    "console_events_truncated": False,
                    "system_prompt": _sys_prompt,
                    "bg_image": None,  # new session — no per-session bg yet
                })

                # Send first_mes from all characters
                _rp_user_name = persona.get("name", "User") if persona else "User"
                for fn in character_order:
                    first_mes = engine.get_first_mes(cards[fn], user_name=_rp_user_name)
                    if first_mes:
                        msg_id = db.db_rp_add_message(session_id, "character", first_mes, character_names[fn])
                        await _safe_send({
                            "type": "character_message", "content": first_mes,
                            "character_filename": fn, "character_name": character_names[fn],
                            "is_first_mes": True, "message_id": msg_id,
                        })

            elif data["type"] == "resume":
                resume_id = data.get("session_id")
                sess = db.db_rp_get_session(resume_id)
                if not sess:
                    await _safe_send({"type": "error", "message": "Session not found"})
                    continue

                session_id = resume_id
                turn_routing = sess["turn_routing"]
                response_style = sess["response_style"]
                pov = sess.get("pov")
                inner_monologue = sess.get("inner_monologue", False)
                persona_filename = sess["persona_filename"]

                cards = {}
                character_names = {}
                character_order = []
                for c in sess["characters"]:
                    fn = c["card_filename"]
                    try:
                        card = engine.load_card(fn)
                        cards[fn] = card
                        character_names[fn] = c["char_name"]
                        character_order.append(fn)
                    except:
                        pass

                if persona_filename:
                    try:
                        persona = engine.load_persona(persona_filename)
                    except:
                        persona = None
                else:
                    persona = None

                messages = db.db_rp_get_messages(session_id)
                stack_cfg = db.get_stack_config(sess)

                # Build system prompt for token estimation in stack builder
                try:
                    _sys_prompt = engine.build_rp_system_prompt(
                        [cards[fn] for fn in character_order],
                        persona, turn_routing, response_style,
                        pov=pov, inner_monologue=inner_monologue,
                    )
                except Exception:
                    _sys_prompt = ""

                # Parse lorebooks
                session_lorebooks = []
                if sess.get("lorebooks"):
                    try: session_lorebooks = json.loads(sess["lorebooks"])
                    except: pass

                await _safe_send({
                    "type": "session_resumed", "session_id": session_id,
                    "characters": [{"filename": fn, "name": character_names[fn]} for fn in character_order],
                    "persona": persona_filename,
                    "turn_routing": turn_routing, "response_style": response_style,
                    "pov": pov, "inner_monologue": inner_monologue,
                    "stack_config": stack_cfg,
                    "lorebooks": session_lorebooks,
                    "messages": [{"id": m["id"], "role": m["role"], "speaker": m["speaker"], "content": m["content"], "persona_name": m.get("persona_name"), "image_path": m.get("image_path"), "image_prompt": m.get("image_prompt"), "seed": m.get("seed"), "swipes": m.get("swipes"), "active_swipe": m.get("active_swipe", 0)} for m in messages],
                    "console_events": [],
                    "console_events_truncated": len(json.loads(sess.get("console_events", "[]")) if sess.get("console_events") else []) > 0,
                    "system_prompt": _sys_prompt,
                    "bg_image": sess.get("bg_image"),
                })

            elif data["type"] == "user_message":
                user_content = data.get("content", "") or ""
                directed_to = data.get("directed_to")
                client_msg_id = data.get("client_msg_id")
                image_path = data.get("image_path")
                image_prompt = data.get("image_prompt")
                user_seed = data.get("seed")

                _pn = persona.get("name") if persona else None
                user_msg_id = db.db_rp_add_message(session_id, "user", user_content, persona_name=_pn, image_path=image_path, image_prompt=image_prompt, client_msg_id=client_msg_id, seed=user_seed)
                await _safe_send({"type": "user_message_stored", "message_id": user_msg_id, "client_msg_id": client_msg_id})

                # Image-only message: store + render, but don't trigger LLM generation
                if not user_content.strip() and image_path:
                    continue

                async def _rp_gen():
                    try:
                        # ── Auto-summary check (after user message, before LLM) ──
                        if _ws_state[0]:
                            await check_auto_summary(session_id, ws, send_fn=_safe_send)

                        # Get stack config
                        sess = db.db_rp_get_session(session_id)
                        stack_cfg = db.get_stack_config(sess)

                        # Build system prompt
                        system_prompt = engine.build_rp_system_prompt(
                            [cards[fn] for fn in character_order],
                            persona, turn_routing, response_style,
                            directed_character=character_names.get(directed_to) if directed_to else None,
                            pov=pov, inner_monologue=inner_monologue,
                        )

                        # Build LLM messages from stack config
                        all_msgs = db.db_rp_get_messages(session_id)
                        llm_messages, block_markers = db.build_llm_messages_from_stack(
                            stack_cfg, system_prompt, all_msgs,
                        )

                        # ── Lorebook injection (after stack build, into system message) ──
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
                                # Inject into the first system message
                                if llm_messages and llm_messages[0]["role"] == "system":
                                    llm_messages[0]["content"] += "\n\n" + lb_injection
                                await _safe_send({
                                    "type": "console_event", "event": "lorebook_injection",
                                    "content": lb_injection, "timestamp": engine._now_iso(),
                                })

                        # Typing indicator
                        if not _ws_state[0]: return
                        if directed_to and directed_to in character_names:
                            await _safe_send({"type": "character_typing", "character_filename": directed_to, "character_name": character_names[directed_to]})
                        else:
                            await _safe_send({"type": "character_typing", "character_filename": None, "character_name": None})

                        style_cap = engine.response_style_max_tokens(response_style, db.CHAT_MAX_TOKENS)
                        effective_max_tokens = min(db.CHAT_MAX_TOKENS, style_cap)

                        # Console: log request
                        await _safe_send({
                            "type": "console_event", "event": "request", "llm": "character",
                            "model": db.CHAT_MODEL, "temperature": db.CHAT_TEMPERATURE, "max_tokens": effective_max_tokens,
                            "messages": [{"role": m["role"], "content": m["content"]} for m in llm_messages],
                            "block_markers": block_markers,
                            "token_estimate": engine.estimate_tokens(llm_messages),
                            "timestamp": engine._now_iso(),
                        })

                        kwargs = dict(
                            model=db.CHAT_MODEL, messages=llm_messages,
                            temperature=db.CHAT_TEMPERATURE, max_tokens=effective_max_tokens,
                            extra_body={"enable_thinking": db.CHAT_ENABLE_THINKING},
                        )
                        if db.CHAT_TOP_P is not None:
                            kwargs["top_p"] = db.CHAT_TOP_P
                        if db.CHAT_TOP_K is not None:
                            kwargs["top_k"] = db.CHAT_TOP_K
                        await _safe_send({
                            "type": "console_event", "event": "request_kwargs", "llm": "character",
                            "label": "Character", "kwargs": kwargs, "timestamp": engine._now_iso(),
                        })
                        if not _ws_state[0]: return
                        resp = await db.chat_client.chat.completions.create(**kwargs)
                        raw_content = resp.choices[0].message.content
                        usage = resp.usage

                        if not _ws_state[0]: return
                        await _safe_send({
                            "type": "console_event", "event": "response", "llm": "character",
                            "model": db.CHAT_MODEL, "content": raw_content,
                            "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                            "finish_reason": resp.choices[0].finish_reason, "timestamp": engine._now_iso(),
                        })

                        parsed = engine.parse_rp_response(raw_content, character_names, character_order)

                        if directed_to and directed_to in character_names:
                            # Directed mode: force to the selected character
                            directed_name = character_names[directed_to]
                            if parsed:
                                # Take only the directed character's response
                                filtered = [p for p in parsed if p["filename"] == directed_to]
                                if filtered:
                                    parsed = filtered[:1]
                                else:
                                    import re as _re
                                    stripped = _re.sub(r'^\[[^\]]+\]:\s*', '', raw_content).strip()
                                    parsed = [{"filename": directed_to, "name": directed_name, "content": stripped}]
                            else:
                                parsed = [{"filename": directed_to, "name": directed_name, "content": raw_content.strip()}]
                        elif parsed:
                            # Auto mode: take only the first character the LLM chose
                            parsed = parsed[:1]
                        else:
                            # No [Name]: prefix — attribute to first character
                            fn = character_order[0] if character_order else None
                            name = character_names.get(fn, "Unknown")
                            parsed = [{"filename": fn, "name": name, "content": raw_content.strip()}]

                        await _safe_send({"type": "character_typing_stopped"})

                        if parsed:
                            for pr in parsed:
                                msg_id = db.db_rp_add_message(session_id, "character", pr["content"], pr["name"])
                                await _safe_send({
                                    "type": "character_message", "content": pr["content"],
                                    "character_filename": pr["filename"], "character_name": pr["name"],
                                    "is_first_mes": False, "message_id": msg_id,
                                })
                        # image_path is not generated by LLM; left for future image-prompt extensions

                        # ── Auto-summary check (after character messages added) ──
                        if _ws_state[0]:
                            await check_auto_summary(session_id, ws, send_fn=_safe_send)

                        # ── Auto image generation (Phase 3) ──
                        if _ws_state[0] and db.IMAGEGEN_AUTO_ENABLED:
                            async def _rp_auto_img_add(img_path, img_prompt=None, img_seed=None):
                                _pn = persona.get("name") if persona else None
                                msg_id_img = db.db_rp_add_message(session_id, "user", "", persona_name=_pn, image_path=img_path, image_prompt=img_prompt, seed=img_seed)
                                await _safe_send({
                                    "type": "character_message", "content": "",
                                    "character_filename": None, "character_name": None,
                                    "is_first_mes": False, "message_id": msg_id_img, "image_path": img_path, "image_prompt": img_prompt, "seed": img_seed,
                                })
                            all_msgs_for_img = db.db_rp_get_messages(session_id)
                            await imagegen.maybe_auto_generate_image(
                                session_id, all_msgs_for_img, _safe_send, mode="rp",
                                image_add_fn=_rp_auto_img_add,
                            )

                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        await _safe_send({"type": "character_typing_stopped"})
                        await _safe_send({"type": "error", "message": f"LLM error: {e}"})

                current_gen_task = asyncio.create_task(_rp_gen())
                continue  # back to main receive_json loop (handles stop)

            elif data["type"] == "delete_message":
                target_id = data.get("message_id")
                target_cid = data.get("client_msg_id")
                if (target_id is not None or target_cid) and session_id is not None:
                    deleted = db.db_rp_delete_message(session_id, target_id, target_cid)
                    await _safe_send({"type": "message_deleted", "message_id": target_id, "client_msg_id": target_cid, "success": deleted})

            elif data["type"] == "delete_single_message":
                target_id = data.get("message_id")
                target_cid = data.get("client_msg_id")
                if (target_id is not None or target_cid) and session_id is not None:
                    deleted = db.db_rp_delete_single_message(session_id, target_id, target_cid)
                    await _safe_send({"type": "message_deleted", "message_id": target_id, "client_msg_id": target_cid, "success": deleted, "mode": "single"})

            elif data["type"] == "regenerate_message":
                target_id = data.get("message_id")
                if target_id is not None and session_id is not None:
                    # Find the character filename from the deleted message before deleting
                    conn_msg = db.db_rp_get_messages(session_id)
                    deleted_msg = None
                    for m in conn_msg:
                        if m["id"] == target_id:
                            deleted_msg = m
                            break
                    directed_to = None
                    if deleted_msg and deleted_msg.get("role") == "character":
                        # Find the filename matching the character name
                        char_name = deleted_msg.get("speaker")
                        for fn, name in character_names.items():
                            if name == char_name:
                                directed_to = fn
                                break
                    db.db_rp_truncate_after(session_id, target_id)

                    async def _rp_regen():
                        try:
                            if _ws_state[0]:
                                await check_auto_summary(session_id, ws, send_fn=_safe_send)

                            sess = db.db_rp_get_session(session_id)
                            stack_cfg = db.get_stack_config(sess)

                            system_prompt = engine.build_rp_system_prompt(
                                [cards[fn] for fn in character_order],
                                persona, turn_routing, response_style,
                                directed_character=character_names.get(directed_to) if directed_to else None,
                                pov=pov, inner_monologue=inner_monologue,
                            )

                            all_msgs = db.db_rp_get_messages(session_id)
                            # Exclude the target message from LLM context (generate fresh response)
                            all_msgs = [m for m in all_msgs if m["id"] != target_id]
                            llm_messages, block_markers = db.build_llm_messages_from_stack(
                                stack_cfg, system_prompt, all_msgs,
                            )

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
                                    await _safe_send({
                                        "type": "console_event", "event": "lorebook_injection",
                                        "content": lb_injection, "timestamp": engine._now_iso(),
                                    })

                            if not _ws_state[0]: return
                            if directed_to and directed_to in character_names:
                                await _safe_send({"type": "character_typing", "character_filename": directed_to, "character_name": character_names[directed_to]})
                            else:
                                await _safe_send({"type": "character_typing", "character_filename": None, "character_name": None})

                            style_cap = engine.response_style_max_tokens(response_style, db.CHAT_MAX_TOKENS)
                            effective_max_tokens = min(db.CHAT_MAX_TOKENS, style_cap)

                            await _safe_send({
                                "type": "console_event", "event": "request", "llm": "character",
                                "model": db.CHAT_MODEL, "temperature": db.CHAT_TEMPERATURE, "max_tokens": effective_max_tokens,
                                "messages": [{"role": m["role"], "content": m["content"]} for m in llm_messages],
                                "block_markers": block_markers,
                                "token_estimate": engine.estimate_tokens(llm_messages),
                                "timestamp": engine._now_iso(),
                            })

                            kwargs = dict(
                                model=db.CHAT_MODEL, messages=llm_messages,
                                temperature=db.CHAT_TEMPERATURE, max_tokens=effective_max_tokens,
                                extra_body={"enable_thinking": db.CHAT_ENABLE_THINKING},
                            )
                            if db.CHAT_TOP_P is not None:
                                kwargs["top_p"] = db.CHAT_TOP_P
                            if db.CHAT_TOP_K is not None:
                                kwargs["top_k"] = db.CHAT_TOP_K
                            await _safe_send({
                                "type": "console_event", "event": "request_kwargs", "llm": "character",
                                "label": "Character", "kwargs": kwargs, "timestamp": engine._now_iso(),
                            })
                            if not _ws_state[0]: return
                            resp = await db.chat_client.chat.completions.create(**kwargs)
                            raw_content = resp.choices[0].message.content
                            usage = resp.usage

                            if not _ws_state[0]: return
                            await _safe_send({
                                "type": "console_event", "event": "response", "llm": "character",
                                "model": db.CHAT_MODEL, "content": raw_content,
                                "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                                "finish_reason": resp.choices[0].finish_reason, "timestamp": engine._now_iso(),
                            })

                            parsed = engine.parse_rp_response(raw_content, character_names, character_order)

                            if directed_to and directed_to in character_names:
                                directed_name = character_names[directed_to]
                                if parsed:
                                    filtered = [p for p in parsed if p["filename"] == directed_to]
                                    if filtered:
                                        parsed = filtered[:1]
                                    else:
                                        import re as _re
                                        stripped = _re.sub(r'^\[[^\]]+\]:\s*', '', raw_content).strip()
                                        parsed = [{"filename": directed_to, "name": directed_name, "content": stripped}]
                                else:
                                    parsed = [{"filename": directed_to, "name": directed_name, "content": raw_content.strip()}]
                            elif parsed:
                                parsed = parsed[:1]
                            else:
                                fn = character_order[0] if character_order else None
                                name = character_names.get(fn, "Unknown")
                                parsed = [{"filename": fn, "name": name, "content": raw_content.strip()}]

                            await _safe_send({"type": "character_typing_stopped"})

                            if parsed:
                                for pr in parsed:
                                    swipe_info = db.db_rp_add_swipe(session_id, target_id, pr["content"])
                                    await _safe_send({
                                        "type": "character_message", "content": pr["content"],
                                        "character_filename": pr["filename"], "character_name": pr["name"],
                                        "is_first_mes": False, "message_id": target_id,
                                        "regenerated": True,
                                        "swipe_index": swipe_info.get("active_swipe", 0),
                                        "swipe_count": swipe_info.get("swipe_count", 1),
                                    })

                            if _ws_state[0]:
                                await check_auto_summary(session_id, ws, send_fn=_safe_send)

                            # ── Auto image generation (Phase 3, regen path) ──
                            if _ws_state[0] and db.IMAGEGEN_AUTO_ENABLED:
                                async def _rp_regen_img_add(img_path, img_prompt=None, img_seed=None):
                                    _pn = persona.get("name") if persona else None
                                    msg_id_img = db.db_rp_add_message(session_id, "user", "", persona_name=_pn, image_path=img_path, image_prompt=img_prompt, seed=img_seed)
                                    await _safe_send({
                                        "type": "character_message", "content": "",
                                        "character_filename": None, "character_name": None,
                                        "is_first_mes": False, "message_id": msg_id_img, "image_path": img_path, "image_prompt": img_prompt, "seed": img_seed,
                                    })
                                _all_msgs_img = db.db_rp_get_messages(session_id)
                                await imagegen.maybe_auto_generate_image(
                                    session_id, _all_msgs_img, _safe_send, mode="rp",
                                    image_add_fn=_rp_regen_img_add,
                                )

                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            await _safe_send({"type": "character_typing_stopped"})
                            await _safe_send({"type": "error", "message": f"Regenerate error: {e}"})

                    current_gen_task = asyncio.create_task(_rp_regen())
                    continue

            elif data["type"] == "swipe_message":
                target_id = data.get("message_id")
                swipe_idx = data.get("swipe_index")
                if target_id is not None and session_id is not None and swipe_idx is not None:
                    result = db.db_rp_switch_swipe(session_id, target_id, swipe_idx)
                    if result:
                        await _safe_send({
                            "type": "swipe_switched", "message_id": target_id,
                            "content": result["content"],
                            "active_swipe": result["active_swipe"],
                            "swipe_count": result["swipe_count"],
                        })

            elif data["type"] == "continue_message":
                if session_id is not None:
                    # Find the last character message to determine who should continue
                    all_msgs = db.db_rp_get_messages(session_id)
                    directed_to = None
                    for m in reversed(all_msgs):
                        if m.get("role") == "character":
                            char_name = m.get("speaker")
                            for fn, name in character_names.items():
                                if name == char_name:
                                    directed_to = fn
                                    break
                            break

                    async def _rp_cont():
                        try:
                            if _ws_state[0]:
                                await check_auto_summary(session_id, ws, send_fn=_safe_send)

                            sess = db.db_rp_get_session(session_id)
                            stack_cfg = db.get_stack_config(sess)

                            system_prompt = engine.build_rp_system_prompt(
                                [cards[fn] for fn in character_order],
                                persona, turn_routing, response_style,
                                directed_character=character_names.get(directed_to) if directed_to else None,
                                pov=pov, inner_monologue=inner_monologue,
                            )

                            all_msgs = db.db_rp_get_messages(session_id)
                            llm_messages, block_markers = db.build_llm_messages_from_stack(
                                stack_cfg, system_prompt, all_msgs,
                            )

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
                                    await _safe_send({
                                        "type": "console_event", "event": "lorebook_injection",
                                        "content": lb_injection, "timestamp": engine._now_iso(),
                                    })

                            if not _ws_state[0]: return
                            if directed_to and directed_to in character_names:
                                await _safe_send({"type": "character_typing", "character_filename": directed_to, "character_name": character_names[directed_to]})
                            else:
                                await _safe_send({"type": "character_typing", "character_filename": None, "character_name": None})

                            style_cap = engine.response_style_max_tokens(response_style, db.CHAT_MAX_TOKENS)
                            effective_max_tokens = min(db.CHAT_MAX_TOKENS, style_cap)

                            await _safe_send({
                                "type": "console_event", "event": "request", "llm": "character",
                                "model": db.CHAT_MODEL, "temperature": db.CHAT_TEMPERATURE, "max_tokens": effective_max_tokens,
                                "messages": [{"role": m["role"], "content": m["content"]} for m in llm_messages],
                                "block_markers": block_markers,
                                "tokenEstimate": engine.estimate_tokens(llm_messages),
                                "timestamp": engine._now_iso(),
                            })

                            kwargs = dict(
                                model=db.CHAT_MODEL, messages=llm_messages,
                                temperature=db.CHAT_TEMPERATURE, max_tokens=effective_max_tokens,
                                extra_body={"enable_thinking": db.CHAT_ENABLE_THINKING},
                            )
                            if db.CHAT_TOP_P is not None:
                                kwargs["top_p"] = db.CHAT_TOP_P
                            if db.CHAT_TOP_K is not None:
                                kwargs["top_k"] = db.CHAT_TOP_K
                            await _safe_send({
                                "type": "console_event", "event": "request_kwargs", "llm": "character",
                                "label": "Character", "kwargs": kwargs, "timestamp": engine._now_iso(),
                            })
                            if not _ws_state[0]: return
                            resp = await db.chat_client.chat.completions.create(**kwargs)
                            raw_content = resp.choices[0].message.content
                            usage = resp.usage

                            if not _ws_state[0]: return
                            await _safe_send({
                                "type": "console_event", "event": "response", "llm": "character",
                                "model": db.CHAT_MODEL, "content": raw_content,
                                "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                                "finish_reason": resp.choices[0].finish_reason, "timestamp": engine._now_iso(),
                            })

                            parsed = engine.parse_rp_response(raw_content, character_names, character_order)

                            if directed_to and directed_to in character_names:
                                directed_name = character_names[directed_to]
                                if parsed:
                                    filtered = [p for p in parsed if p["filename"] == directed_to]
                                    if filtered:
                                        parsed = filtered[:1]
                                    else:
                                        import re as _re
                                        stripped = _re.sub(r'^\[[^\]]+\]:\s*', '', raw_content).strip()
                                        parsed = [{"filename": directed_to, "name": directed_name, "content": stripped}]
                                else:
                                    parsed = [{"filename": directed_to, "name": directed_name, "content": raw_content.strip()}]
                            elif parsed:
                                parsed = parsed[:1]
                            else:
                                fn = character_order[0] if character_order else None
                                name = character_names.get(fn, "Unknown")
                                parsed = [{"filename": fn, "name": name, "content": raw_content.strip()}]

                            await _safe_send({"type": "character_typing_stopped"})

                            if parsed:
                                for pr in parsed:
                                    msg_id = db.db_rp_add_message(session_id, "character", pr["content"], pr["name"])
                                    await _safe_send({
                                        "type": "character_message", "content": pr["content"],
                                        "character_filename": pr["filename"], "character_name": pr["name"],
                                        "is_first_mes": False, "message_id": msg_id,
                                    })

                            if _ws_state[0]:
                                await check_auto_summary(session_id, ws, send_fn=_safe_send)

                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            await _safe_send({"type": "character_typing_stopped"})
                            await _safe_send({"type": "error", "message": f"Continue error: {e}"})

                    current_gen_task = asyncio.create_task(_rp_cont())
                    continue

            elif data["type"] == "update_settings":
                if session_id:
                    turn_routing = data.get("turn_routing", turn_routing)
                    response_style = data.get("response_style", response_style)
                    pov = data.get("pov", pov)
                    inner_monologue = data.get("inner_monologue", inner_monologue)
                    db.db_rp_update_settings(session_id, turn_routing, response_style, pov=pov, inner_monologue=inner_monologue)
                    await _safe_send({"type": "settings_updated", "turn_routing": turn_routing, "response_style": response_style, "pov": pov, "inner_monologue": inner_monologue, "system_prompt": engine.build_rp_system_prompt([cards[fn] for fn in character_order], persona, turn_routing, response_style, pov=pov, inner_monologue=inner_monologue) if character_order else ""})

            elif data["type"] == "set_bg_image":
                if session_id:
                    bg_url = data.get("url")  # may be None to clear
                    db.db_rp_update_settings(session_id, bg_image=bg_url if bg_url else "")
                    await _safe_send({"type": "bg_image_updated", "bg_image": bg_url or None})

            elif data["type"] == "update_stack":
                if session_id:
                    stack_cfg_data = data.get("stack_config")
                    if stack_cfg_data:
                        stack_cfg_str = json.dumps(stack_cfg_data)
                        db.db_rp_update_settings(session_id, stack_config=stack_cfg_str)
                        await _safe_send({"type": "stack_updated", "stack_config": stack_cfg_data})

            elif data["type"] == "summarize_block":
                if session_id:
                    block_index = data.get("block_index")
                    sess = db.db_rp_get_session(session_id)
                    stack_cfg = db.get_stack_config(sess)
                    blocks = stack_cfg.get("blocks", [])
                    if block_index is not None and 0 <= block_index < len(blocks):
                        block = blocks[block_index]
                        # Accept summary_chunk and legacy types
                        if block.get("type") in ("summary_chunk", "early_summary", "late_summary", "summary"):
                            start_idx = block.get("start", 0)
                            end_idx = block.get("end", 0)
                            all_msgs = db.db_rp_get_messages(session_id)
                            start_idx = max(0, min(start_idx, len(all_msgs)))
                            end_idx = max(start_idx, min(end_idx, len(all_msgs)))
                            to_summarize = all_msgs[start_idx:end_idx]
                            existing = block.get("text", "")
                            if to_summarize:
                                await _safe_send({"type": "block_summarizing", "block_index": block_index})
                                new_summary = await engine.rp_summarize(session_id, to_summarize, existing, ws=ws, send_fn=_safe_send)
                                block["text"] = new_summary
                                # Normalize type
                                if block.get("type") in ("early_summary", "late_summary", "summary"):
                                    block["type"] = "summary_chunk"
                                stack_cfg["blocks"] = blocks
                                db.db_rp_update_settings(session_id, stack_config=json.dumps(stack_cfg))
                                await _safe_send({
                                    "type": "block_summary_updated",
                                    "block_index": block_index,
                                    "summary": new_summary,
                                    "stack_config": stack_cfg,
                                })
                            else:
                                await _safe_send({"type": "error", "message": "No messages in range to summarize."})

            elif data["type"] == "save_console_events":
                if session_id:
                    events = data.get("events", [])
                    db.db_rp_save_console_events(session_id, json.dumps(events))
                    await _safe_send({"type": "console_events_saved"})

            elif data["type"] == "set_persona":
                persona_filename = data.get("persona_filename")
                if persona_filename:
                    try:
                        persona = engine.load_persona(persona_filename)
                    except:
                        persona = None
                else:
                    persona = None
                    persona_filename = None
                if session_id:
                    db.db_rp_set_persona(session_id, persona_filename)
                await _safe_send({"type": "persona_changed", "persona": persona_filename})

            elif data["type"] == "add_character":
                if not session_id:
                    await _safe_send({"type": "error", "message": "No active session"})
                    continue
                fn = data.get("filename")
                if not fn:
                    await _safe_send({"type": "error", "message": "No filename provided"})
                    continue
                if fn in character_order:
                    await _safe_send({"type": "error", "message": "Character already in session"})
                    continue
                if len(character_order) >= 4:
                    await _safe_send({"type": "error", "message": "Maximum 4 characters per session"})
                    continue
                try:
                    card = engine.load_card(fn)
                    d = card.get("data", card)
                    name = d.get("name", fn)
                    cards[fn] = card
                    character_names[fn] = name
                    character_order.append(fn)
                    db.db_rp_add_character(session_id, fn, name)
                    # Send first_mes from the new character
                    _rp_user_name = persona.get("name", "User") if persona else "User"
                    first_mes = engine.get_first_mes(cards[fn], user_name=_rp_user_name)
                    if first_mes:
                        msg_id = db.db_rp_add_message(session_id, "character", first_mes, name)
                        await _safe_send({
                            "type": "character_message", "content": first_mes,
                            "character_filename": fn, "character_name": name,
                            "is_first_mes": True, "message_id": msg_id,
                        })
                    await _safe_send({
                        "type": "character_added",
                        "filename": fn, "name": name,
                        "characters": [{"filename": f, "name": character_names[f]} for f in character_order],
                    })
                except Exception as e:
                    await _safe_send({"type": "error", "message": f"Failed to add character: {e}"})

            elif data["type"] == "remove_character":
                if not session_id:
                    await _safe_send({"type": "error", "message": "No active session"})
                    continue
                fn = data.get("filename")
                if not fn or fn not in character_order:
                    await _safe_send({"type": "error", "message": "Character not in session"})
                    continue
                if len(character_order) <= 1:
                    await _safe_send({"type": "error", "message": "Cannot remove last character"})
                    continue
                removed = db.db_rp_remove_character(session_id, fn)
                if not removed:
                    await _safe_send({"type": "error", "message": "Failed to remove character"})
                    continue
                del cards[fn]
                del character_names[fn]
                character_order.remove(fn)
                await _safe_send({
                    "type": "character_removed",
                    "filename": fn,
                    "characters": [{"filename": f, "name": character_names[f]} for f in character_order],
                })
                # Reload session list to update title in sidebar
                await _safe_send({"type": "reload_sessions"})

    except WebSocketDisconnect:
        print("[RP WS] WebSocketDisconnect", flush=True)
        pass
    except Exception as e:
        import traceback
        print(f"[RP WS] EXCEPTION: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        try:
            await _safe_send({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        _ws_state[0] = False
        print("[RP WS] finally block - cleaning up", flush=True)
        if current_gen_task and not current_gen_task.done():
            current_gen_task.cancel()
            try:
                await current_gen_task
            except (asyncio.CancelledError, Exception):
                pass


@router.get("/api/config")
async def get_config():
    """Return current config with API keys masked."""
    _c = db.load_config()
    def mask(key):
        if not key or len(key) <= 8:
            return key
        return key[:4] + "…" + key[-4:]
    return {
        "version": db.APP_VERSION,
        "server": _c.get("server", {"host": "0.0.0.0", "port": 7862}),
        "chat": {
            "base_url": _c.get("chat", {}).get("base_url", ""),
            "api_key": mask(_c.get("chat", {}).get("api_key")),
            "model": _c.get("chat", {}).get("model", ""),
            "temperature": _c.get("chat", {}).get("temperature", 0.8),
            "max_tokens": _c.get("chat", {}).get("max_tokens", 2000),
            "top_p": _c.get("chat", {}).get("top_p", 1.0),
            "top_k": _c.get("chat", {}).get("top_k", 40),
            "enable_thinking": _c.get("chat", {}).get("enable_thinking", False),
        },
        "analysis": {
            "base_url": _c.get("analysis", {}).get("base_url", ""),
            "api_key": mask(_c.get("analysis", {}).get("api_key")),
            "model": _c.get("analysis", {}).get("model", ""),
            "temperature": _c.get("analysis", {}).get("temperature", 0.1),
            "max_tokens": _c.get("analysis", {}).get("max_tokens", 1500),
            "top_p": _c.get("analysis", {}).get("top_p", 1.0),
            "top_k": _c.get("analysis", {}).get("top_k", 40),
        },
        "summary": {
            "base_url": _c.get("summary", {}).get("base_url", ""),
            "api_key": mask(_c.get("summary", {}).get("api_key")),
            "model": _c.get("summary", {}).get("model", ""),
            "temperature": _c.get("summary", {}).get("temperature", 0.3),
            "max_tokens": _c.get("summary", {}).get("max_tokens", 1000),
            "top_p": _c.get("summary", {}).get("top_p", 1.0),
            "top_k": _c.get("summary", {}).get("top_k", 40),
        },
        "paths": _c.get("paths", {"characters_dir": None, "personas_dir": None}),
        "imagegen": {
            "enabled": _c.get("imagegen", {}).get("enabled", False),
            "base_url": _c.get("imagegen", {}).get("base_url", "http://127.0.0.1:8188"),
            "negative_prompt": _c.get("imagegen", {}).get("negative_prompt", ""),
            "width": _c.get("imagegen", {}).get("width", 512),
            "height": _c.get("imagegen", {}).get("height", 768),
            "steps": _c.get("imagegen", {}).get("steps", 20),
            "cfg_scale": _c.get("imagegen", {}).get("cfg_scale", 7.0),
            "sampler": _c.get("imagegen", {}).get("sampler", "euler"),
            "scheduler": _c.get("imagegen", {}).get("scheduler", "normal"),
            "workflow": _c.get("imagegen", {}).get("workflow", None),
        },
        "imagegen_auto": {
            "enabled": _c.get("imagegen_auto", {}).get("enabled", False),
            "base_url": _c.get("imagegen_auto", {}).get("base_url", "https://openrouter.ai/api/v1"),
            "api_key": mask(_c.get("imagegen_auto", {}).get("api_key")),
            "model": _c.get("imagegen_auto", {}).get("model", "deepseek/deepseek-v4-flash"),
            "temperature": _c.get("imagegen_auto", {}).get("temperature", 0.5),
            "max_tokens": _c.get("imagegen_auto", {}).get("max_tokens", 500),
            "interval": _c.get("imagegen_auto", {}).get("interval", 5),
            "negative_prompt": _c.get("imagegen_auto", {}).get("negative_prompt", ""),
        },
        "presets": {
            name: {k: (mask(v) if k == "api_key" else v) for k, v in p.items()}
            for name, p in _c.get("presets", {}).items()
        },
        "workflows": _c.get("workflows", {}),
    }


@router.post("/api/config")
async def update_config(req: Request):
    """Update config.jsonc and reload. Only writes provided fields.
    If api_key contains '…' (masked), keep the existing key."""
    body = await req.json()
    current = db.load_config()

    # Deep-merge: update only provided fields
    for section in ("server", "chat", "analysis", "summary", "paths", "imagegen", "imagegen_auto"):
        if section not in body:
            continue
        if section not in current:
            current[section] = {}
        for k, v in body[section].items():
            if k == "api_key" and v and "…" in v:
                # Masked key from GET — keep existing
                continue
            if k in ("personas_dir", "characters_dir") and (v == "" or v is None):
                current[section][k] = None
            elif k in ("top_p", "top_k") and v == "":
                current[section][k] = None
            else:
                current[section][k] = v

    # Presets — replace entirely if provided
    if "presets" in body:
        current["presets"] = body["presets"]

    # Workflows — replace entirely if provided
    if "workflows" in body:
        current["workflows"] = body["workflows"]

    db.save_config(current)
    db.reload_config()
    return {"status": "ok", "message": "Config saved and reloaded. Restart server if port/host changed."}


@router.delete("/api/presets/{name}")
async def delete_preset(name: str):
    """Delete a named preset from config."""
    current = db.load_config()
    presets = current.get("presets", {})
    if name not in presets:
        return {"status": "error", "message": f"Preset '{name}' not found."}
    del presets[name]
    current["presets"] = presets
    db.save_config(current)
    db.reload_config()
    return {"status": "ok", "message": f"Preset '{name}' deleted."}


@router.get("/api/presets")
async def get_presets():
    """Return presets with full (unmasked) api keys for the preset manager."""
    _c = db.load_config()
    return _c.get("presets", {})


@router.get("/api/workflows")
async def get_workflows():
    """Return all saved ComfyUI workflow presets."""
    _c = db.load_config()
    return _c.get("workflows", {})


@router.post("/api/workflows")
async def save_workflow(req: Request):
    """Save or update a named ComfyUI workflow.
    Body: { "name": "...", "workflow": { ... } | null }"""
    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        return {"status": "error", "message": "Workflow name cannot be empty."}
    current = db.load_config()
    workflows = current.get("workflows", {})
    workflows[name] = body.get("workflow")  # null = empty/default
    current["workflows"] = workflows
    db.save_config(current)
    db.reload_config()
    return {"status": "ok", "message": f"Workflow '{name}' saved."}


@router.delete("/api/workflows/{name}")
async def delete_workflow(name: str):
    """Delete a named ComfyUI workflow from config."""
    current = db.load_config()
    workflows = current.get("workflows", {})
    if name not in workflows:
        return {"status": "error", "message": f"Workflow '{name}' not found."}
    del workflows[name]
    current["workflows"] = workflows
    db.save_config(current)
    db.reload_config()
    return {"status": "ok", "message": f"Workflow '{name}' deleted."}


@router.post("/api/database/reset")
async def reset_database():
    """Wipe all data from all tables, then re-init."""
    try:
        conn = sqlite3.connect(str(db.DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM rp_messages")
        conn.execute("DELETE FROM rp_characters")
        conn.execute("DELETE FROM rp_sessions")
        # Reset auto-increment counters
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('sessions','messages','rp_sessions','rp_characters','rp_messages')")
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
        conn.close()
        return {"status": "ok", "message": "Database cleared and vacuumed."}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

async def check_auto_summary(session_id, ws, send_fn=None):
    """Auto-summarize by growing the anchor block.
    Finds the LAST summary_chunk with auto=true (the "anchor").
    When enough new messages accumulate past its end, summarizes ONLY
    the new batch (tail_n messages) and APPENDS the summary to the
    anchor's text. The anchor's end boundary expands — no new chunks.
    Returns updated stack_cfg if summarization happened, else None."""
    _send = send_fn or (lambda payload: ws.send_json(payload))
    sess = db.db_rp_get_session(session_id)
    stack_cfg = db.get_stack_config(sess)
    blocks = stack_cfg.get("blocks", [])

    # Normalize legacy types to summary_chunk
    for blk in blocks:
        if blk.get("type") in ("early_summary", "late_summary", "summary"):
            blk["type"] = "summary_chunk"

    # Find the last summary_chunk with auto enabled (the anchor).
    # Also find the last enabled tail block to get tail_n.
    anchor_idx = None
    anchor_block = None
    tail_n = 0
    for i, blk in enumerate(blocks):
        if blk.get("type") == "summary_chunk" and blk.get("enabled", True) and blk.get("auto"):
            anchor_idx = i
            anchor_block = blk
        elif blk.get("type") == "tail" and blk.get("enabled", True):
            tail_n = blk.get("n", 3)

    if anchor_block is None or tail_n <= 0:
        return None

    all_msgs = db.db_rp_get_messages(session_id)
    total_msgs = len(all_msgs)
    anchor_end = anchor_block.get("end", 0)

    # Enough unsummarized messages past the anchor?
    if total_msgs - anchor_end < tail_n:
        return None

    # Summarize ONLY the next batch (bounded input — never re-summarizes old ranges)
    batch_start = anchor_end
    batch_end = min(anchor_end + tail_n, total_msgs)
    batch_msgs = all_msgs[batch_start:batch_end]
    if not batch_msgs:
        return None

    await _send({"type": "block_summarizing", "block_index": anchor_idx, "auto": True})
    new_summary = await engine.rp_summarize(session_id, batch_msgs, "", ws=ws, send_fn=_send)

    # Grow the anchor: expand end boundary + append summary text
    anchor_block["end"] = batch_end
    existing_text = anchor_block.get("text", "")
    if existing_text:
        anchor_block["text"] = existing_text + "\n\n" + new_summary
    else:
        anchor_block["text"] = new_summary

    stack_cfg["blocks"] = blocks
    db.db_rp_update_settings(session_id, stack_config=json.dumps(stack_cfg))

    await _send({
        "type": "block_summary_updated",
        "block_index": anchor_idx,
        "summary": new_summary,
        "stack_config": stack_cfg,
        "auto": True,
    })

    return stack_cfg


# ─── Lorebook REST API ────────────────────────────────────────────

@router.get("/api/lorebooks")
async def api_list_lorebooks():
    """List all lorebooks."""
    return JSONResponse(lorebook.list_lorebooks())

@router.post("/api/lorebooks")
async def api_create_lorebook(req: Request):
    """Create a new lorebook."""
    body = await req.json()
    name = body.get("name", "Untitled")
    description = body.get("description", "")
    result = lorebook.create_lorebook(name, description)
    return JSONResponse(result)

@router.get("/api/lorebooks/{filename}")
async def api_get_lorebook(filename: str):
    """Get a single lorebook with all entries."""
    try:
        return JSONResponse(lorebook.load_lorebook(filename))
    except FileNotFoundError:
        return JSONResponse({"error": "Not found"}, status_code=404)

@router.put("/api/lorebooks/{filename}")
async def api_save_lorebook(filename: str, req: Request):
    """Save a lorebook (full data including entries)."""
    body = await req.json()
    lorebook.save_lorebook(filename, body)
    return JSONResponse({"status": "ok"})

@router.delete("/api/lorebooks/{filename}")
async def api_delete_lorebook(filename: str):
    """Delete a lorebook."""
    deleted = lorebook.delete_lorebook(filename)
    if not deleted:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"status": "ok"})

@router.put("/api/rp/sessions/{session_id}/lorebooks")
async def api_set_session_lorebooks(session_id: int, req: Request):
    """Set which lorebooks are active for an RP session."""
    body = await req.json()
    filenames = body.get("lorebooks", [])
    db.db_rp_update_settings(session_id, lorebooks=json.dumps(filenames))
    return JSONResponse({"status": "ok"})

@router.put("/api/school/sessions/{session_id}/lorebooks")
async def api_set_school_session_lorebooks(session_id: int, req: Request):
    """Set which lorebooks are active for a school session."""
    body = await req.json()
    filenames = body.get("lorebooks", [])
    db.db_school_update_settings(session_id, lorebooks=json.dumps(filenames))
    return JSONResponse({"status": "ok"})


# ─── Lorebook Import / Export ─────────────────────────────────────

@router.post("/api/lorebooks/import")
async def api_import_lorebook(req: Request):
    """Import a lorebook from SillyTavern World Info JSON (raw JSON body)."""
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"error": "Expected a JSON object"}, status_code=400)
    if "entries" not in data:
        return JSONResponse({"error": "Not a valid lorebook: missing 'entries' key"}, status_code=400)
    result = lorebook.import_lorebook(data)
    return JSONResponse(result)


@router.get("/api/lorebooks/{filename}/export")
async def api_export_lorebook(filename: str):
    """Export a lorebook as downloadable JSON."""
    try:
        data = lorebook.load_lorebook(filename)
    except FileNotFoundError:
        return JSONResponse({"error": "Not found"}, status_code=404)
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    safe_name = filename.replace(".json", "")
    return PlainTextResponse(
        json_str,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
    )
