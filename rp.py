"""
Character School — RP mode routes + WebSocket + config API.
Split from server.py.
"""
import asyncio
import json
import os
import sqlite3
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import db
import engine

router = APIRouter()

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
    sess["messages"] = [{"id": m["id"], "role": m["role"], "speaker": m["speaker"], "content": m["content"]} for m in messages]
    return JSONResponse(sess)


@router.delete("/api/rp/sessions/{session_id}")
async def api_rp_delete_session(session_id: int):
    deleted = db.db_rp_delete_session(session_id)
    return JSONResponse({"deleted": deleted})


@router.post("/api/rp/sessions/{session_id}/branch")
async def api_rp_branch_session(session_id: int):
    result = db.db_rp_branch_session(session_id)
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
    response_style = 'brief'
    summary_window = 20
    raw_window = 10
    current_gen_task = None  # background generation task (for stop support)

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
                        await ws.send_json({"type": "generation_stopped"})
                    except Exception:
                        pass
                continue

            elif data["type"] == "start":
                char_filenames = data.get("characters", [])
                persona_filename = data.get("persona_filename")
                turn_routing = data.get("turn_routing", "auto")
                response_style = data.get("response_style", "brief")
                summary_window = data.get("summary_window", 20)
                raw_window = data.get("raw_window", 10)

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
                        await ws.send_json({"type": "error", "message": f"Failed to load {fn}: {e}"})

                if not cards:
                    await ws.send_json({"type": "error", "message": "No valid characters loaded"})
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

                session_id = db.db_rp_create_session(char_list, persona_filename, turn_routing, response_style, summary_window, raw_window, stack_config_str)

                await ws.send_json({
                    "type": "session_started", "session_id": session_id,
                    "characters": [{"filename": fn, "name": character_names[fn]} for fn in character_order],
                    "persona": persona_filename,
                    "turn_routing": turn_routing, "response_style": response_style,
                    "stack_config": stack_config_json or db.DEFAULT_STACK_CONFIG,
                    "console_events": [],
                })

                # Send first_mes from all characters
                _rp_user_name = persona.get("name", "User") if persona else "User"
                for fn in character_order:
                    first_mes = engine.get_first_mes(cards[fn], user_name=_rp_user_name)
                    if first_mes:
                        msg_id = db.db_rp_add_message(session_id, "character", first_mes, character_names[fn])
                        await ws.send_json({
                            "type": "character_message", "content": first_mes,
                            "character_filename": fn, "character_name": character_names[fn],
                            "is_first_mes": True, "message_id": msg_id,
                        })

            elif data["type"] == "resume":
                resume_id = data.get("session_id")
                sess = db.db_rp_get_session(resume_id)
                if not sess:
                    await ws.send_json({"type": "error", "message": "Session not found"})
                    continue

                session_id = resume_id
                turn_routing = sess["turn_routing"]
                response_style = sess["response_style"]
                summary_window = sess["summary_window"]
                raw_window = sess["raw_window"]
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

                await ws.send_json({
                    "type": "session_resumed", "session_id": session_id,
                    "characters": [{"filename": fn, "name": character_names[fn]} for fn in character_order],
                    "persona": persona_filename,
                    "turn_routing": turn_routing, "response_style": response_style,
                    "summary_window": summary_window, "raw_window": raw_window,
                    "summary_text": sess.get("summary_text", ""),
                    "stack_config": stack_cfg,
                    "messages": [{"id": m["id"], "role": m["role"], "speaker": m["speaker"], "content": m["content"]} for m in messages],
                    "console_events": json.loads(sess.get("console_events", "[]")) if sess.get("console_events") else [],
                })

            elif data["type"] == "user_message":
                user_content = data["content"]
                directed_to = data.get("directed_to")

                db.db_rp_add_message(session_id, "user", user_content)

                async def _rp_gen():
                    try:
                        # ── Auto-summary check (after user message, before LLM) ──
                        await check_auto_summary(session_id, ws)

                        # Get stack config
                        sess = db.db_rp_get_session(session_id)
                        stack_cfg = db.get_stack_config(sess)

                        # Determine head_n and tail_n from stack
                        head_n = 0
                        tail_n = 0
                        for blk in stack_cfg.get("blocks", []):
                            if blk.get("type") == "head" and blk.get("enabled", True):
                                head_n = blk.get("n", 2)
                            elif blk.get("type") == "tail" and blk.get("enabled", True):
                                tail_n = blk.get("n", 3)

                        # Check if summarization needed (middle section between head and tail)
                        msg_count = db.db_rp_count_messages(session_id)
                        all_msgs = db.db_rp_get_messages(session_id)
                        if msg_count > head_n + summary_window + tail_n:
                            end_idx = len(all_msgs) - tail_n if tail_n > 0 else len(all_msgs)
                            to_summarize = all_msgs[head_n:end_idx] if head_n < end_idx else []
                            if to_summarize:
                                existing_summary = sess.get("summary_text", "") if sess else ""
                                new_summary = await engine.rp_summarize(session_id, to_summarize, existing_summary, ws=ws)
                                db.db_rp_update_summary(session_id, new_summary)
                                await ws.send_json({"type": "summary_updated", "summary": new_summary, "summarized_count": len(to_summarize)})
                                # Refresh session to get updated summary
                                sess = db.db_rp_get_session(session_id)

                        # Build system prompt
                        system_prompt = engine.build_rp_system_prompt(
                            [cards[fn] for fn in character_order],
                            persona, turn_routing, response_style,
                            directed_character=character_names.get(directed_to) if directed_to else None,
                        )

                        # Build LLM messages from stack config
                        summary_text = sess.get("summary_text", "") if sess else ""
                        all_msgs = db.db_rp_get_messages(session_id)
                        llm_messages, block_markers = db.build_llm_messages_from_stack(
                            stack_cfg, system_prompt, all_msgs, summary_text,
                        )

                        # Typing indicator
                        if directed_to and directed_to in character_names:
                            await ws.send_json({"type": "character_typing", "character_filename": directed_to, "character_name": character_names[directed_to]})
                        else:
                            await ws.send_json({"type": "character_typing", "character_filename": None, "character_name": None})

                        # Console: log request
                        await ws.send_json({
                            "type": "console_event", "event": "request", "llm": "character",
                            "model": db.CHAT_MODEL, "temperature": db.CHAT_TEMPERATURE, "max_tokens": db.CHAT_MAX_TOKENS,
                            "messages": [{"role": m["role"], "content": m["content"]} for m in llm_messages],
                            "block_markers": block_markers,
                            "timestamp": engine._now_iso(),
                        })

                        resp = await db.chat_client.chat.completions.create(
                            model=db.CHAT_MODEL, messages=llm_messages,
                            temperature=db.CHAT_TEMPERATURE, max_tokens=db.CHAT_MAX_TOKENS,
                            extra_body={"enable_thinking": db.CHAT_ENABLE_THINKING},
                        )
                        raw_content = resp.choices[0].message.content
                        usage = resp.usage

                        await ws.send_json({
                            "type": "console_event", "event": "response", "llm": "character",
                            "model": db.CHAT_MODEL, "content": raw_content,
                            "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                            "finish_reason": resp.choices[0].finish_reason, "timestamp": engine._now_iso(),
                        })

                        parsed = engine.parse_rp_response(raw_content, character_names, character_order)

                        # In directed mode, filter to only the directed character's response
                        if directed_to and directed_to in character_names:
                            directed_name = character_names[directed_to]
                            if parsed:
                                # Keep only the directed character's responses
                                filtered = [p for p in parsed if p["filename"] == directed_to]
                                if filtered:
                                    parsed = filtered
                                else:
                                    # LLM responded as someone else despite instruction;
                                    # strip any [Name]: prefix and attribute to the directed character
                                    import re as _re
                                    stripped = _re.sub(r'^\[[^\]]+\]:\s*', '', raw_content).strip()
                                    parsed = [{"filename": directed_to, "name": directed_name, "content": stripped}]
                            else:
                                # No [Name]: prefix found — attribute to directed character
                                parsed = [{"filename": directed_to, "name": directed_name, "content": raw_content.strip()}]
                        elif not parsed:
                            # Non-directed mode, no [Name]: prefix — send raw as first character
                            fn = character_order[0] if character_order else None
                            name = character_names.get(fn, "Unknown")
                            parsed = [{"filename": fn, "name": name, "content": raw_content.strip()}]

                        await ws.send_json({"type": "character_typing_stopped"})

                        if parsed:
                            for pr in parsed:
                                msg_id = db.db_rp_add_message(session_id, "character", pr["content"], pr["name"])
                                await ws.send_json({
                                    "type": "character_message", "content": pr["content"],
                                    "character_filename": pr["filename"], "character_name": pr["name"],
                                    "is_first_mes": False, "message_id": msg_id,
                                })

                        # ── Auto-summary check (after character messages added) ──
                        await check_auto_summary(session_id, ws)

                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        await ws.send_json({"type": "character_typing_stopped"})
                        await ws.send_json({"type": "error", "message": f"LLM error: {e}"})

                current_gen_task = asyncio.create_task(_rp_gen())
                continue  # back to main receive_json loop (handles stop)

            elif data["type"] == "delete_message":
                target_id = data.get("message_id")
                if target_id is not None and session_id is not None:
                    deleted = db.db_rp_delete_message(session_id, target_id)
                    await ws.send_json({"type": "message_deleted", "message_id": target_id, "success": deleted})

            elif data["type"] == "update_settings":
                if session_id:
                    turn_routing = data.get("turn_routing", turn_routing)
                    response_style = data.get("response_style", response_style)
                    summary_window = data.get("summary_window", summary_window)
                    raw_window = data.get("raw_window", raw_window)
                    db.db_rp_update_settings(session_id, turn_routing, response_style, summary_window, raw_window)
                    await ws.send_json({"type": "settings_updated", "turn_routing": turn_routing, "response_style": response_style, "summary_window": summary_window, "raw_window": raw_window})

            elif data["type"] == "update_stack":
                if session_id:
                    stack_cfg_data = data.get("stack_config")
                    if stack_cfg_data:
                        stack_cfg_str = json.dumps(stack_cfg_data)
                        db.db_rp_update_settings(session_id, stack_config=stack_cfg_str)
                        await ws.send_json({"type": "stack_updated", "stack_config": stack_cfg_data})

            elif data["type"] == "summarize_block":
                if session_id:
                    block_index = data.get("block_index")
                    sess = db.db_rp_get_session(session_id)
                    stack_cfg = db.get_stack_config(sess)
                    blocks = stack_cfg.get("blocks", [])
                    if block_index is not None and 0 <= block_index < len(blocks):
                        block = blocks[block_index]
                        if block.get("type") in ("early_summary", "late_summary"):
                            start_idx = block.get("start", 0)
                            end_idx = block.get("end", 0)
                            all_msgs = db.db_rp_get_messages(session_id)
                            start_idx = max(0, min(start_idx, len(all_msgs)))
                            end_idx = max(start_idx, min(end_idx, len(all_msgs)))
                            to_summarize = all_msgs[start_idx:end_idx]
                            existing = block.get("text", "")
                            if to_summarize:
                                await ws.send_json({"type": "block_summarizing", "block_index": block_index})
                                new_summary = await engine.rp_summarize(session_id, to_summarize, existing, ws=ws)
                                block["text"] = new_summary
                                stack_cfg["blocks"] = blocks
                                db.db_rp_update_settings(session_id, stack_config=json.dumps(stack_cfg))
                                await ws.send_json({
                                    "type": "block_summary_updated",
                                    "block_index": block_index,
                                    "summary": new_summary,
                                    "stack_config": stack_cfg,
                                })
                            else:
                                await ws.send_json({"type": "error", "message": "No messages in range to summarize."})

            elif data["type"] == "save_console_events":
                if session_id:
                    events = data.get("events", [])
                    db.db_rp_save_console_events(session_id, json.dumps(events))
                    await ws.send_json({"type": "console_events_saved"})

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
                await ws.send_json({"type": "persona_changed", "persona": persona_filename})

            elif data["type"] == "add_character":
                if not session_id:
                    await ws.send_json({"type": "error", "message": "No active session"})
                    continue
                fn = data.get("filename")
                if not fn:
                    await ws.send_json({"type": "error", "message": "No filename provided"})
                    continue
                if fn in character_order:
                    await ws.send_json({"type": "error", "message": "Character already in session"})
                    continue
                if len(character_order) >= 4:
                    await ws.send_json({"type": "error", "message": "Maximum 4 characters per session"})
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
                        await ws.send_json({
                            "type": "character_message", "content": first_mes,
                            "character_filename": fn, "character_name": name,
                            "is_first_mes": True, "message_id": msg_id,
                        })
                    await ws.send_json({
                        "type": "character_added",
                        "filename": fn, "name": name,
                        "characters": [{"filename": f, "name": character_names[f]} for f in character_order],
                    })
                except Exception as e:
                    await ws.send_json({"type": "error", "message": f"Failed to add character: {e}"})

            elif data["type"] == "remove_character":
                if not session_id:
                    await ws.send_json({"type": "error", "message": "No active session"})
                    continue
                fn = data.get("filename")
                if not fn or fn not in character_order:
                    await ws.send_json({"type": "error", "message": "Character not in session"})
                    continue
                if len(character_order) <= 1:
                    await ws.send_json({"type": "error", "message": "Cannot remove last character"})
                    continue
                removed = db.db_rp_remove_character(session_id, fn)
                if not removed:
                    await ws.send_json({"type": "error", "message": "Failed to remove character"})
                    continue
                del cards[fn]
                del character_names[fn]
                character_order.remove(fn)
                await ws.send_json({
                    "type": "character_removed",
                    "filename": fn,
                    "characters": [{"filename": f, "name": character_names[f]} for f in character_order],
                })
                # Reload session list to update title in sidebar
                await ws.send_json({"type": "reload_sessions"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except:
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
            "enable_thinking": _c.get("chat", {}).get("enable_thinking", False),
        },
        "analysis": {
            "base_url": _c.get("analysis", {}).get("base_url", ""),
            "api_key": mask(_c.get("analysis", {}).get("api_key")),
            "model": _c.get("analysis", {}).get("model", ""),
            "temperature": _c.get("analysis", {}).get("temperature", 0.1),
            "max_tokens": _c.get("analysis", {}).get("max_tokens", 1500),
        },
        "summary": {
            "base_url": _c.get("summary", {}).get("base_url", ""),
            "api_key": mask(_c.get("summary", {}).get("api_key")),
            "model": _c.get("summary", {}).get("model", ""),
            "temperature": _c.get("summary", {}).get("temperature", 0.3),
            "max_tokens": _c.get("summary", {}).get("max_tokens", 1000),
        },
        "paths": _c.get("paths", {"characters_dir": None, "personas_dir": None}),
    }


@router.post("/api/config")
async def update_config(req: Request):
    """Update config.jsonc and reload. Only writes provided fields.
    If api_key contains '…' (masked), keep the existing key."""
    body = await req.json()
    current = db.load_config()

    # Deep-merge: update only provided fields
    for section in ("server", "chat", "analysis", "summary", "paths"):
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
            else:
                current[section][k] = v

    db.save_config(current)
    db.reload_config()
    return {"status": "ok", "message": "Config saved and reloaded. Restart server if port/host changed."}


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

async def check_auto_summary(session_id, ws):
    """Check if late_summary block has auto enabled and tail has grown enough to trigger.
    Returns updated stack_cfg if summarization happened, else None."""
    sess = db.db_rp_get_session(session_id)
    stack_cfg = db.get_stack_config(sess)
    blocks = stack_cfg.get("blocks", [])

    # Find late_summary block with auto enabled
    late_block = None
    late_idx = None
    tail_n = 0
    for i, blk in enumerate(blocks):
        if blk.get("type") == "late_summary" and blk.get("enabled", True) and blk.get("auto"):
            late_block = blk
            late_idx = i
        elif blk.get("type") == "tail" and blk.get("enabled", True):
            tail_n = blk.get("n", 3)

    if late_block is None or tail_n <= 0:
        return None

    all_msgs = db.db_rp_get_messages(session_id)
    total_msgs = len(all_msgs)
    end_idx = late_block.get("end", 0)

    # Count messages from end_idx to latest
    tail_count = total_msgs - end_idx
    if tail_count < tail_n:
        return None

    # Advance end by tail_n and summarize [start, new_end]
    start_idx = max(0, late_block.get("start", 0))
    new_end = end_idx + tail_n
    new_end = min(new_end, total_msgs)

    to_summarize = all_msgs[start_idx:new_end]
    if not to_summarize:
        return None

    existing = late_block.get("text", "")
    await ws.send_json({"type": "block_summarizing", "block_index": late_idx, "auto": True})
    new_summary = await engine.rp_summarize(session_id, to_summarize, existing, ws=ws)

    late_block["text"] = new_summary
    late_block["end"] = new_end
    stack_cfg["blocks"] = blocks
    db.db_rp_update_settings(session_id, stack_config=json.dumps(stack_cfg))

    await ws.send_json({
        "type": "block_summary_updated",
        "block_index": late_idx,
        "summary": new_summary,
        "stack_config": stack_cfg,
        "auto": True,
    })

    return stack_cfg
