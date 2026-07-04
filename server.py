"""
Character Card Testing App — main server
FastAPI backend that:
1. Loads/saves chara_card_v2 JSON files
2. Runs chat with an LLM as the character
3. Runs a separate analysis LLM call per response
4. Provides real-time rule compliance feedback
"""
APP_VERSION = "1.0.0"
import json
import os
import base64
import io
import re

# ─── Character Card Version Support ───────────────────────────────
# V3 field name aliases (V2 name → V3 name)
V3_FIELD_ALIASES = {
    "first_mes": "first_message",
    "mes_example": "message_examples",
}


def detect_card_version(card: dict) -> int:
    """Detect character card version: 1, 2, or 3."""
    spec = card.get("spec", "")
    if "v3" in spec:
        return 3
    elif "v2" in spec:
        return 2
    elif "data" in card:
        return 2  # V2 without explicit spec
    else:
        return 1  # V1 flat


def content_to_string(val) -> str:
    """Convert V3 content (string or array of content blocks) to plain string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        parts = []
        for block in val:
            if isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(val)


def get_card_field(d: dict, field: str, version: int = 2) -> str:
    """Get a field from card data, handling V3 name aliases and content arrays."""
    if version >= 3 and field in V3_FIELD_ALIASES:
        v3_name = V3_FIELD_ALIASES[field]
        if v3_name in d:
            return content_to_string(d[v3_name])
    return content_to_string(d.get(field))
import asyncio
import re
import sqlite3
from pathlib import Path
from typing import Optional
from json_repair import repair_json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI

# ─── Config ───────────────────────────────────────────────────────
# Load config from config.jsonc (supports comments)
def load_config() -> dict:
    """Load JSONC config file (JSON with comments)."""
    config_path = Path(__file__).parent / "config.jsonc"
    if not config_path.exists():
        return {}
    raw = config_path.read_text(encoding="utf-8")
    # Strip comments and trailing commas, but preserve strings
    # Simple state machine — walk char by char, track in-string state
    result = []
    i = 0
    n = len(raw)
    in_string = False
    while i < n:
        c = raw[i]
        if in_string:
            if c == '\\' and i + 1 < n:
                result.append(c)
                result.append(raw[i+1])
                i += 2
                continue
            if c == '"':
                in_string = False
            result.append(c)
        else:
            if c == '"':
                in_string = True
                result.append(c)
            elif c == '/' and i + 1 < n and raw[i+1] == '/':
                # Line comment — skip to end of line
                while i < n and raw[i] != '\n':
                    i += 1
                continue
            elif c == '/' and i + 1 < n and raw[i+1] == '*':
                # Block comment — skip to */
                i += 2
                while i + 1 < n and not (raw[i] == '*' and raw[i+1] == '/'):
                    i += 1
                i += 2
                continue
            else:
                result.append(c)
        i += 1
    cleaned = ''.join(result)
    # Remove trailing commas that would break JSON
    cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
    return json.loads(cleaned)


def save_config(cfg: dict):
    """Save config dict back to config.jsonc (pretty JSONC with comments)."""
    config_path = Path(__file__).parent / "config.jsonc"
    lines = []
    lines.append("// ─── Character School Configuration ───────────────────────────")
    lines.append("// JSONC format — comments are allowed (// and /* */")
    lines.append("// This file is managed by the Settings page; you can also edit it manually.")
    lines.append("//")
    lines.append("{")
    srv = cfg.get("server", {})
    lines.append('  // Server settings')
    lines.append('  "server": {')
    lines.append(f'    "host": {json.dumps(srv.get("host", "0.0.0.0"))},')
    lines.append(f'    "port": {int(srv.get("port", 7862))}')
    lines.append('  },')
    chat = cfg.get("chat", {})
    lines.append('')
    lines.append('  // ── Chat endpoint — drives the character\'s responses ──')
    lines.append('  "chat": {')
    lines.append('    "base_url": ' + json.dumps(str(chat.get("base_url", "https://openrouter.ai/api/v1"))) + ',')
    chat_key = chat.get("api_key")
    lines.append('    "api_key": ' + (json.dumps(chat_key) if chat_key else 'null') + ',')
    lines.append('    "model": ' + json.dumps(str(chat.get("model", "deepseek/deepseek-v4-flash"))) + ',')
    lines.append(f'    "temperature": {float(chat.get("temperature", 0.8))},')
    lines.append(f'    "max_tokens": {int(chat.get("max_tokens", 2000))},')
    lines.append(f'    "enable_thinking": {"true" if chat.get("enable_thinking", False) else "false"}')
    lines.append('  },')
    ana = cfg.get("analysis", {})
    lines.append('')
    lines.append('  // ── Analysis endpoint — checks responses & generates fix suggestions ──')
    lines.append('  "analysis": {')
    lines.append('    "base_url": ' + json.dumps(str(ana.get("base_url", "https://openrouter.ai/api/v1"))) + ',')
    ana_key = ana.get("api_key")
    lines.append('    "api_key": ' + (json.dumps(ana_key) if ana_key else 'null') + ',')
    lines.append('    "model": ' + json.dumps(str(ana.get("model", "deepseek/deepseek-v4-flash"))) + ',')
    lines.append(f'    "temperature": {float(ana.get("temperature", 0.1))},')
    lines.append(f'    "max_tokens": {int(ana.get("max_tokens", 1500))}')
    lines.append('  },')
    summ = cfg.get("summary", {})
    lines.append('')
    lines.append('  // ── Summary endpoint — summarizes RP conversation blocks ──')
    lines.append('  "summary": {')
    lines.append('    "base_url": ' + json.dumps(str(summ.get("base_url", "https://openrouter.ai/api/v1"))) + ',')
    summ_key = summ.get("api_key")
    lines.append('    "api_key": ' + (json.dumps(summ_key) if summ_key else 'null') + ',')
    lines.append('    "model": ' + json.dumps(str(summ.get("model", "deepseek/deepseek-v4-flash"))) + ',')
    lines.append(f'    "temperature": {float(summ.get("temperature", 0.3))},')
    lines.append(f'    "max_tokens": {int(summ.get("max_tokens", 1000))}')
    lines.append('  },')
    paths = cfg.get("paths", {})
    lines.append('')
    lines.append('  // Paths — can be absolute or relative to the app directory')
    lines.append('  "paths": {')
    _cdir = paths.get("characters_dir")
    lines.append('    "characters_dir": ' + (json.dumps(_cdir) if _cdir else 'null') + ', // null = defaults to ./characters inside app dir')
    pdir = paths.get("personas_dir")
    lines.append('    "personas_dir": ' + (json.dumps(pdir) if pdir else 'null') + '  // null = defaults to ./personas inside app dir')
    lines.append('  }')
    lines.append('}')
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def reload_config():
    """Reload config from disk and update all globals + LLM clients."""
    global _cfg, CHAT_BASE_URL, CHAT_API_KEY, CHAT_MODEL, CHAT_TEMPERATURE, CHAT_MAX_TOKENS, CHAT_ENABLE_THINKING
    global ANALYSIS_BASE_URL, ANALYSIS_API_KEY, ANALYSIS_MODEL, ANALYSIS_TEMPERATURE, ANALYSIS_MAX_TOKENS
    global SUMMARY_BASE_URL, SUMMARY_API_KEY, SUMMARY_MODEL, SUMMARY_TEMPERATURE, SUMMARY_MAX_TOKENS
    global chat_client, analysis_client, summary_client, CHARACTERS_DIR, PERSONAS_DIR
    _cfg = load_config()
    _chat_cfg = _cfg.get("chat", {})
    _analysis_cfg = _cfg.get("analysis", {})
    _summary_cfg = _cfg.get("summary", {})
    _chat_key = _chat_cfg.get("api_key")
    _analysis_key = _analysis_cfg.get("api_key")
    _summary_key = _summary_cfg.get("api_key")
    CHAT_BASE_URL    = os.environ.get("CHARACTERSCHOOL_CHAT_BASE_URL",    _chat_cfg.get("base_url", "https://openrouter.ai/api/v1"))
    CHAT_API_KEY     = os.environ.get("CHARACTERSCHOOL_CHAT_API_KEY",     _chat_key or os.environ.get("OPENROUTER_API_KEY", ""))
    CHAT_MODEL       = os.environ.get("CHARACTERSCHOOL_CHAT_MODEL",       _chat_cfg.get("model", "deepseek/deepseek-v4-flash"))
    CHAT_TEMPERATURE = _chat_cfg.get("temperature", 0.8)
    CHAT_MAX_TOKENS  = _chat_cfg.get("max_tokens", 2000)
    CHAT_ENABLE_THINKING = _chat_cfg.get("enable_thinking", False)
    ANALYSIS_BASE_URL    = os.environ.get("CHARACTERSCHOOL_ANALYSIS_BASE_URL",    _analysis_cfg.get("base_url", "https://openrouter.ai/api/v1"))
    ANALYSIS_API_KEY     = os.environ.get("CHARACTERSCHOOL_ANALYSIS_API_KEY",     _analysis_key or os.environ.get("OPENROUTER_API_KEY", ""))
    ANALYSIS_MODEL       = os.environ.get("CHARACTERSCHOOL_ANALYSIS_MODEL",       _analysis_cfg.get("model", "deepseek/deepseek-v4-flash"))
    ANALYSIS_TEMPERATURE = _analysis_cfg.get("temperature", 0.1)
    ANALYSIS_MAX_TOKENS  = _analysis_cfg.get("max_tokens", 1500)
    SUMMARY_BASE_URL    = os.environ.get("CHARACTERSCHOOL_SUMMARY_BASE_URL",    _summary_cfg.get("base_url", "https://openrouter.ai/api/v1"))
    SUMMARY_API_KEY     = os.environ.get("CHARACTERSCHOOL_SUMMARY_API_KEY",     _summary_key or os.environ.get("OPENROUTER_API_KEY", ""))
    SUMMARY_MODEL       = os.environ.get("CHARACTERSCHOOL_SUMMARY_MODEL",       _summary_cfg.get("model", "deepseek/deepseek-v4-flash"))
    SUMMARY_TEMPERATURE = _summary_cfg.get("temperature", 0.3)
    SUMMARY_MAX_TOKENS  = _summary_cfg.get("max_tokens", 1000)
    chat_client     = AsyncOpenAI(base_url=CHAT_BASE_URL,     api_key=CHAT_API_KEY or "not-configured")
    analysis_client = AsyncOpenAI(base_url=ANALYSIS_BASE_URL, api_key=ANALYSIS_API_KEY or "not-configured")
    summary_client  = AsyncOpenAI(base_url=SUMMARY_BASE_URL,  api_key=SUMMARY_API_KEY or "not-configured")
    _cdir = _cfg.get("paths", {}).get("characters_dir")
    CHARACTERS_DIR = Path(os.environ.get("CHARACTERS_DIR", _cdir if _cdir else str(Path(__file__).parent / "characters")))
    _pdir = _cfg.get("paths", {}).get("personas_dir")
    PERSONAS_DIR = Path(os.environ.get("PERSONAS_DIR", _pdir if _pdir else str(Path(__file__).parent / "personas")))


_cfg = load_config()

CHARACTERS_DIR = Path(os.environ.get(
    "CHARACTERS_DIR",
    _cfg.get("paths", {}).get("characters_dir") or str(Path(__file__).parent / "characters")
))
_PERSONAS_CFG = _cfg.get("paths", {}).get("personas_dir")
PERSONAS_DIR = Path(os.environ.get(
    "PERSONAS_DIR",
    _PERSONAS_CFG if _PERSONAS_CFG else str(Path(__file__).parent / "personas")
))
APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"

# ─── Dual LLM endpoints ────────────────────────────────────────────
# Chat endpoint drives character responses; analysis endpoint checks
# responses and generates fix suggestions. Each can use a different
# provider, model, API key, and base URL.
_chat_cfg = _cfg.get("chat", {})
_analysis_cfg = _cfg.get("analysis", {})

_chat_key = _chat_cfg.get("api_key")
_analysis_key = _analysis_cfg.get("api_key")

CHAT_BASE_URL    = os.environ.get("CHARACTERSCHOOL_CHAT_BASE_URL",    _chat_cfg.get("base_url", "https://openrouter.ai/api/v1"))
CHAT_API_KEY     = os.environ.get("CHARACTERSCHOOL_CHAT_API_KEY",     _chat_key or os.environ.get("OPENROUTER_API_KEY", ""))
CHAT_MODEL       = os.environ.get("CHARACTERSCHOOL_CHAT_MODEL",       _chat_cfg.get("model", "deepseek/deepseek-v4-flash"))
CHAT_TEMPERATURE = _chat_cfg.get("temperature", 0.8)
CHAT_MAX_TOKENS  = _chat_cfg.get("max_tokens", 2000)
CHAT_ENABLE_THINKING = _chat_cfg.get("enable_thinking", False)

ANALYSIS_BASE_URL    = os.environ.get("CHARACTERSCHOOL_ANALYSIS_BASE_URL",    _analysis_cfg.get("base_url", "https://openrouter.ai/api/v1"))
ANALYSIS_API_KEY     = os.environ.get("CHARACTERSCHOOL_ANALYSIS_API_KEY",     _analysis_key or os.environ.get("OPENROUTER_API_KEY", ""))
ANALYSIS_MODEL       = os.environ.get("CHARACTERSCHOOL_ANALYSIS_MODEL",       _analysis_cfg.get("model", "deepseek/deepseek-v4-flash"))
ANALYSIS_TEMPERATURE = _analysis_cfg.get("temperature", 0.1)
ANALYSIS_MAX_TOKENS  = _analysis_cfg.get("max_tokens", 1500)

# ─── LLM Clients ──────────────────────────────────────────────────
# Use a placeholder when no key is configured so the server still starts;
# the user can set real keys via the Settings page or config.jsonc.
chat_client     = AsyncOpenAI(base_url=CHAT_BASE_URL,     api_key=CHAT_API_KEY or "not-configured")
analysis_client = AsyncOpenAI(base_url=ANALYSIS_BASE_URL, api_key=ANALYSIS_API_KEY or "not-configured")

_summary_cfg = _cfg.get("summary", {})
_summary_key = _summary_cfg.get("api_key")
SUMMARY_BASE_URL    = os.environ.get("CHARACTERSCHOOL_SUMMARY_BASE_URL",    _summary_cfg.get("base_url", "https://openrouter.ai/api/v1"))
SUMMARY_API_KEY     = os.environ.get("CHARACTERSCHOOL_SUMMARY_API_KEY",     _summary_key or os.environ.get("OPENROUTER_API_KEY", ""))
SUMMARY_MODEL       = os.environ.get("CHARACTERSCHOOL_SUMMARY_MODEL",       _summary_cfg.get("model", "deepseek/deepseek-v4-flash"))
SUMMARY_TEMPERATURE = _summary_cfg.get("temperature", 0.3)
SUMMARY_MAX_TOKENS  = _summary_cfg.get("max_tokens", 1000)
summary_client  = AsyncOpenAI(base_url=SUMMARY_BASE_URL,  api_key=SUMMARY_API_KEY or "not-configured")

# ─── SQLite Database ─────────────────────────────────────────────
DB_PATH = APP_DIR / "char_test.db"


def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(str(DB_PATH))
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
            response_style  TEXT DEFAULT 'brief',
            summary_window  INTEGER DEFAULT 20,
            raw_window      INTEGER DEFAULT 10,
            summary_text    TEXT DEFAULT '',
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
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES rp_sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_messages_session ON rp_messages(session_id, seq)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_characters_session ON rp_characters(session_id, display_order)")
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
    conn.commit()
    conn.close()


init_db()


def db_create_session(card_filename: str, system_prompt: str, analysis_prompt: str,
                       persona_filename: str = None) -> int:
    """Create a new session row, return its id."""
    conn = sqlite3.connect(str(DB_PATH))
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
    conn = sqlite3.connect(str(DB_PATH))
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
    conn = sqlite3.connect(str(DB_PATH))
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
    conn = sqlite3.connect(str(DB_PATH))
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
    conn = sqlite3.connect(str(DB_PATH))
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
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()


def db_get_session(session_id: int) -> Optional[dict]:
    """Get session metadata."""
    conn = sqlite3.connect(str(DB_PATH))
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
DEFAULT_STACK_CONFIG = {
    "blocks": [
        {"type": "system", "enabled": True, "locked": True},
        {"type": "head", "enabled": True, "n": 2},
        {"type": "early_summary", "enabled": True, "start": 2, "end": 5, "text": ""},
        {"type": "raw_mid", "enabled": True, "start": 2, "end": 5},
        {"type": "late_summary", "enabled": True, "start": 5, "end": 8, "text": ""},
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
                                  all_msgs: list[dict], summary_text: str):
    """Build LLM messages from stack block configuration.
    Returns (llm_messages, block_markers) where block_markers is a list of
    {"block": type, "label": str, "start": int, "count": int}.
    Supports 7-block layout: system, head, early_summary, raw_mid,
    late_summary, custom, tail.
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
        """Append raw messages, return (start_index, count)."""
        start = len(llm_messages)
        for m in msgs:
            if m["role"] == "user":
                llm_messages.append({"role": "user", "content": m["content"]})
            elif m["role"] == "character":
                llm_messages.append({"role": "assistant", "content": f"[{m['speaker']}]: {m['content']}"})
            elif m["role"] == "system":
                llm_messages.append({"role": "system", "content": m["content"]})
        return start, len(llm_messages) - start

    for block in blocks:
        if not block.get("enabled", True) and not block.get("locked", False):
            continue

        btype = block.get("type", "")
        # Backward compat: old "summary" type → early_summary
        if btype == "summary":
            btype = "early_summary"

        labels = {
            "system": "System Prompt",
            "head": f"Head Messages (first {head_n})",
            "early_summary": f"Early Summary [{block.get('start','?')}-{block.get('end','?')}]",
            "raw_mid": f"Raw Mid [{block.get('start','?')}-{block.get('end','?')}]",
            "late_summary": f"Late Summary [{block.get('start','?')}-{block.get('end','?')}]",
            "custom": "Custom Inject",
            "tail": f"Raw Tail (last {tail_n})",
        }
        label = labels.get(btype, btype)

        if btype == "system":
            start = len(llm_messages)
            llm_messages.append({"role": "system", "content": system_prompt})
            block_markers.append({"block": btype, "label": label, "start": start, "count": 1})

        elif btype == "head" and head_n > 0:
            head_msgs = all_msgs[:head_n]
            s, c = _add_raw_msgs(head_msgs)
            if c > 0:
                block_markers.append({"block": btype, "label": label, "start": s, "count": c})

        elif btype == "early_summary":
            # Use block's own text, fall back to legacy summary_text
            text = block.get("text", "").strip()
            if not text and summary_text:
                text = summary_text.strip()
            if text:
                start = len(llm_messages)
                llm_messages.append({"role": "system", "content": f"SCENE SUMMARY (early):\n{text}"})
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

        elif btype == "late_summary":
            text = block.get("text", "").strip()
            if text:
                start = len(llm_messages)
                llm_messages.append({"role": "system", "content": f"SCENE SUMMARY (late):\n{text}"})
                block_markers.append({"block": btype, "label": label, "start": start, "count": 1})

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

    return llm_messages, block_markers


# ─── RP Database Functions ────────────────────────────────────────
def db_rp_create_session(characters: list[dict], persona_filename: str,
                         turn_routing: str, response_style: str,
                         summary_window: int, raw_window: int,
                         stack_config: str = None) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute(
        "INSERT INTO rp_sessions (title, persona_filename, turn_routing, response_style, summary_window, raw_window, stack_config) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (', '.join(c['name'] for c in characters), persona_filename, turn_routing, response_style, summary_window, raw_window, stack_config),
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


def db_rp_add_message(session_id: int, role: str, content: str, speaker: str = None) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM rp_messages WHERE session_id = ?", (session_id,)).fetchone()
    next_seq = row[0]
    cur = conn.execute(
        "INSERT INTO rp_messages (session_id, seq, role, speaker, content) VALUES (?, ?, ?, ?, ?)",
        (session_id, next_seq, role, speaker, content),
    )
    msg_id = cur.lastrowid
    conn.execute("UPDATE rp_sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return msg_id


def db_rp_get_messages(session_id: int) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT id, seq, role, speaker, content FROM rp_messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "seq": r[1], "role": r[2], "speaker": r[3], "content": r[4]} for r in rows]


def db_rp_get_session(session_id: int) -> Optional[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT id, title, persona_filename, turn_routing, response_style, summary_window, raw_window, summary_text, stack_config, console_events FROM rp_sessions WHERE id = ?",
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
        "summary_window": row[5], "raw_window": row[6], "summary_text": row[7],
        "stack_config": row[8] if len(row) > 8 else None,
        "console_events": row[9] if len(row) > 9 else None,
        "characters": [{"card_filename": c[0], "char_name": c[1], "display_order": c[2]} for c in chars],
    }


def db_rp_save_console_events(session_id: int, events_json: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE rp_sessions SET console_events = ?, updated_at = datetime('now') WHERE id = ?",
        (events_json, session_id),
    )
    conn.commit()
    conn.close()


def db_rp_list_sessions() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT id, title, persona_filename, turn_routing, response_style, updated_at FROM rp_sessions ORDER BY updated_at DESC",
    ).fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "persona_filename": r[2],
             "turn_routing": r[3], "response_style": r[4], "updated_at": r[5]} for r in rows]


def db_rp_delete_session(session_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM rp_messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM rp_characters WHERE session_id = ?", (session_id,))
    cur = conn.execute("DELETE FROM rp_sessions WHERE id = ?", (session_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def db_rp_delete_message(session_id: int, message_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT seq FROM rp_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row:
        conn.close()
        return False
    deleted_seq = row[0]
    conn.execute("DELETE FROM rp_messages WHERE session_id = ? AND seq >= ?", (session_id, deleted_seq))
    conn.commit()
    conn.close()
    return True


def db_rp_update_summary(session_id: int, summary_text: str) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE rp_sessions SET summary_text = ?, updated_at = datetime('now') WHERE id = ?", (summary_text, session_id))
    conn.commit()
    conn.close()


def db_rp_count_messages(session_id: int) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT COUNT(*) FROM rp_messages WHERE session_id = ?", (session_id,)).fetchone()
    conn.close()
    return row[0] if row else 0


def db_rp_update_settings(session_id: int, turn_routing: str = None, response_style: str = None,
                          summary_window: int = None, raw_window: int = None,
                          stack_config: str = None) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    updates = []
    params = []
    if turn_routing is not None:
        updates.append("turn_routing = ?")
        params.append(turn_routing)
    if response_style is not None:
        updates.append("response_style = ?")
        params.append(response_style)
    if summary_window is not None:
        updates.append("summary_window = ?")
        params.append(summary_window)
    if raw_window is not None:
        updates.append("raw_window = ?")
        params.append(raw_window)
    if stack_config is not None:
        updates.append("stack_config = ?")
        params.append(stack_config)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(session_id)
        conn.execute(f"UPDATE rp_sessions SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    conn.close()


def db_rp_set_persona(session_id: int, persona_filename: str) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE rp_sessions SET persona_filename = ?, updated_at = datetime('now') WHERE id = ?", (persona_filename, session_id))
    conn.commit()
    conn.close()


# ─── App ──────────────────────────────────────────────────────────
app = FastAPI(title="Character Card Tester")


# ─── Persona Management ───────────────────────────────────────────
def list_personas() -> list[dict]:
    """List all persona files."""
    personas = []
    for p in sorted(PERSONAS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            personas.append({
                "filename": p.name,
                "name": data.get("name", p.stem),
                "tags": data.get("tags", []),
                "traits": data.get("traits", {}),
                "what_it_tests": data.get("what_it_tests", ""),
            })
        except (json.JSONDecodeError, KeyError):
            personas.append({"filename": p.name, "name": p.stem, "tags": [], "traits": {}, "what_it_tests": "", "error": True})
    return personas


def load_persona(filename: str) -> dict:
    """Load a persona by filename."""
    path = PERSONAS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Persona not found: {filename}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_persona(filename: str, data: dict) -> None:
    """Save a persona by filename."""
    path = PERSONAS_DIR / filename
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def build_persona_context(persona: dict) -> str:
    """Build the persona context string to inject into the character LLM."""
    parts = []
    parts.append(f"THE PERSON YOU ARE INTERACTING WITH:")
    parts.append(f"Name: {persona.get('name', 'Unknown')}")
    if persona.get("physical_description"):
        parts.append(f"Physical appearance: {persona['physical_description']}")
    if persona.get("personality"):
        parts.append(f"Personality: {persona['personality']}")
    if persona.get("behavior_hints"):
        parts.append(f"How they behave: {persona['behavior_hints']}")
    parts.append("")
    parts.append("IMPORTANT: The user's messages are spoken/acted by this person. React to the user's words AND to who this person is — their appearance, their energy, their physical presence. You can notice their body, their posture, their hands, the way they move — even if the user doesn't describe these things, because this is who they are. Your character should respond to the physical and social reality of this person, not just their words.")
    return "\n".join(parts)


def build_analysis_persona_context(persona: dict) -> str:
    """Build persona context for the analysis LLM."""
    return f"""PERSONA USED IN THIS SESSION:
Name: {persona.get('name', 'Unknown')}
Physical: {persona.get('physical_description', '')}
Personality: {persona.get('personality', '')}
Behavior: {persona.get('behavior_hints', '')}
What it tests: {persona.get('what_it_tests', '')}

When assessing the character's response, consider the persona's pressure level. Guard holding against a highly attractive/pressuring persona is more significant than holding against a neutral one. Desire leaking toward a shy non-pursuing persona is more significant than leaking toward someone actively pursuing. Factor the persona's traits into your assessment of fragility and leakage."""


# ─── Card Management ──────────────────────────────────────────────
def list_cards() -> list[dict]:
    """List all character cards in the characters directory."""
    cards = []
    for p in sorted(CHARACTERS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            d = data.get("data", data)
            version = detect_card_version(data)
            cards.append({
                "filename": p.name,
                "name": d.get("name", p.stem),
                "tags": d.get("tags", []),
                "size": p.stat().st_size,
                "version": version,
            })
        except (json.JSONDecodeError, KeyError):
            cards.append({"filename": p.name, "name": p.stem, "tags": [], "size": p.stat().st_size, "error": True})
    return cards


def load_card(filename: str) -> dict:
    """Load a character card by filename."""
    path = CHARACTERS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Card not found: {filename}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_card(filename: str, data: dict) -> None:
    """Save a character card by filename."""
    path = CHARACTERS_DIR / filename
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_char_name(card: dict) -> str:
    """Resolve the character name based on the card's char_name_mode setting.
    
    'full'  (default) — use the full name (e.g. 'Farah Surihani')
    'first'           — use only the first name (e.g. 'Farah')
    """
    d = card.get("data", card)
    full_name = d.get("name", "Character") or "Character"
    mode = d.get("char_name_mode", "full") or "full"
    parts = full_name.split()
    if mode == "first":
        return parts[0] if parts else "Character"
    if mode == "last":
        return parts[-1] if len(parts) > 1 else full_name
    return full_name


def substitute_macros(text: str, char_name: str, user_name: str = "User") -> str:
    """Replace {{char}} and {{user}} placeholders with actual names."""
    if not text:
        return text
    return text.replace("{{char}}", char_name).replace("{{user}}", user_name)


def build_system_prompt(card: dict, user_name: str = "User") -> str:
    """Build the system prompt for the character LLM from any version card."""
    d = card.get("data", card)
    version = detect_card_version(card)
    char_name = resolve_char_name(card)
    parts = []
    # System prompt is the core
    val = get_card_field(d, "system_prompt", version)
    if val:
        parts.append(substitute_macros(val, char_name, user_name))
    # Description adds character context
    val = get_card_field(d, "description", version)
    if val:
        parts.append(f"\n\nCHARACTER DESCRIPTION:\n{substitute_macros(val, char_name, user_name)}")
    # Personality
    val = get_card_field(d, "personality", version)
    if val:
        parts.append(f"\n\nPERSONALITY:\n{substitute_macros(val, char_name, user_name)}")
    # Scenario
    val = get_card_field(d, "scenario", version)
    if val:
        parts.append(f"\n\nSCENARIO:\n{substitute_macros(val, char_name, user_name)}")
    # Post-history instructions (always-on reminders)
    val = get_card_field(d, "post_history_instructions", version)
    if val:
        parts.append(f"\n\nALWAYS REMEMBER:\n{substitute_macros(val, char_name, user_name)}")
    # Mes example for voice reference
    val = get_card_field(d, "mes_example", version)
    if val:
        parts.append(f"\n\nEXAMPLE DIALOGUE (for voice reference):\n{substitute_macros(val, char_name, user_name)}")
    return "\n".join(parts)


def build_analysis_prompt(card: dict) -> str:
    """Build the system prompt for the analysis LLM."""
    d = card.get("data", card)
    version = detect_card_version(card)
    char_name = resolve_char_name(card)
    # Gather all the rules from the card
    rules_text = []
    for field in ["system_prompt", "post_history_instructions", "description", "personality"]:
        val = get_card_field(d, field, version)
        if val:
            rules_text.append(f"--- {field.upper()} ---\n{substitute_macros(val, char_name)}")

    return f"""You are a QA analysis engine for character card testing. You run alongside a live chat session where a human user is interacting with an LLM playing a character. Your job is to analyze each character response in real-time and check it against the character's rules.

CHARACTER RULES TO CHECK AGAINST:
{chr(10).join(rules_text)}

YOUR TASK:
For each character response, analyze it and return a JSON object with these fields:

{{
  "banned_words": ["list of any banned words found in the response, empty if none"],
  "banned_word_context": ["the sentence where each banned word appeared"],
  "required_patterns_present": {{"pattern_name": true/false}},
  "required_patterns_missing": ["list of required patterns that should have appeared but didn't"],
  "voice_consistency": "consistent / slight_drift / significant_drift",
  "voice_notes": "brief note on voice quality",
  "arc_status": "not_triggered / building / triggered / violated_sequence",
  "arc_notes": "brief note on arc mechanics if applicable",
  "internal_state": {{
    "expressed": "what the character showed externally",
    "implied": "what was leaking through despite the guard",
    "conflict": "none / mild / moderate / strong / breaking_point"
  }},
  "leakage": {{
    "detected": true/false,
    "details": "what desire/emotion leaked through the guard, if anything"
  }},
  "rule_violations": ["list of specific rule violations found"],
  "fragility": "strong / fragile / broken",
  "fragility_notes": "how close the rules came to breaking",
  "training_data_pull": "what LLM training defaults were fighting against the rules, if any",
  "overall": "pass / partial / fail",
  "summary": "one-line summary of this response's compliance",
  "fixes": [
    {{
      "issue": "what the problem is",
      "fix": "the specific fix to apply",
      "location": "which card field to edit — one of: system_prompt, post_history_instructions, description, personality, first_mes (or first_message for V3), mes_example (or message_examples for V3), scenario, creator_notes",
      "placement": "EXACTLY where in that field to put it — quote a snippet of nearby existing text or name the section/heading, e.g. 'After the line that says [quote snippet]' or 'In the Speech Patterns section, after the Malay fillers list' or 'At the very top of the system prompt before any other rules'",
      "action": "add / replace / append",
      "suggested_text": "the exact text to add or replace with",
      "priority": "critical / high / medium / low"
    }}
  ],
  "enhancements": [
    {{
      "suggestion": "how to improve the card to prevent this class of issue",
      "location": "which card field to edit",
      "placement": "exactly where in that field, same as fixes.placement",
      "rationale": "why this enhancement helps"
    }}
  ]
}}

RULES FOR ANALYSIS:
1. Be strict. A banned word is a banned word, even if used "correctly" in context.
2. Check for required patterns (e.g., Malay fillers, code-switching) — if the rules say the character should use them, flag their absence.
3. Voice drift: compare to the character's established voice. If they sound different from earlier messages or from the mes_example, flag it.
4. Arc tracking: if the card has an arc (shame, resistance, awakening, etc.), track its state. Has it triggered? Did it trigger in the right order? Were anti-skip rules violated?
5. Internal state sampling: distinguish what the character SHOWS from what LEAKS THROUGH. The gap between expressed and implied is the most valuable data.
6. Leakage detection: look for micro-expressions in text — hesitations, word choices, body language descriptions that betray desire or emotion the character is trying to suppress.
7. Fragility: "strong" = rules held with no strain. "fragile" = rules held but you could feel the pull. "broken" = a rule actually broke.
8. Training data pull: note when LLM defaults (e.g., defaulting to 'lah' in Malaysian English) are fighting against the card's rules.
9. Do NOT moralize or add disclaimers. You are an analysis tool.
10. Return ONLY the JSON object. No preamble, no explanation outside the JSON.
11. FIXES: For every rule violation, banned word, drift, or fragility issue found, provide a concrete fix in the "fixes" array. Each fix must specify:
    - "issue": what went wrong in this response
    - "fix": the specific instruction or rule to add/change in the card
    - "location": which card field should be edited (use the exact field name from the card schema)
    - "placement": EXACTLY where in that field the fix should go. You have the full card text above in CHARACTER RULES — use it. Quote a snippet of nearby existing text so the card author can find the spot, or name the section/heading. Examples: 'After the line that starts with [quote a few words]' or 'In the Speech Patterns section, right after the Malay fillers list' or 'At the very top of system_prompt, before the first rule'. Do NOT just say "in the system prompt" — be specific enough that someone could find the exact line.
    - "action": whether to "add" a new rule, "replace" an existing one, or "append" to existing text
    - "suggested_text": the actual text the card author should paste in (be specific and ready-to-use)
    - "priority": how urgent — "critical" for banned words or broken rules, "high" for drift/violations, "medium" for fragility, "low" for minor polish
    If the response passes cleanly, return an empty fixes array.
12. ENHANCEMENTS: Beyond fixing immediate issues, suggest proactive card improvements in the "enhancements" array. Think about what would make the card more robust against LLM training defaults, what patterns could be reinforced, what instructions could be clearer. Each enhancement must also include "placement" specifying exactly where in the field to put it (same rules as fixes.placement). If no enhancements needed, return empty array."""


def get_first_mes(card: dict, user_name: str = "User") -> Optional[str]:
    """Get the first message / greeting from the card (V1/V2/V3)."""
    d = card.get("data", card)
    version = detect_card_version(card)
    val = get_card_field(d, "first_mes", version)
    if not val:
        return None
    char_name = resolve_char_name(card)
    return substitute_macros(val, char_name, user_name)


# ─── RP Prompt Building ───────────────────────────────────────────
def build_rp_system_prompt(cards: list[dict], persona: dict = None,
                           turn_routing: str = 'auto', response_style: str = 'brief',
                           directed_character: str = None) -> str:
    """Build system prompt for multi-character RP."""
    parts = []
    parts.append("You are roleplaying as multiple characters in a shared scene. The user is a participant in the scene.")
    parts.append("")
    parts.append("CHARACTERS IN THIS SCENE:")
    parts.append("")

    # Get persona name for {{user}} substitution
    _user_name = persona.get("name", "User") if persona else "User"

    if directed_character:
        # Directed mode: full detail for the target character, brief listing for others
        directed_lower = directed_character.lower()
        for card in cards:
            d = card.get("data", card)
            version = detect_card_version(card)
            name = d.get("name", "Unknown")
            char_name = resolve_char_name(card)
            if name.lower() == directed_lower:
                parts.append(f"=== YOU ARE {name} ===")
                val = get_card_field(d, "system_prompt", version)
                if val:
                    parts.append(substitute_macros(val, char_name, _user_name))
                val = get_card_field(d, "description", version)
                if val:
                    parts.append(f"CHARACTER DESCRIPTION: {substitute_macros(val, char_name, _user_name)}")
                val = get_card_field(d, "personality", version)
                if val:
                    parts.append(f"PERSONALITY: {substitute_macros(val, char_name, _user_name)}")
                val = get_card_field(d, "mes_example", version)
                if val:
                    parts.append(f"EXAMPLE DIALOGUE (for voice reference): {substitute_macros(val, char_name, _user_name)}")
                val = get_card_field(d, "post_history_instructions", version)
                if val:
                    parts.append(f"ALWAYS REMEMBER: {substitute_macros(val, char_name, _user_name)}")
                parts.append("")
            else:
                # Brief context only — just enough to know who else is in the scene
                desc = get_card_field(d, "description", version) or ""
                short = desc[:200] + "..." if len(desc) > 200 else desc
                parts.append(f"OTHER CHARACTER IN SCENE: {name}" + (f" — {short}" if short else ""))
        parts.append("")
    else:
        # Auto mode: full detail for all characters
        for card in cards:
            d = card.get("data", card)
            version = detect_card_version(card)
            name = d.get("name", "Unknown")
            char_name = resolve_char_name(card)
            parts.append(f"--- {name} ---")
            val = get_card_field(d, "system_prompt", version)
            if val:
                parts.append(substitute_macros(val, char_name, _user_name))
            val = get_card_field(d, "description", version)
            if val:
                parts.append(f"CHARACTER DESCRIPTION: {substitute_macros(val, char_name, _user_name)}")
            val = get_card_field(d, "personality", version)
            if val:
                parts.append(f"PERSONALITY: {substitute_macros(val, char_name, _user_name)}")
            val = get_card_field(d, "mes_example", version)
            if val:
                parts.append(f"EXAMPLE DIALOGUE (for voice reference): {substitute_macros(val, char_name, _user_name)}")
            val = get_card_field(d, "post_history_instructions", version)
            if val:
                parts.append(f"ALWAYS REMEMBER: {substitute_macros(val, char_name, _user_name)}")
            parts.append("")

    # Turn routing
    parts.append("RESPONSE FORMAT:")
    parts.append("- Start each character's response with [CharacterName]: followed by their dialogue and actions.")
    parts.append("- Example: [Lisa]: *looks up from her book* What did you say?")
    if turn_routing == 'auto':
        parts.append("- You choose which character(s) respond naturally. Multiple characters can respond in sequence, each with their own [Name]: prefix on a new line.")
        parts.append("- Characters can react to the user AND to each other.")
    else:  # directed
        if directed_character:
            parts.append(f"** CRITICAL: You must respond ONLY as {directed_character}. **")
            parts.append(f"- Write {directed_character}'s dialogue and actions only.")
            parts.append(f"- Do NOT write dialogue for any other character.")
            parts.append(f"- Stay fully in {directed_character}'s voice and personality.")
            parts.append(f"- If another character would react, you may briefly note it in {directed_character}'s POV, but do not write their dialogue.")
        else:
            parts.append("- Only respond as the directed character. Do not write dialogue for other characters.")
    parts.append("")

    # Response style
    if response_style == 'brief':
        parts.append("RESPONSE STYLE: Keep each character's response to 1-2 short sentences. Be snappy and reactive. Prioritize dialogue over description.")
    else:
        parts.append("RESPONSE STYLE: Respond with a full paragraph per character. Include actions, internal thoughts, body language, and emotional detail.")
    parts.append("")

    # Persona
    if persona:
        parts.append(build_persona_context(persona))

    return "\n".join(parts)


def parse_rp_response(raw_content: str, character_names: dict, character_order: list) -> list[dict]:
    """Parse [CharacterName]: content format from LLM response."""
    import re
    responses = []

    # Build a mapping of name -> filename (case-insensitive)
    name_to_filename = {}
    for fn in character_order:
        name = character_names[fn]
        name_to_filename[name.lower()] = {"filename": fn, "name": name}

    # Pattern: [Name]: content (until next [Name]: or end)
    pattern = r'\[([^\]]+)\]:\s*'
    matches = list(re.finditer(pattern, raw_content))

    if not matches:
        return []

    for i, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_content)
        content = raw_content[start:end].strip()

        # Find matching character
        lookup = name_to_filename.get(name.lower())
        if lookup:
            responses.append({"filename": lookup["filename"], "name": lookup["name"], "content": content})
        else:
            # Try partial match
            found = False
            for lower_name, info in name_to_filename.items():
                if name.lower() in lower_name or lower_name in name.lower():
                    responses.append({"filename": info["filename"], "name": info["name"], "content": content})
                    found = True
                    break
            if not found:
                # Unknown character — still include
                responses.append({"filename": None, "name": name, "content": content})

    return responses


async def rp_summarize(session_id: int, messages_to_summarize: list[dict],
                       existing_summary: str, ws: WebSocket = None) -> str:
    """Summarize older messages using the analysis LLM."""
    convo = []
    for m in messages_to_summarize:
        speaker = m.get("speaker") or m["role"]
        convo.append(f"[{speaker}]: {m['content']}")
    summary_input = "\n".join(convo)

    prompt = "Summarize the following roleplay scene conversation. Focus on: emotional states, relationship dynamics, key events, character development, and any important context that would be needed to continue the scene naturally. Write it as a narrative summary, not a list."
    if existing_summary:
        prompt += f"\n\nPREVIOUS SUMMARY (incorporate and update):\n{existing_summary}"
    prompt += f"\n\nCONVERSATION TO SUMMARIZE:\n{summary_input}"

    messages = [
        {"role": "system", "content": "You are a roleplay scene summarizer. Be concise but capture emotional and relational detail. Write in present tense."},
        {"role": "user", "content": prompt},
    ]

    if ws:
        await ws.send_json({
            "type": "console_event", "event": "request", "llm": "summary",
            "model": SUMMARY_MODEL, "label": "Summarization",
            "temperature": SUMMARY_TEMPERATURE, "max_tokens": SUMMARY_MAX_TOKENS,
            "messages": messages, "timestamp": _now_iso(),
        })

    try:
        resp = await summary_client.chat.completions.create(
            model=SUMMARY_MODEL, messages=messages,
            temperature=SUMMARY_TEMPERATURE, max_tokens=SUMMARY_MAX_TOKENS,
        )
        summary = resp.choices[0].message.content.strip()
        usage = resp.usage
        if ws:
            await ws.send_json({
                "type": "console_event", "event": "response", "llm": "summary",
                "model": SUMMARY_MODEL, "label": "Summarization",
                "content": summary,
                "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                "finish_reason": resp.choices[0].finish_reason, "timestamp": _now_iso(),
            })
        return summary
    except Exception as e:
        if ws:
            await ws.send_json({"type": "error", "message": f"Summarization error: {e}"})
        return existing_summary


# ─── API Routes ───────────────────────────────────────────────────
@app.get("/")
async def index():
    """Serve the main HTML page with cache-busting headers."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )


@app.get("/api/cards")
async def api_list_cards():
    return JSONResponse(list_cards())


@app.get("/api/cards/{filename}")
async def api_get_card(filename: str):
    try:
        card = load_card(filename)
        return JSONResponse(card)
    except FileNotFoundError:
        return JSONResponse({"error": "Card not found"}, status_code=404)
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)


@app.delete("/api/cards/{filename}")
async def api_delete_card(filename: str):
    """Delete a character card JSON and its associated avatar PNG."""
    path = CHARACTERS_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "Card not found"}, status_code=404)
    try:
        path.unlink()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    # Also delete avatar PNG if it exists
    stem = Path(filename).stem
    avatar_path = CHARACTERS_DIR / f"{stem}_avatar.png"
    if avatar_path.exists():
        try:
            avatar_path.unlink()
        except Exception:
            pass  # non-fatal if avatar deletion fails
    return JSONResponse({"status": "deleted", "filename": filename})


@app.put("/api/cards/{filename}")
async def api_save_card(filename: str, data: dict):
    try:
        save_card(filename, data)
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
    chunks = _parse_png_text_chunks(png_bytes)
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


@app.post("/api/cards/upload")
async def api_upload_card(file: UploadFile = File(...)):
    """Upload a character card JSON or PNG file."""
    try:
        content = await file.read()
        filename_in = file.filename or ""

        # Detect PNG files by magic bytes or extension
        is_png = content[:8] == b'\\x89PNG\\r\\n\\x1a\\n' or filename_in.lower().endswith(".png")

        if is_png:
            # Extract chara JSON from PNG tEXt/iTXt chunk
            data = extract_chara_from_png(content)
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

        save_card(filename, data)

        # If it was a PNG, also save the avatar image
        if is_png:
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(content))
                avatar_path = CHARACTERS_DIR / f"{safe_name}_avatar.png"
                # Save just the image without metadata to keep it small
                img.convert("RGBA").save(str(avatar_path), format="PNG")
            except Exception:
                pass  # avatar save is best-effort

        return JSONResponse({"status": "uploaded", "filename": filename, "name": name, "format": "png" if is_png else "json"})
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/cards/{filename}/png")
async def api_download_card_png(filename: str):
    """Download a character card as a PNG with embedded chara metadata."""
    try:
        path = CHARACTERS_DIR / filename
        if not path.exists():
            return JSONResponse({"error": "Card not found"}, status_code=404)
        data = json.loads(path.read_text(encoding="utf-8"))

        # Check for an existing avatar image
        stem = path.stem
        avatar_bytes = None
        avatar_path = CHARACTERS_DIR / f"{stem}_avatar.png"
        if avatar_path.exists():
            avatar_bytes = avatar_path.read_bytes()

        png_bytes = create_chara_png(data, avatar_bytes)

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


@app.get("/api/cards/{filename}/json")
async def api_download_card_json(filename: str):
    """Download a character card as raw JSON."""
    try:
        path = CHARACTERS_DIR / filename
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


@app.post("/api/cards/{filename}/validate")
async def api_validate_card(filename: str, data: dict):
    """Validate a card's JSON structure and check for common issues."""
    issues = []
    d = data.get("data", data)
    version = detect_card_version(data)

    # Check required fields
    required = ["name", "description", "system_prompt"]
    for field in required:
        val = get_card_field(d, field, version)
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
@app.get("/api/personas")
async def api_list_personas():
    return JSONResponse(list_personas())


@app.get("/api/personas/{filename}")
async def api_get_persona(filename: str):
    try:
        persona = load_persona(filename)
        return JSONResponse(persona)
    except FileNotFoundError:
        return JSONResponse({"error": "Persona not found"}, status_code=404)
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)


@app.put("/api/personas/{filename}")
async def api_save_persona(filename: str, data: dict):
    try:
        save_persona(filename, data)
        return JSONResponse({"status": "saved", "filename": filename})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/personas/upload")
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

        save_persona(filename, data)
        return JSONResponse({"status": "uploaded", "filename": filename, "name": name})
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/personas/{filename}/validate")
async def api_validate_persona(filename: str, data: dict):
    """Validate a persona's structure."""
    issues = []
    required = ["name", "physical_description", "personality"]
    for field in required:
        if not data.get(field):
            issues.append({"severity": "error", "field": field, "message": f"Missing required field: {field}"})
    if not data.get("traits"):
        issues.append({"severity": "warning", "field": "traits", "message": "No traits specified — trait-based analysis won't work"})
    return JSONResponse({"valid": len([i for i in issues if i["severity"] == "error"]) == 0, "issues": issues})


# ─── WebSocket Chat ───────────────────────────────────────────────
@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    """
    WebSocket protocol:
    → {"type": "start", "card_filename": "neelofa.json"}
    ← {"type": "character_message", "content": "...", "analysis": {...}}
    → {"type": "user_message", "content": "..."}
    ← {"type": "character_message", "content": "...", "analysis": {...}}
    → {"type": "reset"}
    ← {"type": "character_message", "content": "...", "analysis": {...}}  (first_mes)
    → {"type": "get_report"}
    ← {"type": "report", "content": {...}}
    """
    await ws.accept()

    card = None
    card_filename = None
    persona = None
    persona_filename = None
    system_prompt = ""
    analysis_prompt = ""
    session_id = None  # SQLite session row id
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
                        await ws.send_json({"type": "analysis_typing_stopped"})
                        await ws.send_json({"type": "generation_stopped"})
                    except Exception:
                        pass
                continue

            elif data["type"] == "start":
                card_filename = data["card_filename"]
                persona_filename = data.get("persona_filename")
                try:
                    card = load_card(card_filename)

                    # Load persona if provided (needed for {{user}} substitution)
                    if persona_filename:
                        try:
                            persona = load_persona(persona_filename)
                            _user_name = persona.get("name", "User")
                        except Exception as pe:
                            await ws.send_json({"type": "error", "message": f"Persona error: {pe}"})
                            persona = None
                            _user_name = "User"
                    else:
                        persona = None
                        _user_name = "User"

                    system_prompt = build_system_prompt(card, user_name=_user_name)
                    analysis_prompt = build_analysis_prompt(card)

                    if persona:
                        persona_context = build_persona_context(persona)
                        system_prompt = system_prompt + "\n\n" + persona_context
                        analysis_prompt = analysis_prompt + "\n\n" + build_analysis_persona_context(persona)

                    # Create SQLite session
                    session_id = db_create_session(card_filename, system_prompt, analysis_prompt, persona_filename)

                    # Send session_started so the UI enables input
                    await ws.send_json({"type": "session_started", "card": card_filename, "persona": persona_filename})

                    # Send first_mes if available — NO analysis, just display it.
                    first_mes = get_first_mes(card, user_name=_user_name)
                    if first_mes:
                        msg_id = db_add_message(session_id, "assistant", first_mes, is_first_mes=True)

                        await ws.send_json({
                            "type": "character_message",
                            "content": first_mes,
                            "analysis": None,
                            "is_first_mes": True,
                            "message_id": msg_id,
                        })
                except Exception as e:
                    await ws.send_json({"type": "error", "message": str(e)})

            elif data["type"] == "user_message":
                user_content = data["content"]
                user_msg_id = db_add_message(session_id, "user", user_content)

                # Send the user message ID back so the frontend can tag the DOM node
                await ws.send_json({"type": "user_message_stored", "message_id": user_msg_id})

                # Get character response
                async def _school_gen():
                    try:
                        # Build LLM context from SQLite
                        llm_messages = db_get_llm_messages(session_id)

                        # ── Notify frontend: character is responding ──
                        await ws.send_json({"type": "character_typing", "character_name": card.get("data", card).get("name", "Character")})

                        # ── Console: log the request ──
                        await ws.send_json({
                            "type": "console_event",
                            "event": "request",
                            "llm": "character",
                            "model": CHAT_MODEL,
                            "temperature": CHAT_TEMPERATURE,
                            "max_tokens": CHAT_MAX_TOKENS,
                            "messages": [{"role": m["role"], "content": m["content"]} for m in llm_messages],
                            "timestamp": _now_iso(),
                        })

                        resp = await chat_client.chat.completions.create(
                            model=CHAT_MODEL,
                            messages=llm_messages,
                            temperature=CHAT_TEMPERATURE,
                            max_tokens=CHAT_MAX_TOKENS,
                            extra_body={"enable_thinking": CHAT_ENABLE_THINKING},
                        )
                        char_content = resp.choices[0].message.content
                        usage = resp.usage

                        await ws.send_json({"type": "character_typing_stopped"})

                        # ── Console: log the response ──
                        await ws.send_json({
                            "type": "console_event",
                            "event": "response",
                            "llm": "character",
                            "model": CHAT_MODEL,
                            "content": char_content,
                            "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                            "finish_reason": resp.choices[0].finish_reason,
                            "timestamp": _now_iso(),
                        })

                        # Get previous assistant responses for drift comparison (before adding the new one)
                        prev_assistants = db_get_assistant_messages(session_id)
                        prev_texts = [m["content"] for m in prev_assistants]  # includes first_mes + prior responses

                        # ── Notify frontend: analyzer is running ──
                        await ws.send_json({"type": "analysis_typing"})

                        # Run analysis
                        analysis = await analyze_response(
                            analysis_prompt, char_content, prev_texts, card, ws=ws
                        )

                        await ws.send_json({"type": "analysis_typing_stopped"})

                        # Store in SQLite with analysis
                        analysis_str = json.dumps(analysis) if analysis else None
                        msg_id = db_add_message(session_id, "assistant", char_content, is_first_mes=False, analysis_json=analysis_str)

                        await ws.send_json({
                            "type": "character_message",
                            "content": char_content,
                            "analysis": analysis,
                            "is_first_mes": False,
                            "message_id": msg_id,
                        })
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        await ws.send_json({"type": "character_typing_stopped"})
                        await ws.send_json({"type": "analysis_typing_stopped"})
                        await ws.send_json({"type": "error", "message": f"LLM error: {e}"})

                current_gen_task = asyncio.create_task(_school_gen())
                continue  # back to main receive_json loop (handles stop)

            elif data["type"] == "set_persona":
                # Change persona mid-session (will rebuild system prompt and reset)
                persona_filename = data.get("persona_filename")
                if card:
                    if persona_filename:
                        try:
                            persona = load_persona(persona_filename)
                            _user_name = persona.get("name", "User")
                        except Exception as pe:
                            await ws.send_json({"type": "error", "message": f"Persona error: {pe}"})
                            persona = None
                            _user_name = "User"
                    else:
                        persona = None
                        _user_name = "User"

                    system_prompt = build_system_prompt(card, user_name=_user_name)
                    analysis_prompt = build_analysis_prompt(card)
                    if persona:
                        system_prompt = system_prompt + "\n\n" + build_persona_context(persona)
                        analysis_prompt = analysis_prompt + "\n\n" + build_analysis_persona_context(persona)

                    # Create new SQLite session for the new persona
                    session_id = db_create_session(card_filename, system_prompt, analysis_prompt, persona_filename)

                    # Send persona_changed so the client clears the chat
                    await ws.send_json({"type": "persona_changed", "persona": persona_filename})

                    # Send first_mes — NO analysis, just display
                    first_mes = get_first_mes(card, user_name=_user_name)
                    if first_mes:
                        msg_id = db_add_message(session_id, "assistant", first_mes, is_first_mes=True)
                        await ws.send_json({
                            "type": "character_message",
                            "content": first_mes,
                            "analysis": None,
                            "is_first_mes": True,
                            "message_id": msg_id,
                        })
                else:
                    await ws.send_json({"type": "error", "message": "No card loaded"})

            elif data["type"] == "reset":
                if card:
                    # Create new SQLite session for the reset
                    session_id = db_create_session(card_filename, system_prompt, analysis_prompt, persona_filename)

                    # Send reset_complete so the client clears the chat
                    await ws.send_json({"type": "reset_complete"})

                    # Send first_mes — NO analysis, just display
                    _user_name = persona.get("name", "User") if persona else "User"
                    first_mes = get_first_mes(card, user_name=_user_name)
                    if first_mes:
                        msg_id = db_add_message(session_id, "assistant", first_mes, is_first_mes=True)
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
                # Delete a message and all subsequent messages from SQLite.
                # The frontend removes the DOM nodes.
                target_id = data.get("message_id")
                if target_id is not None and session_id is not None:
                    deleted = db_delete_message(session_id, target_id)
                    await ws.send_json({"type": "message_deleted", "message_id": target_id, "success": deleted})

            elif data["type"] == "get_report":
                # Gather all assistant responses + analyses from SQLite
                assistant_msgs = db_get_assistant_messages(session_id)
                # Exclude first_mes from report data (it wasn't analyzed)
                responses = [m["content"] for m in assistant_msgs if not m["is_first_mes"]]
                analyses = [m["analysis"] for m in assistant_msgs if not m["is_first_mes"] and m["analysis"]]

                report = await generate_report(
                    analysis_prompt, responses, analyses, card, persona, persona_filename, ws=ws
                )
                await ws.send_json({"type": "report", "content": report})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except:
            pass


def _now_iso() -> str:
    """Current UTC timestamp in ISO format for console events."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def analyze_response(analysis_prompt: str, response: str,
                           previous_responses: list[str], card: dict,
                           ws: Optional[WebSocket] = None) -> dict:
    """Run analysis LLM call on a character response."""
    d = card.get("data", card)
    char_name = d.get("name", "Unknown")

    # Build the analysis message
    prev_context = ""
    if previous_responses:
        prev_context = f"\n\nPREVIOUS CHARACTER RESPONSES (for drift comparison):\n"
        for i, r in enumerate(previous_responses[-3:], 1):  # last 3 for context
            prev_context += f"Response {i}: {r[:300]}...\n"

    user_msg = f"""Analyze this response from the character "{char_name}":

RESPONSE TO ANALYZE:
{response}
{prev_context}

Return ONLY the JSON object."""

    analysis_messages = [
        {"role": "system", "content": analysis_prompt},
        {"role": "user", "content": user_msg},
    ]

    # ── Console: log the analysis request ──
    if ws:
        await ws.send_json({
            "type": "console_event",
            "event": "request",
            "llm": "analysis",
            "model": ANALYSIS_MODEL,
            "temperature": ANALYSIS_TEMPERATURE,
            "max_tokens": ANALYSIS_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": analysis_prompt},
                {"role": "user", "content": user_msg},
            ],
            "timestamp": _now_iso(),
        })

    try:
        resp = await analysis_client.chat.completions.create(
            model=ANALYSIS_MODEL,
            messages=analysis_messages,
            temperature=ANALYSIS_TEMPERATURE,
            max_tokens=ANALYSIS_MAX_TOKENS,
        )
        content = resp.choices[0].message.content.strip()
        usage = resp.usage

        # ── Console: log the analysis response ──
        if ws:
            await ws.send_json({
                "type": "console_event",
                "event": "response",
                "llm": "analysis",
                "model": ANALYSIS_MODEL,
                "content": content,
                "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                "finish_reason": resp.choices[0].finish_reason,
                "timestamp": _now_iso(),
            })

        # Try to parse JSON from the response
        # Handle cases where LLM wraps JSON in markdown code blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        # Find the JSON object
        brace_start = content.find("{")
        brace_end = content.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            content = content[brace_start:brace_end+1]

        # Use json_repair for resilient parsing — LLMs often produce
        # slightly malformed JSON (trailing commas, missing delimiters)
        return json.loads(repair_json(content))
    except Exception as e:
        return {
            "overall": "error",
            "summary": f"Analysis error: {str(e)}",
            "raw_response": response,
        }


async def generate_report(analysis_prompt: str, responses: list[str],
                          analyses: list[dict], card: dict,
                          persona: Optional[dict] = None,
                          persona_filename: str = None,
                          ws: Optional[WebSocket] = None) -> dict:
    """Generate a final session report."""
    d = card.get("data", card)
    char_name = d.get("name", "Unknown")

    # Aggregate stats from analyses
    total = len(analyses)
    passes = sum(1 for a in analyses if a.get("overall") == "pass")
    fails = sum(1 for a in analyses if a.get("overall") == "fail")
    partials = sum(1 for a in analyses if a.get("overall") == "partial")

    all_violations = []
    all_banned = []
    all_leakage = []
    fragility_scores = {"strong": 0, "fragile": 0, "broken": 0}

    for i, a in enumerate(analyses):
        if a.get("rule_violations"):
            for v in a["rule_violations"]:
                all_violations.append({"message": i+1, "violation": v})
        if a.get("banned_words"):
            all_banned.append({"message": i+1, "words": a["banned_words"]})
        if a.get("leakage", {}).get("detected"):
            all_leakage.append({"message": i+1, "details": a["leakage"].get("details", "")})
        frag = a.get("fragility", "strong")
        if frag in fragility_scores:
            fragility_scores[frag] += 1

    # Ask analysis LLM for a comprehensive summary
    all_data = json.dumps({
        "responses": [{"n": i+1, "text": r[:500]} for i, r in enumerate(responses)],
        "analyses": analyses,
        "stats": {"total": total, "passes": passes, "fails": fails, "partials": partials,
                  "fragility": fragility_scores, "violations": all_violations,
                  "banned_words": all_banned, "leakage": all_leakage}
    }, indent=2)

    report_messages = [
        {"role": "system", "content": analysis_prompt + "\n\nYou are now generating a FINAL SESSION REPORT. Analyze all the data and provide comprehensive findings."},
        {"role": "user", "content": f"""Generate a final session report for the character "{char_name}".

SESSION DATA:
{all_data}

Return a JSON object with this structure:
{{
  "character_name": "name",
  "overall_verdict": "pass / fail / partial",
  "compliance_score": "X/Y rules consistently followed",
  "phase_results": {{
    "voice_speech": "pass/fail/partial — one line",
    "personality": "pass/fail/partial — one line",
    "arc_mechanics": "pass/fail/partial — one line",
    "consistency": "pass/fail/partial — one line"
  }},
  "issues_by_severity": {{
    "critical": ["issues that will break real sessions"],
    "major": ["issues that degrade quality"],
    "minor": ["cosmetic or edge case issues"]
  }},
  "rule_effectiveness": [
    {{"rule": "description", "status": "strong/fragile/broken", "notes": "why"}}
  ],
  "fragility_summary": "overall how close the rules were to breaking",
  "leakage_summary": "summary of desire/emotion leakage patterns",
  "training_data_conflicts": ["where LLM defaults fought against the rules"],
  "recommended_fixes": [
    {{"priority": 1, "fix": "specific actionable fix with exact wording", "field": "which field to edit", "location": "exact section within the field", "placement": "exactly where in the field — quote nearby text or name the section", "action": "add/replace/append", "suggested_text": "ready-to-paste text", "issue": "what problem this fix addresses"}}
  ],
  "enhancement_suggestions": [
    {{"suggestion": "how to improve the card proactively", "field": "which field to edit", "placement": "exactly where in the field", "rationale": "why this helps"}}
  ],
  "retest_recommendation": "should they retest? which areas?"
}}

Return ONLY the JSON object."""},
    ]

    # ── Console: log the report request ──
    if ws:
        await ws.send_json({
            "type": "console_event",
            "event": "request",
            "llm": "analysis",
            "model": ANALYSIS_MODEL,
            "label": "Session Report",
            "temperature": ANALYSIS_TEMPERATURE,
            "max_tokens": ANALYSIS_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": analysis_prompt},
                {"role": "user", "content": f"[Session report for {char_name} — {total} messages analyzed]"},
            ],
            "timestamp": _now_iso(),
        })

    try:
        resp = await analysis_client.chat.completions.create(
            model=ANALYSIS_MODEL,
            messages=report_messages,
            temperature=ANALYSIS_TEMPERATURE,
            max_tokens=ANALYSIS_MAX_TOKENS,
        )
        content = resp.choices[0].message.content.strip()
        usage = resp.usage

        # ── Console: log the report response ──
        if ws:
            await ws.send_json({
                "type": "console_event",
                "event": "response",
                "llm": "analysis",
                "model": ANALYSIS_MODEL,
                "label": "Session Report",
                "content": content,
                "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                "finish_reason": resp.choices[0].finish_reason,
                "timestamp": _now_iso(),
            })

        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        brace_start = content.find("{")
        brace_end = content.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            content = content[brace_start:brace_end+1]
        report = json.loads(repair_json(content))
    except:
        report = {
            "character_name": char_name,
            "overall_verdict": "error",
            "stats": {"total": total, "passes": passes, "fails": fails, "partials": partials},
            "error": "Failed to generate AI report, showing raw stats",
        }

    # Always include raw stats
    report["raw_stats"] = {
        "total_messages": total,
        "passes": passes,
        "fails": fails,
        "partials": partials,
        "fragility_distribution": fragility_scores,
        "all_violations": all_violations,
        "all_banned_words": all_banned,
        "all_leakage": all_leakage,
    }

    # Include persona info in report
    if persona:
        report["persona_used"] = {
            "name": persona.get("name", ""),
            "filename": persona_filename or "",
            "traits": persona.get("traits", {}),
            "what_it_tests": persona.get("what_it_tests", ""),
        }

    return report


# ─── RP REST API ──────────────────────────────────────────────────
@app.get("/api/rp/sessions")
async def api_rp_list_sessions():
    return JSONResponse(db_rp_list_sessions())


@app.get("/api/rp/sessions/{session_id}")
async def api_rp_get_session(session_id: int):
    sess = db_rp_get_session(session_id)
    if not sess:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    messages = db_rp_get_messages(session_id)
    sess["messages"] = [{"id": m["id"], "role": m["role"], "speaker": m["speaker"], "content": m["content"]} for m in messages]
    return JSONResponse(sess)


@app.delete("/api/rp/sessions/{session_id}")
async def api_rp_delete_session(session_id: int):
    deleted = db_rp_delete_session(session_id)
    return JSONResponse({"deleted": deleted})


# ─── RP WebSocket ─────────────────────────────────────────────────
@app.websocket("/ws/rp")
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
                        card = load_card(fn)
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
                        persona = load_persona(persona_filename)
                    except:
                        persona = None
                else:
                    persona = None

                stack_config_json = data.get("stack_config")
                stack_config_str = json.dumps(stack_config_json) if stack_config_json else None

                session_id = db_rp_create_session(char_list, persona_filename, turn_routing, response_style, summary_window, raw_window, stack_config_str)

                await ws.send_json({
                    "type": "session_started", "session_id": session_id,
                    "characters": [{"filename": fn, "name": character_names[fn]} for fn in character_order],
                    "persona": persona_filename,
                    "turn_routing": turn_routing, "response_style": response_style,
                    "stack_config": stack_config_json or DEFAULT_STACK_CONFIG,
                    "console_events": [],
                })

                # Send first_mes from all characters
                _rp_user_name = persona.get("name", "User") if persona else "User"
                for fn in character_order:
                    first_mes = get_first_mes(cards[fn], user_name=_rp_user_name)
                    if first_mes:
                        msg_id = db_rp_add_message(session_id, "character", first_mes, character_names[fn])
                        await ws.send_json({
                            "type": "character_message", "content": first_mes,
                            "character_filename": fn, "character_name": character_names[fn],
                            "is_first_mes": True, "message_id": msg_id,
                        })

            elif data["type"] == "resume":
                resume_id = data.get("session_id")
                sess = db_rp_get_session(resume_id)
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
                        card = load_card(fn)
                        cards[fn] = card
                        character_names[fn] = c["char_name"]
                        character_order.append(fn)
                    except:
                        pass

                if persona_filename:
                    try:
                        persona = load_persona(persona_filename)
                    except:
                        persona = None
                else:
                    persona = None

                messages = db_rp_get_messages(session_id)
                stack_cfg = get_stack_config(sess)

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

                db_rp_add_message(session_id, "user", user_content)

                async def _rp_gen():
                    try:
                        # Get stack config
                        sess = db_rp_get_session(session_id)
                        stack_cfg = get_stack_config(sess)

                        # Determine head_n and tail_n from stack
                        head_n = 0
                        tail_n = 0
                        for blk in stack_cfg.get("blocks", []):
                            if blk.get("type") == "head" and blk.get("enabled", True):
                                head_n = blk.get("n", 2)
                            elif blk.get("type") == "tail" and blk.get("enabled", True):
                                tail_n = blk.get("n", 3)

                        # Check if summarization needed (middle section between head and tail)
                        msg_count = db_rp_count_messages(session_id)
                        all_msgs = db_rp_get_messages(session_id)
                        if msg_count > head_n + summary_window + tail_n:
                            end_idx = len(all_msgs) - tail_n if tail_n > 0 else len(all_msgs)
                            to_summarize = all_msgs[head_n:end_idx] if head_n < end_idx else []
                            if to_summarize:
                                existing_summary = sess.get("summary_text", "") if sess else ""
                                new_summary = await rp_summarize(session_id, to_summarize, existing_summary, ws=ws)
                                db_rp_update_summary(session_id, new_summary)
                                await ws.send_json({"type": "summary_updated", "summary": new_summary, "summarized_count": len(to_summarize)})
                                # Refresh session to get updated summary
                                sess = db_rp_get_session(session_id)

                        # Build system prompt
                        system_prompt = build_rp_system_prompt(
                            [cards[fn] for fn in character_order],
                            persona, turn_routing, response_style,
                            directed_character=character_names.get(directed_to) if directed_to else None,
                        )

                        # Build LLM messages from stack config
                        summary_text = sess.get("summary_text", "") if sess else ""
                        all_msgs = db_rp_get_messages(session_id)
                        llm_messages, block_markers = build_llm_messages_from_stack(
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
                            "model": CHAT_MODEL, "temperature": CHAT_TEMPERATURE, "max_tokens": CHAT_MAX_TOKENS,
                            "messages": [{"role": m["role"], "content": m["content"]} for m in llm_messages],
                            "block_markers": block_markers,
                            "timestamp": _now_iso(),
                        })

                        resp = await chat_client.chat.completions.create(
                            model=CHAT_MODEL, messages=llm_messages,
                            temperature=CHAT_TEMPERATURE, max_tokens=CHAT_MAX_TOKENS,
                            extra_body={"enable_thinking": CHAT_ENABLE_THINKING},
                        )
                        raw_content = resp.choices[0].message.content
                        usage = resp.usage

                        await ws.send_json({
                            "type": "console_event", "event": "response", "llm": "character",
                            "model": CHAT_MODEL, "content": raw_content,
                            "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                            "finish_reason": resp.choices[0].finish_reason, "timestamp": _now_iso(),
                        })

                        parsed = parse_rp_response(raw_content, character_names, character_order)

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
                                msg_id = db_rp_add_message(session_id, "character", pr["content"], pr["name"])
                                await ws.send_json({
                                    "type": "character_message", "content": pr["content"],
                                    "character_filename": pr["filename"], "character_name": pr["name"],
                                    "is_first_mes": False, "message_id": msg_id,
                                })
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
                    deleted = db_rp_delete_message(session_id, target_id)
                    await ws.send_json({"type": "message_deleted", "message_id": target_id, "success": deleted})

            elif data["type"] == "update_settings":
                if session_id:
                    turn_routing = data.get("turn_routing", turn_routing)
                    response_style = data.get("response_style", response_style)
                    summary_window = data.get("summary_window", summary_window)
                    raw_window = data.get("raw_window", raw_window)
                    db_rp_update_settings(session_id, turn_routing, response_style, summary_window, raw_window)
                    await ws.send_json({"type": "settings_updated", "turn_routing": turn_routing, "response_style": response_style, "summary_window": summary_window, "raw_window": raw_window})

            elif data["type"] == "update_stack":
                if session_id:
                    stack_cfg_data = data.get("stack_config")
                    if stack_cfg_data:
                        stack_cfg_str = json.dumps(stack_cfg_data)
                        db_rp_update_settings(session_id, stack_config=stack_cfg_str)
                        await ws.send_json({"type": "stack_updated", "stack_config": stack_cfg_data})

            elif data["type"] == "summarize_block":
                if session_id:
                    block_index = data.get("block_index")
                    sess = db_rp_get_session(session_id)
                    stack_cfg = get_stack_config(sess)
                    blocks = stack_cfg.get("blocks", [])
                    if block_index is not None and 0 <= block_index < len(blocks):
                        block = blocks[block_index]
                        if block.get("type") in ("early_summary", "late_summary"):
                            start_idx = block.get("start", 0)
                            end_idx = block.get("end", 0)
                            all_msgs = db_rp_get_messages(session_id)
                            start_idx = max(0, min(start_idx, len(all_msgs)))
                            end_idx = max(start_idx, min(end_idx, len(all_msgs)))
                            to_summarize = all_msgs[start_idx:end_idx]
                            existing = block.get("text", "")
                            if to_summarize:
                                await ws.send_json({"type": "block_summarizing", "block_index": block_index})
                                new_summary = await rp_summarize(session_id, to_summarize, existing, ws=ws)
                                block["text"] = new_summary
                                stack_cfg["blocks"] = blocks
                                db_rp_update_settings(session_id, stack_config=json.dumps(stack_cfg))
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
                    db_rp_save_console_events(session_id, json.dumps(events))
                    await ws.send_json({"type": "console_events_saved"})

            elif data["type"] == "set_persona":
                persona_filename = data.get("persona_filename")
                if persona_filename:
                    try:
                        persona = load_persona(persona_filename)
                    except:
                        persona = None
                else:
                    persona = None
                    persona_filename = None
                if session_id:
                    db_rp_set_persona(session_id, persona_filename)
                await ws.send_json({"type": "persona_changed", "persona": persona_filename})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except:
            pass


# ─── Character Generation API ────────────────────────────────────

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


@app.post("/api/generate-character")
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
        "model": CHAT_MODEL, "label": "Character Generation",
        "temperature": 0.9, "max_tokens": 4000,
        "messages": messages, "timestamp": _now_iso(),
    }]

    try:
        completion = await chat_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            temperature=0.9,
            max_tokens=4000,
            extra_body={"enable_thinking": CHAT_ENABLE_THINKING},
        )
        raw = completion.choices[0].message.content or ""

        usage = completion.usage
        console_events.append({
            "type": "console_event", "event": "response", "llm": "character",
            "model": CHAT_MODEL, "label": "Character Generation",
            "content": raw,
            "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
            "finish_reason": completion.choices[0].finish_reason, "timestamp": _now_iso(),
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
            "model": CHAT_MODEL, "label": "Character Generation",
            "message": str(e), "timestamp": _now_iso(),
        })
        return JSONResponse({"error": str(e), "console_events": console_events}, status_code=500)


@app.post("/api/generate-character/save")
async def save_generated_character(req: Request):
    """Save a generated/previewed character card to the characters directory."""
    body = await req.json()
    card = body.get("card")
    if not card or not isinstance(card, dict):
        return JSONResponse({"error": "Invalid card data"}, status_code=400)

    # Extract name for filename
    version = detect_card_version(card)
    data = card.get("data", card) if version >= 2 else card
    name = (data.get("name") or "unnamed").strip()
    if not name:
        name = "unnamed"

    # Sanitize filename
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name).strip('_').lower() or "unnamed"
    filename = f"{safe_name}.json"

    # Avoid overwriting existing files
    if CHARACTERS_DIR.exists():
        counter = 1
        while (CHARACTERS_DIR / filename).exists():
            filename = f"{safe_name}_{counter}.json"
            counter += 1

    save_card(filename, card)
    return {"status": "ok", "filename": filename, "name": name}


# ─── Config API ───────────────────────────────────────────────────
@app.get("/api/config")
async def get_config():
    """Return current config with API keys masked."""
    _c = load_config()
    def mask(key):
        if not key or len(key) <= 8:
            return key
        return key[:4] + "…" + key[-4:]
    return {
        "version": APP_VERSION,
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


@app.post("/api/config")
async def update_config(req: Request):
    """Update config.jsonc and reload. Only writes provided fields.
    If api_key contains '…' (masked), keep the existing key."""
    body = await req.json()
    current = load_config()

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

    save_config(current)
    reload_config()
    return {"status": "ok", "message": "Config saved and reloaded. Restart server if port/host changed."}


@app.post("/api/database/reset")
async def reset_database():
    """Wipe all data from all tables, then re-init."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
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


# ─── Static files (CSS/JS) ────────────────────────────────────────
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    _srv = _cfg.get("server", {})
    _host = os.environ.get("CHARACTERSCHOOL_HOST", _srv.get("host", "0.0.0.0"))
    _port = int(os.environ.get("CHARACTERSCHOOL_PORT", _srv.get("port", 7862)))
    uvicorn.run(app, host=_host, port=_port)
