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
      "position": 0,
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

    # Sort by order descending (higher order = higher priority, injected closer to chat)
    active.sort(key=lambda e: e.get("order", 100), reverse=True)
    return active


def build_lorebook_injection(lorebooks: list[dict], recent_messages: list[dict],
                             scan_depth: int = 10) -> str:
    """Build the lorebook injection text from multiple lorebooks.

    Scans all provided lorebooks, collects active entries, and assembles
    them into a single text block for injection into the prompt.

    Args:
        lorebooks: List of lorebook dicts
        recent_messages: Recent chat messages for keyword scanning
        scan_depth: How many recent messages to scan

    Returns:
        Formatted lorebook text, or empty string if no entries active
    """
    all_entries = []
    for lb in lorebooks:
        lb_name = lb.get("name", "Unknown")
        active = scan_for_active_entries(lb, recent_messages, scan_depth)
        for entry in active:
            comment = entry.get("comment", "")
            content = entry.get("content", "").strip()
            if content:
                all_entries.append({
                    "lorebook": lb_name,
                    "comment": comment,
                    "content": content,
                    "order": entry.get("order", 100),
                })

    if not all_entries:
        return ""

    # Sort by order descending (same as individual scan, but across all lorebooks)
    all_entries.sort(key=lambda e: e["order"], reverse=True)

    parts = ["WORLD INFO (LOREBOOK ENTRIES):"]
    for e in all_entries:
        label = e["comment"] if e["comment"] else "(unnamed)"
        parts.append(f"[{e['lorebook']} — {label}]")
        parts.append(e["content"])
        parts.append("")

    return "\n".join(parts)


def load_lorebooks_for_session(lorebook_filenames: list[str]) -> list[dict]:
    """Load multiple lorebooks by filename, skipping missing ones."""
    result = []
    for fn in lorebook_filenames:
        try:
            result.append(load_lorebook(fn))
        except FileNotFoundError:
            pass
    return result
