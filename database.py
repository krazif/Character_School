"""
Character School — database layer (SQLite + stack config).
Split from db.py.
"""
import copy
import json
import re
import sqlite3
from pathlib import Path
from typing import Optional

import config

# ─── SQLite Database ─────────────────────────────────────────────
DB_PATH = config.APP_DIR / "char_test.db"

# How long a writer waits for the lock before giving up. SQLite's default is 5s;
# a background summary save or console-events write can hold the lock briefly,
# and 5s is tight enough that a slow disk plus a 3MB blob surfaces as a raw
# "database is locked" error in the chat instead of a short wait.
BUSY_TIMEOUT_MS = 15000


def _connect() -> sqlite3.Connection:
    """Open a connection with a generous busy timeout and WAL-friendly settings."""
    conn = sqlite3.connect(str(DB_PATH), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _connect()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            card_filename   TEXT NOT NULL,
            persona_filename TEXT,
            system_prompt   TEXT NOT NULL,
            analysis_prompt TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL,
            seq         INTEGER NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            is_first_mes INTEGER DEFAULT 0,
            analysis_json TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq)")
    # ─── RP (Roleplay) tables ───
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rp_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT,
            persona_filename TEXT,
            turn_routing    TEXT DEFAULT 'auto',
            response_style  TEXT DEFAULT 'moderate',
            stack_config    TEXT,
            console_events  TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rp_characters (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER NOT NULL,
            card_filename   TEXT NOT NULL,
            char_name       TEXT NOT NULL,
            display_order   INTEGER DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES rp_sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rp_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER NOT NULL,
            seq             INTEGER NOT NULL,
            role            TEXT NOT NULL,
            speaker         TEXT,
            content         TEXT NOT NULL,
            image_path      TEXT,
            image_prompt    TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES rp_sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_messages_session ON rp_messages(session_id, seq)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_characters_session ON rp_characters(session_id, display_order)")
    # Migration: add persona_name column to rp_messages if not exists
    try:
        conn.execute("ALTER TABLE rp_messages ADD COLUMN persona_name TEXT")
    except Exception:
        pass  # Column already exists
    # Migration: add stack_config column if not exists
    try:
        conn.execute("ALTER TABLE rp_sessions ADD COLUMN stack_config TEXT")
    except Exception:
        pass  # Column already exists
    # Migration: add console_events column if not exists
    try:
        conn.execute("ALTER TABLE rp_sessions ADD COLUMN console_events TEXT")
    except Exception:
        pass  # Column already exists
    # ─── School persistent session tables ───
    conn.execute("""
        CREATE TABLE IF NOT EXISTS school_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT,
            card_filename   TEXT NOT NULL,
            persona_filename TEXT,
            stack_config    TEXT,
            console_events  TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS school_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL,
            seq         INTEGER NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            is_first_mes INTEGER DEFAULT 0,
            analysis_json TEXT,
            image_path  TEXT,
            image_prompt TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES school_sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_school_messages_session ON school_messages(session_id, seq)")
    # Migration: add response_style column to school_sessions
    try:
        conn.execute("ALTER TABLE school_sessions ADD COLUMN response_style TEXT")
    except Exception:
        pass  # Column already exists
    # Migration: add lorebooks column to rp_sessions and school_sessions
    try:
        conn.execute("ALTER TABLE rp_sessions ADD COLUMN lorebooks TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE school_sessions ADD COLUMN lorebooks TEXT")
    except Exception:
        pass
    # Migration: add client_msg_id column for delete-by-fallback
    try:
        conn.execute("ALTER TABLE rp_messages ADD COLUMN client_msg_id TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE school_messages ADD COLUMN client_msg_id TEXT")
    except Exception:
        pass
    # Migration: add bg_image column to rp_sessions and school_sessions
    try:
        conn.execute("ALTER TABLE rp_sessions ADD COLUMN bg_image TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE school_sessions ADD COLUMN bg_image TEXT")
    except Exception:
        pass
    # Migration: add pov and inner_monologue columns to rp_sessions
    try:
        conn.execute("ALTER TABLE rp_sessions ADD COLUMN pov TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE rp_sessions ADD COLUMN inner_monologue INTEGER DEFAULT 0")
    except Exception:
        pass
    # Migration: add pov and inner_monologue columns to school_sessions
    try:
        conn.execute("ALTER TABLE school_sessions ADD COLUMN pov TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE school_sessions ADD COLUMN inner_monologue INTEGER DEFAULT 0")
    except Exception:
        pass
    # Migration: add auto_continue column to rp_sessions and school_sessions
    try:
        conn.execute("ALTER TABLE rp_sessions ADD COLUMN auto_continue INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE school_sessions ADD COLUMN auto_continue INTEGER DEFAULT 0")
    except Exception:
        pass
    conn.commit()
    conn.close()

    # Migrations
    _migrate_add_image_path()
    _migrate_add_image_prompt()
    _migrate_add_seed()
    _migrate_add_swipes()


def _migrate_add_swipes():
    """Add swipes and active_swipe columns to rp_messages and school_messages if missing."""
    conn = _connect()
    cur = conn.cursor()
    try:
        for table in ("rp_messages", "school_messages"):
            cur.execute(f"PRAGMA table_info({table})")
            cols = [row[1] for row in cur.fetchall()]
            if "swipes" not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN swipes TEXT")
            if "active_swipe" not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN active_swipe INTEGER DEFAULT 0")
        conn.commit()
    finally:
        conn.close()


def _migrate_add_seed():
    """Add seed column to rp_messages and school_messages if missing."""
    conn = _connect()
    cur = conn.cursor()
    try:
        for table in ("rp_messages", "school_messages"):
            cur.execute(f"PRAGMA table_info({table})")
            cols = [row[1] for row in cur.fetchall()]
            if "seed" not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN seed INTEGER")
        conn.commit()
    finally:
        conn.close()


def _migrate_add_image_path():
    """Add image_path columns to message tables if they don't exist."""
    conn = _connect()
    cur = conn.cursor()
    try:
        for table in ("rp_messages", "school_messages"):
            cur.execute(f"PRAGMA table_info({table})")
            cols = [row[1] for row in cur.fetchall()]
            if "image_path" not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN image_path TEXT")
        conn.commit()
    finally:
        conn.close()


def _migrate_add_image_prompt():
    """Add image_prompt column to rp_messages and school_messages if missing."""
    conn = _connect()
    cur = conn.cursor()
    try:
        for table in ("rp_messages", "school_messages"):
            cur.execute(f"PRAGMA table_info({table})")
            cols = [row[1] for row in cur.fetchall()]
            if "image_prompt" not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN image_prompt TEXT")
        conn.commit()
    finally:
        conn.close()


init_db()


def db_create_session(card_filename: str, system_prompt: str, analysis_prompt: str,
                       persona_filename: str = None) -> int:
    """Create a new session row, return its id."""
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO sessions (card_filename, persona_filename, system_prompt, analysis_prompt) VALUES (?, ?, ?, ?)",
        (card_filename, persona_filename, system_prompt, analysis_prompt),
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def db_add_message(session_id: int, role: str, content: str,
                    is_first_mes: bool = False, analysis_json: str = None) -> int:
    """Insert a message, return its row id."""
    conn = _connect()
    # Get next seq
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?", (session_id,)).fetchone()
    next_seq = row[0]
    cur = conn.execute(
        "INSERT INTO messages (session_id, seq, role, content, is_first_mes, analysis_json) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, next_seq, role, content, 1 if is_first_mes else 0, analysis_json),
    )
    msg_id = cur.lastrowid
    conn.commit()
    conn.close()
    return msg_id


def db_get_llm_messages(session_id: int) -> list[dict]:
    """Get all messages for LLM context, ordered by seq.
    The system prompt is stored in the sessions table and prepended here."""
    conn = _connect()
    sess = conn.execute(
        "SELECT system_prompt FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY seq", (session_id,)
    ).fetchall()
    conn.close()
    result = []
    if sess and sess[0]:
        result.append({"role": "system", "content": sess[0]})
    for r in rows:
        result.append({"role": r[0], "content": r[1]})
    return result


def db_get_assistant_messages(session_id: int) -> list[dict]:
    """Get all assistant messages with their analysis, ordered by seq."""
    conn = _connect()
    rows = conn.execute(
        "SELECT id, content, analysis_json, is_first_mes FROM messages WHERE session_id = ? AND role = 'assistant' ORDER BY seq",
        (session_id,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        analysis = json.loads(r[2]) if r[2] else None
        result.append({"id": r[0], "content": r[1], "analysis": analysis, "is_first_mes": bool(r[3])})
    return result


def db_delete_message(session_id: int, message_id: int) -> bool:
    """Delete a message and all subsequent messages in the session."""
    conn = _connect()
    row = conn.execute(
        "SELECT seq FROM messages WHERE id = ? AND session_id = ?", (message_id, session_id)
    ).fetchone()
    if not row:
        conn.close()
        return False
    deleted_seq = row[0]
    # Delete the target message and everything after it
    conn.execute(
        "DELETE FROM messages WHERE session_id = ? AND seq >= ?", (session_id, deleted_seq)
    )
    conn.commit()
    conn.close()
    return True


def db_clear_messages(session_id: int) -> None:
    """Delete all messages for a session (used by reset)."""
    conn = _connect()
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()


def db_get_session(session_id: int) -> Optional[dict]:
    """Get session metadata."""
    conn = _connect()
    row = conn.execute(
        "SELECT id, card_filename, persona_filename, system_prompt, analysis_prompt FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "card_filename": row[1], "persona_filename": row[2],
            "system_prompt": row[3], "analysis_prompt": row[4]}


# ─── RP Stack Config ─────────────────────────────────────────────
# Chunk-based summary system (option B):
#   summary_chunk — engine-generated, read-only, covers a fixed msg range
#   custom        — user-editable free-text inject
#   Multiple of each can be interleaved arbitrarily.
DEFAULT_STACK_CONFIG = {
    "blocks": [
        {"type": "system", "enabled": True, "locked": True},
        {"type": "head", "enabled": True, "n": 2},
        {"type": "summary_chunk", "enabled": True, "start": 2, "end": 5, "text": ""},
        {"type": "raw_mid", "enabled": True, "start": 5, "end": 8},
        {"type": "summary_chunk", "enabled": True, "start": 8, "end": 11, "text": "", "auto": False},
        {"type": "custom", "enabled": False, "text": ""},
        {"type": "tail", "enabled": True, "n": 3},
    ]
}


def get_stack_config(sess: dict) -> dict:
    """Return stack_config from session, or default if not set."""
    raw = sess.get("stack_config") if sess else None
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    import copy
    return copy.deepcopy(DEFAULT_STACK_CONFIG)


def build_llm_messages_from_stack(stack_config: dict, system_prompt: str,
                                  all_msgs: list[dict]):
    """Build LLM messages from stack block configuration.
    Returns (llm_messages, block_markers) where block_markers is a list of
    {"block": type, "label": str, "start": int, "count": int}.
    Supports dynamic blocks: system, head, summary_chunk, raw_mid,
    custom, tail. summary_chunk and custom can appear multiple times.
    Backward-compatible with early_summary/late_summary/summary.
    """
    blocks = stack_config.get("blocks", [])
    llm_messages = []
    block_markers = []

    # Determine head_n and tail_n from enabled blocks
    head_n = 0
    tail_n = 0
    for block in blocks:
        if block.get("type") == "head" and block.get("enabled", True):
            head_n = block.get("n", 2)
        elif block.get("type") == "tail" and block.get("enabled", True):
            tail_n = block.get("n", 3)

    def _add_raw_msgs(msgs):
        """Append raw messages, return (start_index, count).
        Skips messages with empty content (e.g. image-only messages)."""
        start = len(llm_messages)
        for m in msgs:
            content = m.get("content") or ""
            if not content.strip():
                continue  # skip empty-content messages (image-only, etc.)
            if m["role"] == "user":
                llm_messages.append({"role": "user", "content": content})
            elif m["role"] == "character":
                # Strip any leading [Speaker]: prefix from content to prevent
                # double-prefixing (model may have included it in raw output)
                clean = content
                sp = m.get("speaker")
                if sp:
                    clean = re.sub(r'^\[?' + re.escape(sp) + r'\]?:\s*', '', clean)
                llm_messages.append({"role": "assistant", "content": f"[{m['speaker']}]: {clean}"})
            elif m["role"] == "assistant":
                llm_messages.append({"role": "assistant", "content": content})
            elif m["role"] == "system":
                llm_messages.append({"role": "system", "content": content})
        return start, len(llm_messages) - start

    # Count summary chunks for labels
    summary_chunk_num = 0
    custom_num = 0

    for block in blocks:
        if not block.get("enabled", True) and not block.get("locked", False):
            continue

        btype = block.get("type", "")
        # Backward compat: old types → summary_chunk
        if btype in ("summary", "early_summary", "late_summary"):
            btype = "summary_chunk"

        if btype == "summary_chunk":
            summary_chunk_num += 1
            label = f"Summary Chunk {summary_chunk_num} [{block.get('start','?')}-{block.get('end','?')}]"
        elif btype == "custom":
            custom_num += 1
            label = f"Custom Inject {custom_num}"
        elif btype == "system":
            label = "System Prompt"
        elif btype == "head":
            label = f"Head Messages (first {head_n})"
        elif btype == "raw_mid":
            label = f"Raw Mid [{block.get('start','?')}-{block.get('end','?')}]"
        elif btype == "tail":
            label = f"Raw Tail (last {tail_n})"
        else:
            label = btype

        if btype == "system":
            sp = (system_prompt or "").strip()
            if sp:
                start = len(llm_messages)
                llm_messages.append({"role": "system", "content": sp})
                block_markers.append({"block": btype, "label": label, "start": start, "count": 1})

        elif btype == "head" and head_n > 0:
            head_msgs = all_msgs[:head_n]
            s, c = _add_raw_msgs(head_msgs)
            if c > 0:
                block_markers.append({"block": btype, "label": label, "start": s, "count": c})

        elif btype == "summary_chunk":
            text = block.get("text", "").strip()
            if text:
                start = len(llm_messages)
                llm_messages.append({"role": "system", "content": f"SCENE SUMMARY:\n{text}"})
                block_markers.append({"block": btype, "label": label, "start": start, "count": 1})

        elif btype == "raw_mid":
            s_idx = block.get("start", head_n)
            e_idx = block.get("end", len(all_msgs) - tail_n)
            # Clamp to valid range
            s_idx = max(0, min(s_idx, len(all_msgs)))
            e_idx = max(s_idx, min(e_idx, len(all_msgs)))
            mid_msgs = all_msgs[s_idx:e_idx]
            s, c = _add_raw_msgs(mid_msgs)
            if c > 0:
                block_markers.append({"block": btype, "label": label, "start": s, "count": c})

        elif btype == "custom":
            text = block.get("text", "").strip()
            if text:
                start = len(llm_messages)
                llm_messages.append({"role": "system", "content": text})
                block_markers.append({"block": btype, "label": label, "start": start, "count": 1})

        elif btype == "tail" and tail_n > 0:
            # Avoid duplicating head messages
            start_idx = max(head_n, len(all_msgs) - tail_n) if len(all_msgs) > tail_n else head_n
            tail_msgs = all_msgs[start_idx:]
            s, c = _add_raw_msgs(tail_msgs)
            if c > 0:
                block_markers.append({"block": btype, "label": label, "start": s, "count": c})

    # ─── Qwen/strict-template compat: flatten mid-stack system → user ───
    # Some model chat templates (notably Qwen) reject system messages
    # that don't appear at position 0 ("System message must be at the
    # beginning").  Convert any non-first system message to user role so
    # it passes through cleanly.  Content already prefixed with
    # "SCENE SUMMARY:" is left as-is (self-describing); everything else
    # gets a [System Note] prefix to distinguish it from in-character
    # user speech.  Safe for all models — user-role injection is the
    # standard pattern used by SillyTavern author's notes, etc.
    for _i in range(1, len(llm_messages)):
        if llm_messages[_i].get("role") == "system":
            llm_messages[_i]["role"] = "user"
            _c = llm_messages[_i]["content"]
            if not _c.startswith(("SCENE SUMMARY:", "[System", "[Scene")):
                llm_messages[_i]["content"] = f"[System Note]\n{_c}"

    return llm_messages, block_markers


# ─── RP Database Functions ────────────────────────────────────────
def db_rp_create_session(characters: list[dict], persona_filename: str,
                         turn_routing: str, response_style: str,
                         stack_config: str = None) -> int:
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO rp_sessions (title, persona_filename, turn_routing, response_style, stack_config) VALUES (?, ?, ?, ?, ?)",
        (', '.join(c['name'] for c in characters), persona_filename, turn_routing, response_style, stack_config),
    )
    sid = cur.lastrowid
    for i, c in enumerate(characters):
        conn.execute(
            "INSERT INTO rp_characters (session_id, card_filename, char_name, display_order) VALUES (?, ?, ?, ?)",
            (sid, c['filename'], c['name'], i),
        )
    conn.commit()
    conn.close()
    return sid


def db_rp_add_message(session_id: int, role: str, content: str, speaker: str = None, persona_name: str = None, image_path: str = None, image_prompt: str = None, client_msg_id: str = None, seed: int = None) -> int:
    conn = _connect()
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM rp_messages WHERE session_id = ?", (session_id,)).fetchone()
    next_seq = row[0]
    cur = conn.execute(
        "INSERT INTO rp_messages (session_id, seq, role, speaker, content, persona_name, image_path, image_prompt, client_msg_id, seed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, next_seq, role, speaker, content, persona_name, image_path, image_prompt, client_msg_id, seed),
    )
    msg_id = cur.lastrowid
    conn.execute("UPDATE rp_sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return msg_id


def db_rp_get_messages(session_id: int) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT id, seq, role, speaker, content, persona_name, image_path, image_prompt, seed, swipes, active_swipe, client_msg_id FROM rp_messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        swipes = None
        if r[9]:
            try:
                swipes = json.loads(r[9])
            except Exception:
                pass
        result.append({"id": r[0], "seq": r[1], "role": r[2], "speaker": r[3], "content": r[4], "persona_name": r[5], "image_path": r[6], "image_prompt": r[7], "seed": r[8], "swipes": swipes, "active_swipe": r[10] if len(r) > 10 else 0, "client_msg_id": r[11] if len(r) > 11 else None})
    return result


def db_rp_get_session(session_id: int) -> Optional[dict]:
    conn = _connect()
    row = conn.execute(
        "SELECT id, title, persona_filename, turn_routing, response_style, stack_config, console_events, lorebooks, bg_image, pov, inner_monologue, auto_continue FROM rp_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        conn.close()
        return None
    chars = conn.execute(
        "SELECT card_filename, char_name, display_order FROM rp_characters WHERE session_id = ? ORDER BY display_order",
        (session_id,),
    ).fetchall()
    conn.close()
    return {
        "id": row[0], "title": row[1], "persona_filename": row[2],
        "turn_routing": row[3], "response_style": row[4],
        "stack_config": row[5] if len(row) > 5 else None,
        "console_events": row[6] if len(row) > 6 else None,
        "lorebooks": row[7] if len(row) > 7 and row[7] else None,
        "bg_image": row[8] if len(row) > 8 and row[8] else None,
            "pov": row[9] if len(row) > 9 and row[9] else None,
            "inner_monologue": bool(row[10]) if len(row) > 10 and row[10] else False,
            "auto_continue": bool(row[11]) if len(row) > 11 and row[11] else False,
            "characters": [{"card_filename": c[0], "char_name": c[1], "display_order": c[2]} for c in chars],
    }


def _cap_console_events(events_json: str, max_events: int = 60,
                        max_bytes: int = 512 * 1024) -> str:
    """Keep only the newest console events before they hit the DB.

    A single 'request' event carries the whole LLM message list (~50KB), and the
    console had no limit by default, so an old session's row grew into the
    multi-MB range and rewrote itself in full on every event — a long write
    window that made lock contention much more likely. Trim to the byte budget
    (and the event count) so the row stays small; what the console shows live is
    unaffected.
    """
    if not events_json or len(events_json) <= max_bytes:
        return events_json
    try:
        events = json.loads(events_json)
    except Exception:
        return events_json
    if not isinstance(events, list):
        return events_json

    kept = []
    used = 2  # "[]"
    for ev in reversed(events):          # newest first
        if len(kept) >= max_events:
            break
        size = len(json.dumps(ev)) + 1   # +1 for the comma
        if kept and used + size > max_bytes:
            break
        kept.append(ev)
        used += size
    kept.reverse()
    return json.dumps(kept)


def db_rp_save_console_events(session_id: int, events_json: str):
    events_json = _cap_console_events(events_json)
    conn = _connect()
    conn.execute(
        "UPDATE rp_sessions SET console_events = ?, updated_at = datetime('now') WHERE id = ?",
        (events_json, session_id),
    )
    conn.commit()
    conn.close()


def db_rp_list_sessions() -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT id, title, persona_filename, turn_routing, response_style, updated_at FROM rp_sessions ORDER BY updated_at DESC",
    ).fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "persona_filename": r[2],
             "turn_routing": r[3], "response_style": r[4], "updated_at": r[5]} for r in rows]


def db_rp_delete_session(session_id: int) -> bool:
    conn = _connect()
    _cleanup_image_files(conn, session_id, "rp_messages", 0)
    conn.execute("DELETE FROM rp_messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM rp_characters WHERE session_id = ?", (session_id,))
    cur = conn.execute("DELETE FROM rp_sessions WHERE id = ?", (session_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def _cleanup_image_files(conn, session_id: int, table: str, deleted_seq: int):
    """Delete image files from disk for all messages being removed."""
    rows = conn.execute(
        f"SELECT image_path FROM {table} WHERE session_id = ? AND seq >= ? AND image_path IS NOT NULL",
        (session_id, deleted_seq),
    ).fetchall()
    for (img_path,) in rows:
        if img_path:
            try:
                full = config.UPLOAD_DIR / Path(img_path).name
                if full.exists():
                    full.unlink()
            except Exception:
                pass


def db_rp_delete_message(session_id: int, message_id: int = None, client_msg_id: str = None) -> bool:
    conn = _connect()
    row = None
    if message_id is not None:
        row = conn.execute("SELECT seq FROM rp_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row and client_msg_id is not None:
        row = conn.execute("SELECT seq FROM rp_messages WHERE client_msg_id = ? AND session_id = ?", (client_msg_id, session_id)).fetchone()
    if not row:
        conn.close()
        return False
    deleted_seq = row[0]
    _cleanup_image_files(conn, session_id, "rp_messages", deleted_seq)
    conn.execute("DELETE FROM rp_messages WHERE session_id = ? AND seq >= ?", (session_id, deleted_seq))
    conn.commit()
    conn.close()
    return True


def db_rp_delete_single_message(session_id: int, message_id: int = None, client_msg_id: str = None) -> bool:
    """Delete a single message and renumber remaining seq values."""
    conn = _connect()
    row = None
    if message_id is not None:
        row = conn.execute("SELECT seq FROM rp_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row and client_msg_id is not None:
        row = conn.execute("SELECT seq FROM rp_messages WHERE client_msg_id = ? AND session_id = ?", (client_msg_id, session_id)).fetchone()
    if not row:
        conn.close()
        return False
    deleted_seq = row[0]
    # Cleanup image for this single message only
    img_row = conn.execute("SELECT image_path FROM rp_messages WHERE seq = ? AND session_id = ? AND image_path IS NOT NULL", (deleted_seq, session_id)).fetchone()
    if img_row and img_row[0]:
        try:
            full = config.UPLOAD_DIR / Path(img_row[0]).name
            if full.exists():
                full.unlink()
        except Exception:
            pass
    # Delete the single message
    conn.execute("DELETE FROM rp_messages WHERE session_id = ? AND seq = ?", (session_id, deleted_seq))
    # Renumber seq for remaining messages
    conn.execute("UPDATE rp_messages SET seq = seq - 1 WHERE session_id = ? AND seq > ?", (session_id, deleted_seq))
    conn.commit()
    conn.close()
    return True


def db_rp_add_swipe(session_id: int, message_id: int, new_content: str) -> dict:
    """Append a new swipe to a message. Returns {active_swipe, swipe_count}."""
    conn = _connect()
    row = conn.execute("SELECT content, swipes FROM rp_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row:
        conn.close()
        return {}
    old_content = row[0]
    swipes_raw = row[1]
    if swipes_raw:
        try:
            swipes = json.loads(swipes_raw)
        except Exception:
            swipes = [old_content]
    else:
        swipes = [old_content]
    swipes.append(new_content)
    new_index = len(swipes) - 1
    conn.execute(
        "UPDATE rp_messages SET swipes = ?, active_swipe = ?, content = ? WHERE id = ? AND session_id = ?",
        (json.dumps(swipes), new_index, new_content, message_id, session_id),
    )
    conn.commit()
    conn.close()
    return {"active_swipe": new_index, "swipe_count": len(swipes)}


def db_rp_switch_swipe(session_id: int, message_id: int, swipe_index: int) -> dict:
    """Switch active swipe. Returns {content, active_swipe, swipe_count} or empty."""
    conn = _connect()
    row = conn.execute("SELECT swipes FROM rp_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row or not row[0]:
        conn.close()
        return {}
    try:
        swipes = json.loads(row[0])
    except Exception:
        conn.close()
        return {}
    if swipe_index < 0 or swipe_index >= len(swipes):
        conn.close()
        return {}
    conn.execute(
        "UPDATE rp_messages SET active_swipe = ?, content = ? WHERE id = ? AND session_id = ?",
        (swipe_index, swipes[swipe_index], message_id, session_id),
    )
    conn.commit()
    conn.close()
    return {"content": swipes[swipe_index], "active_swipe": swipe_index, "swipe_count": len(swipes)}






def db_rp_truncate_after(session_id: int, message_id: int) -> bool:
    """Delete all messages after the given message (seq > target_seq), keeping the target."""
    conn = _connect()
    row = conn.execute("SELECT seq FROM rp_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row:
        conn.close()
        return False
    target_seq = row[0]
    _cleanup_image_files(conn, session_id, "rp_messages", target_seq + 1)
    conn.execute("DELETE FROM rp_messages WHERE session_id = ? AND seq > ?", (session_id, target_seq))
    conn.commit()
    conn.close()
    return True


def db_rp_count_messages(session_id: int) -> int:
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) FROM rp_messages WHERE session_id = ?", (session_id,)).fetchone()
    conn.close()
    return row[0] if row else 0


def db_rp_update_settings(session_id: int, turn_routing: str = None, response_style: str = None,
                           stack_config: str = None, lorebooks: str = None, bg_image: str = None,
                           pov: str = None, inner_monologue: bool = None, auto_continue: bool = None) -> None:
    conn = _connect()
    updates = []
    params = []
    if turn_routing is not None:
        updates.append("turn_routing = ?")
        params.append(turn_routing)
    if response_style is not None:
        updates.append("response_style = ?")
        params.append(response_style)
    if stack_config is not None:
        updates.append("stack_config = ?")
        params.append(stack_config)
    if lorebooks is not None:
        updates.append("lorebooks = ?")
        params.append(lorebooks)
    if bg_image is not None:
        updates.append("bg_image = ?")
        params.append(bg_image)
    if pov is not None:
        updates.append("pov = ?")
        params.append(pov)
    if inner_monologue is not None:
        updates.append("inner_monologue = ?")
        params.append(1 if inner_monologue else 0)
    if auto_continue is not None:
        updates.append("auto_continue = ?")
        params.append(1 if auto_continue else 0)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(session_id)
        conn.execute(f"UPDATE rp_sessions SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    conn.close()


def db_rp_add_character(session_id: int, filename: str, name: str) -> bool:
    """Add a character to an existing RP session. Returns True on success."""
    conn = _connect()
    # Check if already exists
    existing = conn.execute(
        "SELECT 1 FROM rp_characters WHERE session_id = ? AND card_filename = ?",
        (session_id, filename),
    ).fetchone()
    if existing:
        conn.close()
        return False
    # Get next display_order
    row = conn.execute(
        "SELECT COALESCE(MAX(display_order), 0) + 1 FROM rp_characters WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    next_order = row[0]
    conn.execute(
        "INSERT INTO rp_characters (session_id, card_filename, char_name, display_order) VALUES (?, ?, ?, ?)",
        (session_id, filename, name, next_order),
    )
    # Update session title
    chars = conn.execute(
        "SELECT char_name FROM rp_characters WHERE session_id = ? ORDER BY display_order",
        (session_id,),
    ).fetchall()
    title = ', '.join(c[0] for c in chars)
    conn.execute(
        "UPDATE rp_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
        (title, session_id),
    )
    conn.commit()
    conn.close()
    return True


def db_rp_remove_character(session_id: int, filename: str) -> bool:
    """Remove a character from an existing RP session. Returns True on success.
    Messages from the removed character are preserved (historical record)."""
    conn = _connect()
    # Count remaining characters
    count_row = conn.execute(
        "SELECT COUNT(*) FROM rp_characters WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if count_row[0] <= 1:
        conn.close()
        return False  # Can't remove last character
    conn.execute(
        "DELETE FROM rp_characters WHERE session_id = ? AND card_filename = ?",
        (session_id, filename),
    )
    # Re-index display_order
    chars = conn.execute(
        "SELECT card_filename FROM rp_characters WHERE session_id = ? ORDER BY display_order",
        (session_id,),
    ).fetchall()
    for i, c in enumerate(chars):
        conn.execute(
            "UPDATE rp_characters SET display_order = ? WHERE session_id = ? AND card_filename = ?",
            (i, session_id, c[0]),
        )
    # Update session title
    names = conn.execute(
        "SELECT char_name FROM rp_characters WHERE session_id = ? ORDER BY display_order",
        (session_id,),
    ).fetchall()
    title = ', '.join(n[0] for n in names)
    conn.execute(
        "UPDATE rp_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
        (title, session_id),
    )
    conn.commit()
    conn.close()
    return True


def db_rp_fork_session(session_id: int) -> Optional[dict]:
    """Duplicate an RP session with all its characters, messages, and settings.
    Returns {'new_session_id': N, 'title': '...'} or None on failure."""
    sess = db_rp_get_session(session_id)
    if not sess:
        return None
    conn = _connect()
    try:
        # Copy session row with (fork) suffix
        fork_title = sess['title'] + ' (fork)' if sess['title'] else 'Untitled (fork)'
        cur = conn.execute(
            """INSERT INTO rp_sessions
           (title, persona_filename, turn_routing, response_style,
             stack_config, console_events, lorebooks, pov, inner_monologue, bg_image, auto_continue)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fork_title, sess['persona_filename'], sess['turn_routing'], sess['response_style'],
             sess.get('stack_config'), sess.get('console_events'), sess.get('lorebooks'),
             sess.get('pov'), sess.get('inner_monologue', 0), sess.get('bg_image'),
             1 if sess.get('auto_continue') else 0),
        )
        new_sid = cur.lastrowid
        # Copy characters
        chars = conn.execute(
            "SELECT card_filename, char_name, display_order FROM rp_characters WHERE session_id = ? ORDER BY display_order",
            (session_id,),
        ).fetchall()
        for c in chars:
            conn.execute(
                "INSERT INTO rp_characters (session_id, card_filename, char_name, display_order) VALUES (?, ?, ?, ?)",
                (new_sid, c[0], c[1], c[2]),
            )
        # Copy messages (preserving seq, role, speaker, content, persona_name)
        msgs = conn.execute(
            "SELECT seq, role, speaker, content, persona_name, image_path, image_prompt, client_msg_id, seed, swipes, active_swipe FROM rp_messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        for m in msgs:
            conn.execute(
                "INSERT INTO rp_messages (session_id, seq, role, speaker, content, persona_name, image_path, image_prompt, client_msg_id, seed, swipes, active_swipe) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_sid, m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10]),
            )
        conn.commit()
        return {'new_session_id': new_sid, 'title': fork_title}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def db_rp_set_persona(session_id: int, persona_filename: str) -> None:
    conn = _connect()
    conn.execute("UPDATE rp_sessions SET persona_filename = ?, updated_at = datetime('now') WHERE id = ?", (persona_filename, session_id))
    conn.commit()
    conn.close()


# ─── School Database Functions ────────────

def db_school_create_session(card_filename: str, persona_filename: str = None,
                             stack_config: str = None, title: str = None) -> int:
    conn = _connect()
    if title:
        _title = title
    else:
        _title = Path(card_filename).stem
    cur = conn.execute(
        "INSERT INTO school_sessions (title, card_filename, persona_filename, stack_config) VALUES (?, ?, ?, ?)",
        (_title, card_filename, persona_filename, stack_config),
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def db_school_update_message_analysis(message_id: int, analysis_json: str) -> None:
    """Store analysis result on an existing school message."""
    conn = _connect()
    conn.execute("UPDATE school_messages SET analysis_json = ? WHERE id = ?", (analysis_json, message_id))
    conn.commit()
    conn.close()


def db_school_add_message(session_id: int, role: str, content: str,
                          is_first_mes: bool = False, analysis_json: str = None, image_path: str = None, image_prompt: str = None, client_msg_id: str = None, seed: int = None) -> int:
    conn = _connect()
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM school_messages WHERE session_id = ?", (session_id,)).fetchone()
    next_seq = row[0]
    cur = conn.execute(
        "INSERT INTO school_messages (session_id, seq, role, content, is_first_mes, analysis_json, image_path, image_prompt, client_msg_id, seed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, next_seq, role, content, 1 if is_first_mes else 0, analysis_json, image_path, image_prompt, client_msg_id, seed),
    )
    msg_id = cur.lastrowid
    conn.execute("UPDATE school_sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return msg_id


def db_school_get_messages(session_id: int) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT id, seq, role, content, is_first_mes, analysis_json, image_path, image_prompt, seed, swipes, active_swipe, client_msg_id FROM school_messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        swipes = None
        if r[9]:
            try:
                swipes = json.loads(r[9])
            except Exception:
                pass
        result.append({"id": r[0], "seq": r[1], "role": r[2], "content": r[3],
                 "is_first_mes": bool(r[4]), "analysis_json": r[5], "image_path": r[6], "image_prompt": r[7], "seed": r[8], "swipes": swipes, "active_swipe": r[10] if len(r) > 10 else 0, "client_msg_id": r[11] if len(r) > 11 else None})
    return result


def db_school_get_session(session_id: int) -> Optional[dict]:
    conn = _connect()
    row = conn.execute(
        "SELECT id, title, card_filename, persona_filename, stack_config, console_events, lorebooks, bg_image, pov, inner_monologue, auto_continue, response_style FROM school_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "title": row[1], "card_filename": row[2],
        "persona_filename": row[3], "stack_config": row[4], "console_events": row[5],
        "lorebooks": row[6] if len(row) > 6 and row[6] else None,
        "bg_image": row[7] if len(row) > 7 and row[7] else None,
        "pov": row[8] if len(row) > 8 and row[8] else None,
        "inner_monologue": bool(row[9]) if len(row) > 9 and row[9] else False,
        "auto_continue": bool(row[10]) if len(row) > 10 and row[10] else False,
        "response_style": row[11] if len(row) > 11 and row[11] else None,
    }


def db_school_list_sessions() -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT id, title, card_filename, persona_filename, updated_at FROM school_sessions ORDER BY updated_at DESC",
    ).fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "card_filename": r[2],
             "persona_filename": r[3], "updated_at": r[4]} for r in rows]


def db_school_delete_session(session_id: int) -> bool:
    conn = _connect()
    _cleanup_image_files(conn, session_id, "school_messages", 0)
    conn.execute("DELETE FROM school_messages WHERE session_id = ?", (session_id,))
    cur = conn.execute("DELETE FROM school_sessions WHERE id = ?", (session_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def db_school_delete_message(session_id: int, message_id: int = None, client_msg_id: str = None) -> bool:
    conn = _connect()
    row = None
    if message_id is not None:
        row = conn.execute("SELECT seq FROM school_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row and client_msg_id is not None:
        row = conn.execute("SELECT seq FROM school_messages WHERE client_msg_id = ? AND session_id = ?", (client_msg_id, session_id)).fetchone()
    if not row:
        conn.close()
        return False
    deleted_seq = row[0]
    _cleanup_image_files(conn, session_id, "school_messages", deleted_seq)
    conn.execute("DELETE FROM school_messages WHERE session_id = ? AND seq >= ?", (session_id, deleted_seq))
    conn.commit()
    conn.close()
    return True


def db_school_delete_single_message(session_id: int, message_id: int = None, client_msg_id: str = None) -> bool:
    """Delete a single message and renumber remaining seq values."""
    conn = _connect()
    row = None
    if message_id is not None:
        row = conn.execute("SELECT seq FROM school_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row and client_msg_id is not None:
        row = conn.execute("SELECT seq FROM school_messages WHERE client_msg_id = ? AND session_id = ?", (client_msg_id, session_id)).fetchone()
    if not row:
        conn.close()
        return False
    deleted_seq = row[0]
    # Cleanup image for this single message only
    img_row = conn.execute("SELECT image_path FROM school_messages WHERE seq = ? AND session_id = ? AND image_path IS NOT NULL", (deleted_seq, session_id)).fetchone()
    if img_row and img_row[0]:
        try:
            full = config.UPLOAD_DIR / Path(img_row[0]).name
            if full.exists():
                full.unlink()
        except Exception:
            pass
    # Delete the single message
    conn.execute("DELETE FROM school_messages WHERE session_id = ? AND seq = ?", (session_id, deleted_seq))
    # Renumber seq for remaining messages
    conn.execute("UPDATE school_messages SET seq = seq - 1 WHERE session_id = ? AND seq > ?", (session_id, deleted_seq))
    conn.commit()
    conn.close()
    return True


def db_school_add_swipe(session_id: int, message_id: int, new_content: str) -> dict:
    """Append a new swipe to a message. Returns {active_swipe, swipe_count}."""
    conn = _connect()
    row = conn.execute("SELECT content, swipes FROM school_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row:
        conn.close()
        return {}
    old_content = row[0]
    swipes_raw = row[1]
    if swipes_raw:
        try:
            swipes = json.loads(swipes_raw)
        except Exception:
            swipes = [old_content]
    else:
        swipes = [old_content]
    swipes.append(new_content)
    new_index = len(swipes) - 1
    conn.execute(
        "UPDATE school_messages SET swipes = ?, active_swipe = ?, content = ? WHERE id = ? AND session_id = ?",
        (json.dumps(swipes), new_index, new_content, message_id, session_id),
    )
    conn.commit()
    conn.close()
    return {"active_swipe": new_index, "swipe_count": len(swipes)}


def db_school_switch_swipe(session_id: int, message_id: int, swipe_index: int) -> dict:
    """Switch active swipe. Returns {content, active_swipe, swipe_count} or empty."""
    conn = _connect()
    row = conn.execute("SELECT swipes FROM school_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row or not row[0]:
        conn.close()
        return {}
    try:
        swipes = json.loads(row[0])
    except Exception:
        conn.close()
        return {}
    if swipe_index < 0 or swipe_index >= len(swipes):
        conn.close()
        return {}
    conn.execute(
        "UPDATE school_messages SET active_swipe = ?, content = ? WHERE id = ? AND session_id = ?",
        (swipe_index, swipes[swipe_index], message_id, session_id),
    )
    conn.commit()
    conn.close()
    return {"content": swipes[swipe_index], "active_swipe": swipe_index, "swipe_count": len(swipes)}


def db_school_truncate_after(session_id: int, message_id: int) -> bool:
    """Delete all messages after the given message (seq > target_seq), keeping the target."""
    conn = _connect()
    row = conn.execute("SELECT seq FROM school_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row:
        conn.close()
        return False
    target_seq = row[0]
    _cleanup_image_files(conn, session_id, "school_messages", target_seq + 1)
    conn.execute("DELETE FROM school_messages WHERE session_id = ? AND seq > ?", (session_id, target_seq))
    conn.commit()
    conn.close()
    return True


def db_school_update_session_meta(session_id: int, persona_filename: str = None,
                                  title: str = None) -> None:
    """Update persona_filename and/or title on an existing school session."""
    conn = _connect()
    fields = []
    vals = []
    if persona_filename is not None:
        fields.append("persona_filename = ?")
        vals.append(persona_filename)
    if title is not None:
        fields.append("title = ?")
        vals.append(title)
    if fields:
        fields.append("updated_at = datetime('now')")
        vals.append(session_id)
        conn.execute(f"UPDATE school_sessions SET {', '.join(fields)} WHERE id = ?", vals)
        conn.commit()
    conn.close()


def db_school_update_settings(session_id: int, stack_config: str = None, response_style: str = None,
                              lorebooks: str = None, bg_image: str = None,
                              pov: str = None, inner_monologue: bool = None,
                              auto_continue: bool = None) -> None:
    conn = _connect()
    if stack_config is not None:
        conn.execute("UPDATE school_sessions SET stack_config = ?, updated_at = datetime('now') WHERE id = ?",
                     (stack_config, session_id))
    if response_style is not None:
        conn.execute("UPDATE school_sessions SET response_style = ?, updated_at = datetime('now') WHERE id = ?",
                     (response_style, session_id))
    if lorebooks is not None:
        conn.execute("UPDATE school_sessions SET lorebooks = ?, updated_at = datetime('now') WHERE id = ?",
                     (lorebooks, session_id))
    if bg_image is not None:
        conn.execute("UPDATE school_sessions SET bg_image = ?, updated_at = datetime('now') WHERE id = ?",
                     (bg_image, session_id))
    if pov is not None:
        conn.execute("UPDATE school_sessions SET pov = ?, updated_at = datetime('now') WHERE id = ?",
                     (pov, session_id))
    if inner_monologue is not None:
        conn.execute("UPDATE school_sessions SET inner_monologue = ?, updated_at = datetime('now') WHERE id = ?",
                     (1 if inner_monologue else 0, session_id))
    if auto_continue is not None:
        conn.execute("UPDATE school_sessions SET auto_continue = ?, updated_at = datetime('now') WHERE id = ?",
                     (1 if auto_continue else 0, session_id))
    conn.commit()
    conn.close()


def db_school_save_console_events(session_id: int, events_json: str):
    events_json = _cap_console_events(events_json)
    conn = _connect()
    conn.execute("UPDATE school_sessions SET console_events = ?, updated_at = datetime('now') WHERE id = ?",
                 (events_json, session_id))
    conn.commit()
    conn.close()


def db_school_count_messages(session_id: int) -> int:
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) FROM school_messages WHERE session_id = ?", (session_id,)).fetchone()
    conn.close()
    return row[0] if row else 0


def db_school_get_assistant_messages(session_id: int) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT id, content, analysis_json, is_first_mes FROM school_messages WHERE session_id = ? AND role = 'assistant' ORDER BY seq",
        (session_id,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        analysis = json.loads(r[2]) if r[2] else None
        result.append({"id": r[0], "content": r[1], "analysis": analysis, "is_first_mes": bool(r[3])})
    return result


def db_school_fork_session(session_id: int) -> Optional[dict]:
    sess = db_school_get_session(session_id)
    if not sess:
        return None
    conn = _connect()
    try:
        fork_title = sess['title'] + ' (fork)' if sess['title'] else 'Untitled (fork)'
        cur = conn.execute(
            """INSERT INTO school_sessions (title, card_filename, persona_filename, stack_config, console_events, lorebooks, pov, inner_monologue, bg_image, auto_continue, response_style)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fork_title, sess['card_filename'], sess['persona_filename'],
             sess.get('stack_config'), sess.get('console_events'), sess.get('lorebooks'),
             sess.get('pov'), sess.get('inner_monologue', 0), sess.get('bg_image'),
             1 if sess.get('auto_continue') else 0, sess.get('response_style')),
        )
        new_sid = cur.lastrowid
        msgs = conn.execute(
            "SELECT seq, role, content, is_first_mes, analysis_json, image_path, image_prompt, client_msg_id, seed, swipes, active_swipe FROM school_messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        for m in msgs:
            conn.execute(
                "INSERT INTO school_messages (session_id, seq, role, content, is_first_mes, analysis_json, image_path, image_prompt, client_msg_id, seed, swipes, active_swipe) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_sid, m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10]),
            )
        conn.commit()
        return {'new_session_id': new_sid, 'title': fork_title}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def db_school_set_persona(session_id: int, persona_filename: str) -> None:
    conn = _connect()
    conn.execute("UPDATE school_sessions SET persona_filename = ?, updated_at = datetime('now') WHERE id = ?",
                 (persona_filename, session_id))
    conn.commit()
    conn.close()


# ─── App ──────────────────────────────────────────────────────────

def db_school_to_rp(session_id: int) -> Optional[int]:
    """Convert a school session to an RP session (one-way).
    Copies card as single character, all messages (stripping analysis),
    stack_config, console_events, persona, and response_style.
    Returns new RP session ID or None on failure."""
    sess = db_school_get_session(session_id)
    if not sess:
        return None

    # Load card to get character name
    card_path = config.CHARACTERS_DIR / sess['card_filename']
    if not card_path.exists():
        return None
    try:
        card = json.loads(card_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    d = card.get('data', card)
    char_name = d.get('name', Path(sess['card_filename']).stem)

    # Fetch data needed before opening write transaction
    response_style = sess.get('response_style') or 'moderate'
    msgs = db_school_get_messages(session_id)

    conn = _connect()
    try:
        # Create RP session
        title = sess.get('title', char_name)
        cur = conn.execute(
            """INSERT INTO rp_sessions (title, persona_filename, turn_routing, response_style,
           stack_config, console_events, lorebooks, bg_image, pov, inner_monologue, auto_continue)
           VALUES (?, ?, 'auto', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, sess.get('persona_filename'), response_style,
             sess.get('stack_config'), sess.get('console_events'),
             sess.get('lorebooks'), sess.get('bg_image'), sess.get('pov'),
             sess.get('inner_monologue', 0), 1 if sess.get('auto_continue') else 0),
        )
        rp_sid = cur.lastrowid

        # Add single character
        conn.execute(
            "INSERT INTO rp_characters (session_id, card_filename, char_name, display_order) VALUES (?, ?, ?, 0)",
            (rp_sid, sess['card_filename'], char_name),
        )

        # Copy messages — map assistant→character, strip analysis_json
        for m in msgs:
            role = m['role']
            speaker = None
            if role == 'assistant':
                role = 'character'
                speaker = char_name
            _swipes = json.dumps(m['swipes']) if m.get('swipes') else None
            conn.execute(
                "INSERT INTO rp_messages (session_id, seq, role, speaker, content, persona_name, image_path, image_prompt, seed, swipes, active_swipe, client_msg_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rp_sid, m['seq'], role, speaker, m['content'], m.get('persona_name'),
                 m.get('image_path'), m.get('image_prompt'), m.get('seed'),
                 _swipes, m.get('active_swipe') or 0, m.get('client_msg_id')),
            )

        conn.commit()
        return rp_sid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
