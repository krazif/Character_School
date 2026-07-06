"""
Character School — data layer (config + SQLite).
Split from server.py.
"""
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional
from openai import AsyncOpenAI

APP_VERSION = "1.0.0"

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
        {"type": "late_summary", "enabled": True, "start": 5, "end": 8, "text": "", "auto": False},
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
            elif m["role"] == "assistant":
                llm_messages.append({"role": "assistant", "content": m["content"]})
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


def db_rp_add_character(session_id: int, filename: str, name: str) -> bool:
    """Add a character to an existing RP session. Returns True on success."""
    conn = sqlite3.connect(str(DB_PATH))
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
    conn = sqlite3.connect(str(DB_PATH))
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


def db_rp_branch_session(session_id: int) -> Optional[dict]:
    """Duplicate an RP session with all its characters, messages, and settings.
    Returns {'new_session_id': N, 'title': '...'} or None on failure."""
    sess = db_rp_get_session(session_id)
    if not sess:
        return None
    conn = sqlite3.connect(str(DB_PATH))
    # Copy session row with (branch) suffix
    branch_title = sess['title'] + ' (branch)' if sess['title'] else 'Untitled (branch)'
    cur = conn.execute(
        """INSERT INTO rp_sessions
           (title, persona_filename, turn_routing, response_style, summary_window, raw_window,
            summary_text, stack_config, console_events)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (branch_title, sess['persona_filename'], sess['turn_routing'], sess['response_style'],
         sess['summary_window'], sess['raw_window'], sess.get('summary_text', ''),
         sess.get('stack_config'), sess.get('console_events')),
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
    # Copy messages (preserving seq, role, speaker, content)
    msgs = conn.execute(
        "SELECT seq, role, speaker, content FROM rp_messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    for m in msgs:
        conn.execute(
            "INSERT INTO rp_messages (session_id, seq, role, speaker, content) VALUES (?, ?, ?, ?, ?)",
            (new_sid, m[0], m[1], m[2], m[3]),
        )
    conn.commit()
    conn.close()
    return {'new_session_id': new_sid, 'title': branch_title}


def db_rp_set_persona(session_id: int, persona_filename: str) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE rp_sessions SET persona_filename = ?, updated_at = datetime('now') WHERE id = ?", (persona_filename, session_id))
    conn.commit()
    conn.close()


# ─── School Database Functions ────────────

def db_school_create_session(card_filename: str, persona_filename: str = None,
                             stack_config: str = None, title: str = None) -> int:
    conn = sqlite3.connect(str(DB_PATH))
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
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE school_messages SET analysis_json = ? WHERE id = ?", (analysis_json, message_id))
    conn.commit()
    conn.close()


def db_school_add_message(session_id: int, role: str, content: str,
                          is_first_mes: bool = False, analysis_json: str = None) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM school_messages WHERE session_id = ?", (session_id,)).fetchone()
    next_seq = row[0]
    cur = conn.execute(
        "INSERT INTO school_messages (session_id, seq, role, content, is_first_mes, analysis_json) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, next_seq, role, content, 1 if is_first_mes else 0, analysis_json),
    )
    msg_id = cur.lastrowid
    conn.execute("UPDATE school_sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return msg_id


def db_school_get_messages(session_id: int) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT id, seq, role, content, is_first_mes, analysis_json FROM school_messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "seq": r[1], "role": r[2], "content": r[3],
             "is_first_mes": bool(r[4]), "analysis_json": r[5]} for r in rows]


def db_school_get_session(session_id: int) -> Optional[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT id, title, card_filename, persona_filename, stack_config, console_events FROM school_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "title": row[1], "card_filename": row[2],
        "persona_filename": row[3], "stack_config": row[4], "console_events": row[5],
    }


def db_school_list_sessions() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT id, title, card_filename, persona_filename, updated_at FROM school_sessions ORDER BY updated_at DESC",
    ).fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "card_filename": r[2],
             "persona_filename": r[3], "updated_at": r[4]} for r in rows]


def db_school_delete_session(session_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM school_messages WHERE session_id = ?", (session_id,))
    cur = conn.execute("DELETE FROM school_sessions WHERE id = ?", (session_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def db_school_delete_message(session_id: int, message_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT seq FROM school_messages WHERE id = ? AND session_id = ?", (message_id, session_id)).fetchone()
    if not row:
        conn.close()
        return False
    deleted_seq = row[0]
    conn.execute("DELETE FROM school_messages WHERE session_id = ? AND seq >= ?", (session_id, deleted_seq))
    conn.commit()
    conn.close()
    return True


def db_school_update_session_meta(session_id: int, persona_filename: str = None,
                                  title: str = None) -> None:
    """Update persona_filename and/or title on an existing school session."""
    conn = sqlite3.connect(str(DB_PATH))
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


def db_school_update_settings(session_id: int, stack_config: str = None, response_style: str = None) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    if stack_config is not None:
        conn.execute("UPDATE school_sessions SET stack_config = ?, updated_at = datetime('now') WHERE id = ?",
                     (stack_config, session_id))
    if response_style is not None:
        conn.execute("UPDATE school_sessions SET response_style = ?, updated_at = datetime('now') WHERE id = ?",
                     (response_style, session_id))
    conn.commit()
    conn.close()


def db_school_save_console_events(session_id: int, events_json: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE school_sessions SET console_events = ?, updated_at = datetime('now') WHERE id = ?",
                 (events_json, session_id))
    conn.commit()
    conn.close()


def db_school_count_messages(session_id: int) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT COUNT(*) FROM school_messages WHERE session_id = ?", (session_id,)).fetchone()
    conn.close()
    return row[0] if row else 0


def db_school_get_assistant_messages(session_id: int) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
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


def db_school_branch_session(session_id: int) -> Optional[dict]:
    sess = db_school_get_session(session_id)
    if not sess:
        return None
    conn = sqlite3.connect(str(DB_PATH))
    branch_title = sess['title'] + ' (branch)' if sess['title'] else 'Untitled (branch)'
    cur = conn.execute(
        """INSERT INTO school_sessions (title, card_filename, persona_filename, stack_config, console_events)
           VALUES (?, ?, ?, ?, ?)""",
        (branch_title, sess['card_filename'], sess['persona_filename'],
         sess.get('stack_config'), sess.get('console_events')),
    )
    new_sid = cur.lastrowid
    msgs = conn.execute(
        "SELECT seq, role, content, is_first_mes, analysis_json FROM school_messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    for m in msgs:
        conn.execute(
            "INSERT INTO school_messages (session_id, seq, role, content, is_first_mes, analysis_json) VALUES (?, ?, ?, ?, ?, ?)",
            (new_sid, m[0], m[1], m[2], m[3], m[4]),
        )
    conn.commit()
    conn.close()
    return {'new_session_id': new_sid, 'title': branch_title}


def db_school_set_persona(session_id: int, persona_filename: str) -> None:
    conn = sqlite3.connect(str(DB_PATH))
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
    card_path = CHARACTERS_DIR / sess['card_filename']
    if not card_path.exists():
        return None
    try:
        card = json.loads(card_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    d = card.get('data', card)
    char_name = d.get('name', Path(sess['card_filename']).stem)

    # Get response_style from school session
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT response_style FROM school_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    response_style = row[0] if row and row[0] else 'brief'

    # Get messages
    msgs = db_school_get_messages(session_id)

    # Create RP session
    title = sess.get('title', char_name)
    cur = conn.execute(
        """INSERT INTO rp_sessions (title, persona_filename, turn_routing, response_style,
           summary_window, raw_window, stack_config, console_events)
           VALUES (?, ?, 'auto', ?, 20, 10, ?, ?)""",
        (title, sess.get('persona_filename'), response_style,
         sess.get('stack_config'), sess.get('console_events')),
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
        conn.execute(
            "INSERT INTO rp_messages (session_id, seq, role, speaker, content) VALUES (?, ?, ?, ?, ?)",
            (rp_sid, m['seq'], role, speaker, m['content']),
        )

    conn.commit()
    conn.close()
    return rp_sid
