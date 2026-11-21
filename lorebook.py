"""
Character School — Lorebook (World Info) module.
SillyTavern-compatible lorebook storage and keyword-triggered injection.

Lorebooks are stored as JSON files in the lorebooks directory.
Each lorebook has the SillyTavern World Info format:
{
  "entries": {
    "0": {
      "uid": 0,
      "key": ["keyword1", "keyword2"],
      "keysecondary": ["keyword3"],
      "comment": "Entry description",
      "content": "Lore text to inject",
      "constant": false,
      "selective": true,
      "order": 100,
      "position": 0,       # 0=before system, 1=after system, 2=before chat
      "disable": false,
      "extensions": {}
    }
  }
}
"""
import json
import os
from pathlib import Path
from typing import Optional
import db


def _lorebooks_dir() -> Path:
    """Get the lorebooks directory, creating it if needed."""
    d = Path(db.APP_DIR) / "lorebooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_lorebooks() -> list[dict]:
    """List all lorebooks with summary info."""
    d = _lorebooks_dir()
    result = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            entries = data.get("entries", {})
            entry_count = len(entries)
            active_count = sum(1 for e in entries.values() if not e.get("disable", False))
            constant_count = sum(1 for e in entries.values() if e.get("constant", False) and not e.get("disable", False))
            result.append({
                "filename": p.name,
                "name": data.get("name", p.stem),
                "description": data.get("description", ""),
                "entry_count": entry_count,
                "active_count": active_count,
                "constant_count": constant_count,
            })
        except (json.JSONDecodeError, KeyError):
            result.append({"filename": p.name, "name": p.stem, "description": "", "entry_count": 0, "active_count": 0, "constant_count": 0, "error": True})
    return result


def load_lorebook(filename: str) -> dict:
    """Load a lorebook by filename."""
    path = _lorebooks_dir() / filename
    if not path.exists():
        raise FileNotFoundError(f"Lorebook not found: {filename}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_lorebook(filename: str, data: dict) -> None:
    """Save a lorebook by filename."""
    path = _lorebooks_dir() / filename
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_lorebook(filename: str) -> bool:
    """Delete a lorebook. Returns True if deleted."""
    path = _lorebooks_dir() / filename
    if not path.exists():
        return False
    path.unlink()
    return True


def create_lorebook(name: str, description: str = "") -> dict:
    """Create a new empty lorebook and return its info."""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.lower()).strip("_")
    if not safe_name:
        safe_name = "lorebook"
    filename = f"{safe_name}.json"
    # Avoid overwrites
    d = _lorebooks_dir()
    counter = 1
    while (d / filename).exists():
        filename = f"{safe_name}_{counter}.json"
        counter += 1
    data = {
        "name": name,
        "description": description,
        "entries": {},
    }
    save_lorebook(filename, data)
    return {"filename": filename, "name": name, "description": description, "entry_count": 0}


# ─── Keyword Matching Engine ───────────────────────────────────────

def _check_keywords(keywords: list[str], text: str, case_insensitive: bool = True) -> bool:
    """Check if any of the keywords appear in the text.
    Keywords can be single words or multi-word phrases."""
    if not keywords:
        return False
    haystack = text.lower() if case_insensitive else text
    for kw in keywords:
        if not kw:
            continue
        needle = kw.lower() if case_insensitive else kw
        if needle in haystack:
            return True
    return False


def scan_for_active_entries(lorebook: dict, recent_messages: list[dict],
                            scan_depth: int = 10) -> list[dict]:
    """Scan recent messages for keyword triggers and return active entries.

    Args:
        lorebook: The lorebook dict with "entries"
        recent_messages: List of message dicts with "content" key
        scan_depth: Number of recent messages to scan (from the end)

    Returns:
        List of active entry dicts sorted by order (descending = higher priority first)
    """
    entries = lorebook.get("entries", {})
    if not entries:
        return []

    # Build the text to scan from recent messages
    msgs_to_scan = recent_messages[-scan_depth:] if len(recent_messages) > scan_depth else recent_messages
    scan_text = " ".join(m.get("content", "") for m in msgs_to_scan)

    active = []
    for uid_str, entry in entries.items():
        if entry.get("disable", False):
            continue

        is_active = False

        if entry.get("constant", False):
            # Constant entries are always active
            is_active = True
        else:
            keys = entry.get("key", [])
            if not keys:
                continue

            if entry.get("selective", False) and entry.get("keysecondary"):
                # Selective mode: need at least one primary AND one secondary match
                primary_match = _check_keywords(keys, scan_text)
                secondary_match = _check_keywords(entry["keysecondary"], scan_text)
                is_active = primary_match and secondary_match
            else:
                # Normal mode: any primary keyword match
                is_active = _check_keywords(keys, scan_text)

        if is_active:
            active.append(entry)

    # SillyTavern insertion order: constant entries first, then ascending
    # `order` — ST puts larger order numbers CLOSER to the end of the
    # context ("an entry with Order 100 appears before an entry with
    # Order 250"), i.e. higher order = more impact.
    active.sort(key=lambda e: (0 if e.get("constant") else 1, e.get("order", 100)))
    return active


# Entry placement, simplified from SillyTavern's insertion positions to what
# this prompt structure supports:
#   0 = before system — prepended to the system message (moderate impact)
#   1 = after system  — appended to the system message (greater impact)
#   2 = before chat   — its own system message directly before the first
#                       user turn (greatest impact)
POSITION_BEFORE_SYSTEM = 0
POSITION_AFTER_SYSTEM = 1
POSITION_BEFORE_CHAT = 2


def build_lorebook_blocks(lorebooks: list[dict], recent_messages: list[dict],
                          scan_depth: int = 10) -> dict:
    """Scan lorebooks and group active entries by position.

    Returns {0: text, 1: text, 2: text} with "" for positions that fired
    nothing. Within a position, entries keep ST insertion order: constants
    first, then ascending `order` (larger order = closer to the end of the
    context = more impact), merged across books as if one file.
    """
    groups: dict = {POSITION_BEFORE_SYSTEM: [], POSITION_AFTER_SYSTEM: [],
                    POSITION_BEFORE_CHAT: []}
    for lb in lorebooks:
        lb_name = lb.get("name", "Unknown")
        for entry in scan_for_active_entries(lb, recent_messages, scan_depth):
            content = (entry.get("content") or "").strip()
            if not content:
                continue
            # Missing/unknown position -> after system = where the old
            # code always put it (appended to the system message).
            pos = entry.get("position", POSITION_AFTER_SYSTEM)
            if pos not in groups:
                pos = POSITION_AFTER_SYSTEM
            groups[pos].append({
                "lorebook": lb_name,
                "comment": entry.get("comment", ""),
                "content": content,
                "order": entry.get("order", 100),
                "constant": bool(entry.get("constant", False)),
            })

    blocks = {}
    for pos, entries in groups.items():
        entries.sort(key=lambda e: (0 if e["constant"] else 1, e["order"]))
        if not entries:
            blocks[pos] = ""
            continue
        parts = ["WORLD INFO (LOREBOOK ENTRIES):"]
        for e in entries:
            label = e["comment"] if e["comment"] else "(unnamed)"
            parts.append(f"[{e['lorebook']} — {label}]")
            parts.append(e["content"])
            parts.append("")
        blocks[pos] = "\n".join(parts)
    return blocks


def _join(a: str, b: str) -> str:
    """Concatenate two prompt fragments without a stray blank line."""
    if not a:
        return b
    if not b:
        return a
    return a + "\n\n" + b


def apply_lorebook_blocks(llm_messages: list[dict], blocks: dict) -> str:
    """Place each position block into llm_messages; returns injected text.

    Position 0/1 need a leading system message — if the stack built none,
    one is created rather than silently dropping the lore (the old code's
    failure mode).
    """
    before = (blocks.get(POSITION_BEFORE_SYSTEM) or "").strip()
    after = (blocks.get(POSITION_AFTER_SYSTEM) or "").strip()
    chat = (blocks.get(POSITION_BEFORE_CHAT) or "").strip()
    injected = "\n\n".join(t for t in (before, after, chat) if t)
    if not injected:
        return ""

    has_system = bool(llm_messages) and llm_messages[0].get("role") == "system"
    if (before or after) and not has_system:
        llm_messages.insert(0, {"role": "system", "content": ""})
    if before:
        sys_msg = llm_messages[0]
        sys_msg["content"] = _join(before, sys_msg.get("content", ""))
    if after:
        sys_msg = llm_messages[0]
        sys_msg["content"] = _join(sys_msg.get("content", ""), after)
    if chat:
        idx = next((i for i, m in enumerate(llm_messages)
                    if m.get("role") != "system"), len(llm_messages))
        llm_messages.insert(idx, {"role": "system", "content": chat})
    return injected


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def inject_lorebook(llm_messages: list[dict], session: dict,
                          all_msgs: list[dict], send,
                          scan_depth: int = 10) -> str:
    """Load a session's attached lorebooks, inject active entries, notify.

    One call replaces the six hand-rolled injection blocks in school.py and
    rp.py. `send` is the async JSON sender for the console event
    (ws.send_json / _safe_send). Returns the injected text ('' if none).
    """
    filenames = []
    raw = (session or {}).get("lorebooks")
    if raw:
        try:
            filenames = json.loads(raw)
        except (TypeError, ValueError):
            filenames = []
    if not filenames:
        return ""
    blocks = build_lorebook_blocks(load_lorebooks_for_session(filenames),
                                   all_msgs, scan_depth)
    injected = apply_lorebook_blocks(llm_messages, blocks)
    if injected:
        await send({
            "type": "console_event", "event": "lorebook_injection",
            "content": injected, "timestamp": _now_iso(),
        })
    return injected


def load_lorebooks_for_session(lorebook_filenames: list[str]) -> list[dict]:
    """Load multiple lorebooks by filename, skipping missing ones."""
    result = []
    for fn in lorebook_filenames:
        try:
            result.append(load_lorebook(fn))
        except FileNotFoundError:
            pass
    return result


def _unique_filename(safe_name: str) -> str:
    """Generate a unique .json filename, appending _N if needed."""
    filename = f"{safe_name}.json"
    d = _lorebooks_dir()
    counter = 1
    while (d / filename).exists():
        filename = f"{safe_name}_{counter}.json"
        counter += 1
    return filename


def _normalize_entry(uid, entry: dict) -> dict:
    """Normalize a single lorebook entry to have all required fields."""
    try:
        uid_int = int(uid)
    except (ValueError, TypeError):
        uid_int = uid
    return {
        "uid": entry.get("uid", uid_int),
        "key": entry.get("key", []) or [],
        "keysecondary": entry.get("keysecondary", []) or [],
        "comment": entry.get("comment", ""),
        "content": entry.get("content", ""),
        "constant": entry.get("constant", False),
        "selective": entry.get("selective", False),
        "order": entry.get("order", 100),
        "position": entry.get("position", POSITION_AFTER_SYSTEM),
        "disable": entry.get("disable", False),
        "extensions": entry.get("extensions", {}) or {},
    }


def import_lorebook(data: dict, override_name: str = "") -> dict:
    """Import a lorebook from SillyTavern World Info JSON or our own format.

    Accepts both SillyTavern format (entries dict at top level) and our
    extended format (name, description, entries). All entries are normalized
    to have every required field. A unique filename is generated to avoid
    overwriting existing lorebooks.

    Returns the new lorebook's summary info.
    """
    lb_name = override_name or data.get("name", "")
    description = data.get("description", "")
    entries = data.get("entries", {})

    if not lb_name:
        lb_name = "Imported Lorebook"

    # Normalize all entries
    normalized = {}
    for uid_str, entry in entries.items():
        normalized[str(uid_str)] = _normalize_entry(uid_str, entry)

    out_data = {
        "name": lb_name,
        "description": description,
        "entries": normalized,
    }

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in lb_name.lower()).strip("_")
    if not safe_name:
        safe_name = "lorebook"
    filename = _unique_filename(safe_name)

    save_lorebook(filename, out_data)
    return {"filename": filename, "name": lb_name, "description": description, "entry_count": len(normalized)}
