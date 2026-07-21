"""
Character School — engine layer (pure logic, no FastAPI).
Split from server.py.
"""
import json
import re
import base64
import io
from pathlib import Path
from typing import Optional, Any
from json_repair import repair_json
import db

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


# ─── Persona Management ───────────────────────────────────────────
def list_personas() -> list[dict]:
    """List all persona files (V1 format)."""
    personas = []
    for p in sorted(db.PERSONAS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            personas.append({
                "filename": p.name,
                "name": data.get("name", p.stem),
                "description": data.get("description", ""),
                "personality": data.get("personality", ""),
                "tags": data.get("tags", []),
                "what_it_tests": data.get("what_it_tests", ""),
            })
        except (json.JSONDecodeError, KeyError):
            personas.append({"filename": p.name, "name": p.stem, "description": "", "personality": "", "tags": [], "what_it_tests": "", "error": True})
    return personas


def load_persona(filename: str) -> dict:
    """Load a persona by filename."""
    path = db.PERSONAS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Persona not found: {filename}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_persona(filename: str, data: dict) -> None:
    """Save a persona by filename."""
    path = db.PERSONAS_DIR / filename
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def build_persona_context(persona: dict) -> str:
    """Build the persona context string to inject into the character LLM.
    SillyTavern-style: inject V1 fields as plain context, no hardcoded directive.
    The persona author controls all directives via the description field."""
    parts = []
    parts.append(f"THE PERSON YOU ARE INTERACTING WITH:")
    parts.append(f"Name: {persona.get('name', 'Unknown')}")
    if persona.get("description"):
        parts.append(f"Description: {persona['description']}")
    if persona.get("personality"):
        parts.append(f"Personality: {persona['personality']}")
    return "\n".join(parts)


def build_analysis_persona_context(persona: dict) -> str:
    """Build persona context for the analysis LLM (V1 fields)."""
    return f"""PERSONA USED IN THIS SESSION:
Name: {persona.get('name', 'Unknown')}
Description: {persona.get('description', '')}
Personality: {persona.get('personality', '')}
What it tests: {persona.get('what_it_tests', '')}

When assessing the character's response, consider the persona's pressure level. Guard holding against a highly attractive/pressuring persona is more significant than holding against a neutral one. Desire leaking toward a shy non-pursuing persona is more significant than leaking toward someone actively pursuing. Factor the persona's description and personality into your assessment of fragility and leakage."""


# ─── Card Management ──────────────────────────────────────────────
def list_cards() -> list[dict]:
    """List all character cards in the characters directory."""
    cards = []
    for p in sorted(db.CHARACTERS_DIR.glob("*.json")):
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
    path = db.CHARACTERS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Card not found: {filename}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_card(filename: str, data: dict) -> None:
    """Save a character card by filename."""
    path = db.CHARACTERS_DIR / filename
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


def _pov_directive(pov: str) -> str:
    """Return an IMPORTANT-tagged POV/perspective directive for the LLM."""
    if pov == 'first':
        return "[IMPORTANT — PERSPECTIVE: FIRST PERSON] Write from your own perspective using 'I' and 'me' for yourself. Refer to the user as 'you' or by their name. All actions, observations, and narration must be from your first-person viewpoint."
    elif pov == 'second':
        return "[IMPORTANT — PERSPECTIVE: SECOND PERSON] Address the user directly using 'you'. Describe the user's actions in second person. Write your own actions and dialogue as your character speaking/acting — the 'you' refers to the user, not yourself."
    elif pov == 'third':
        return "[IMPORTANT — PERSPECTIVE: THIRD PERSON] Write in third person narrative. Use your character name or she/he/they pronouns for yourself. Use 'you' for the user. Narrate actions and dialogue as an observer describing your character."
    return ""  # None = no directive (inherit from card content)


def _inner_monologue_directive(enabled: bool) -> str:
    """Return an IMPORTANT-tagged inner monologue directive for the LLM."""
    if enabled:
        return "[IMPORTANT — INNER MONOLOGUE: ON] At the end of your response, include your character's private thoughts prefixed with 'Thoughts:' on a new line. This must reflect what you are secretly thinking — your true feelings, suspicions, desires, or calculations — which may differ from what you say or do outwardly. The Thoughts block is mandatory in every response."
    return ""


def _is_gemma(model: str) -> bool:
    """Check if the model is a Gemma variant."""
    return bool(model and "gemma" in model.lower())


def build_extra_body(model: str, enable_thinking: bool) -> dict:
    """Build the extra_body for LLM calls, branching on model family.

    Ollama's OpenAI-compatible /v1/chat/completions endpoint maps
    reasoning_effort to the internal Think field:
      "high"/"medium"/"low" → thinking ON
      "none"                → thinking OFF
    The native "think" param and "options.think" are silently ignored on
    the compat endpoint. Gemma models go through Ollama; non-Gemma models
    (DeepSeek, Qwen via OpenRouter etc.) use the standard enable_thinking.
    """
    if _is_gemma(model):
        if not enable_thinking:
            return {"reasoning_effort": "none"}
        return {}  # Ollama auto-enables thinking for capable models
    return {"enable_thinking": enable_thinking}


def build_system_prompt(card: dict, user_name: str = "User", response_style: str = None,
                        pov: str = None, inner_monologue: bool = False) -> str:
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
    # Response style directive (simplified — caps + auto-continue handle enforcement)
    if response_style == 'short':
        parts.append("\n\n[IMPORTANT — RESPONSE LENGTH: Keep it short. Prefer dialogue and action over description.]")
    elif response_style == 'moderate':
        parts.append("\n\n[IMPORTANT — RESPONSE LENGTH: Moderate length. Balance dialogue, action beats, and description.]")
    elif response_style == 'long':
        parts.append("\n\n[IMPORTANT — RESPONSE LENGTH: Write a full, detailed paragraph — include dialogue, actions, internal thoughts, body language, and emotional detail.]")
    elif response_style == 'flexible':
        parts.append("\n\n[IMPORTANT — RESPONSE LENGTH: Choose your length naturally based on the scene.]")
    # POV / perspective directive
    _pov = _pov_directive(pov)
    if _pov:
        parts.append(f"\n\n{_pov}")
    # Inner monologue directive
    _im = _inner_monologue_directive(inner_monologue)
    if _im:
        parts.append(f"\n\n{_im}")
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
      "suggested_text": "the exact text to add or replace with — ready-to-paste, same as fixes.suggested_text",
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
12. ENHANCEMENTS: Beyond fixing immediate issues, suggest proactive card improvements in the "enhancements" array. Think about what would make the card more robust against LLM training defaults, what patterns could be reinforced, what instructions could be clearer. Each enhancement must include:
    - "suggestion": what the enhancement does
    - "location": which card field to edit
    - "placement": EXACTLY where in that field (same rules as fixes.placement — quote nearby existing text)
    - "suggested_text": the actual ready-to-paste text the card author should insert — NOT a description of what to write, but the actual text itself, written in the card's voice and style
    - "rationale": why this enhancement helps
    If no enhancements needed, return empty array."""


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
                           turn_routing: str = 'auto', response_style: str = 'moderate',
                           directed_character: str = None,
                           pov: str = None, inner_monologue: bool = False) -> str:
    """Build system prompt for multi-character RP."""
    parts = []
    parts.append("You are roleplaying as a character in a shared scene. The user is a participant in the scene.")
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
    parts.append("- Start your response with [CharacterName]: followed by the character's dialogue and actions.")
    parts.append("- Example: [Lisa]: *looks up from her book* What did you say?")
    if turn_routing == 'auto':
        parts.append("- You choose which single character responds. Respond as the one most appropriate for this moment.")
        parts.append("- Do not write dialogue for other characters — only the one you choose.")
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

    # Response style (simplified — caps + auto-continue handle enforcement)
    if response_style == 'short':
        parts.append("[IMPORTANT — RESPONSE LENGTH: Keep it short. Prefer dialogue and action over description.]")
    elif response_style == 'moderate':
        parts.append("[IMPORTANT — RESPONSE LENGTH: Moderate length. Balance dialogue, action beats, and description.]")
    elif response_style == 'long':
        parts.append("[IMPORTANT — RESPONSE LENGTH: Write a full, detailed paragraph — include dialogue, actions, internal thoughts, body language, and emotional detail.]")
    elif response_style == 'flexible':
        parts.append("[IMPORTANT — RESPONSE LENGTH: Choose your length naturally based on the scene.]")
    parts.append("")

    # POV / perspective directive
    _pov = _pov_directive(pov)
    if _pov:
        parts.append(_pov)
        parts.append("")
    # Inner monologue directive
    _im = _inner_monologue_directive(inner_monologue)
    if _im:
        parts.append(_im)
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
                       existing_summary: str, ws: Any = None, send_fn=None) -> str:
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

    _send = send_fn or (lambda payload: ws.send_json(payload)) if ws else None
    if _send:
        await _send({
            "type": "console_event", "event": "request", "llm": "summary",
            "model": db.SUMMARY_MODEL, "label": "Summarization",
            "temperature": db.SUMMARY_TEMPERATURE, "max_tokens": db.SUMMARY_MAX_TOKENS,
            "messages": messages, "timestamp": _now_iso(),
        })

    try:
        kwargs = dict(
            model=db.SUMMARY_MODEL, messages=messages,
            temperature=db.SUMMARY_TEMPERATURE, max_tokens=db.SUMMARY_MAX_TOKENS,
        )
        if db.SUMMARY_TOP_P is not None:
            kwargs["top_p"] = db.SUMMARY_TOP_P
        if db.SUMMARY_TOP_K is not None:
            kwargs["top_k"] = db.SUMMARY_TOP_K
        if _send:
            await _send({
                "type": "console_event", "event": "request_kwargs", "llm": "summary",
                "label": "Summarization", "kwargs": kwargs, "timestamp": _now_iso(),
            })
        resp = await db.summary_client.chat.completions.create(**kwargs)
        summary = resp.choices[0].message.content.strip()
        usage = resp.usage
        if _send:
            await _send({
                "type": "console_event", "event": "response", "llm": "summary",
                "model": db.SUMMARY_MODEL, "label": "Summarization",
                "content": summary,
                "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None,
                "finish_reason": resp.choices[0].finish_reason, "timestamp": _now_iso(),
            })
        return summary
    except Exception as e:
        if _send:
            try:
                await _send({"type": "error", "message": f"Summarization error: {e}"})
            except Exception:
                pass
        return existing_summary



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



def _now_iso() -> str:
    """Current UTC timestamp in ISO format for console events."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def analyze_response(analysis_prompt: str, response: str,
                           previous_responses: list[str], card: dict,
                           guidance: str = "",
                           ws: Optional[Any] = None) -> dict:
    """Run analysis LLM call on a character response.

    If *guidance* is provided, the analyzer is asked to focus on the user's
    desired characteristics and suggest specific card edits.
    """
    d = card.get("data", card)
    char_name = d.get("name", "Unknown")

    # Build the analysis message
    prev_context = ""
    if previous_responses:
        prev_context = f"\n\nPREVIOUS CHARACTER RESPONSES (for drift comparison):\n"
        for i, r in enumerate(previous_responses[-3:], 1):  # last 3 for context
            prev_context += f"Response {i}: {r[:300]}...\n"

    guidance_block = ""
    if guidance and guidance.strip():
        guidance_block = f"""

USER GUIDANCE — DESIRED CHARACTERISTICS:
{guidance.strip()}

Focus your analysis on how well the character matches these desired characteristics. In your fixes and enhancements, suggest specific card edits — what to add exactly, where to add it (which field, which section, quoting nearby text) — that would make the character better match these desired characteristics."""

    user_msg = f"""Analyze this response from the character "{char_name}":

RESPONSE TO ANALYZE:
{response}
{prev_context}{guidance_block}

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
            "model": db.ANALYSIS_MODEL,
            "temperature": db.ANALYSIS_TEMPERATURE,
            "max_tokens": db.ANALYSIS_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": analysis_prompt},
                {"role": "user", "content": user_msg},
            ],
            "timestamp": _now_iso(),
        })

    try:
        kwargs = dict(
            model=db.ANALYSIS_MODEL,
            messages=analysis_messages,
            temperature=db.ANALYSIS_TEMPERATURE,
            max_tokens=db.ANALYSIS_MAX_TOKENS,
        )
        if db.ANALYSIS_TOP_P is not None:
            kwargs["top_p"] = db.ANALYSIS_TOP_P
        if db.ANALYSIS_TOP_K is not None:
            kwargs["top_k"] = db.ANALYSIS_TOP_K
        if ws:
            await ws.send_json({
                "type": "console_event", "event": "request_kwargs", "llm": "analysis",
                "label": "Analysis", "kwargs": kwargs, "timestamp": _now_iso(),
            })
        resp = await db.analysis_client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content.strip()
        usage = resp.usage

        # ── Console: log the analysis response ──
        if ws:
            await ws.send_json({
                "type": "console_event",
                "event": "response",
                "llm": "analysis",
                "model": db.ANALYSIS_MODEL,
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
                          ws: Optional[Any] = None) -> dict:
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
    {{"suggestion": "how to improve the card proactively", "field": "which field to edit", "placement": "exactly where in the field", "suggested_text": "ready-to-paste text", "rationale": "why this helps"}}
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
            "model": db.ANALYSIS_MODEL,
            "label": "Session Report",
            "temperature": db.ANALYSIS_TEMPERATURE,
            "max_tokens": db.ANALYSIS_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": analysis_prompt},
                {"role": "user", "content": f"[Session report for {char_name} — {total} messages analyzed]"},
            ],
            "timestamp": _now_iso(),
        })

    try:
        kwargs = dict(
            model=db.ANALYSIS_MODEL,
            messages=report_messages,
            temperature=db.ANALYSIS_TEMPERATURE,
            max_tokens=db.ANALYSIS_MAX_TOKENS,
        )
        if db.ANALYSIS_TOP_P is not None:
            kwargs["top_p"] = db.ANALYSIS_TOP_P
        if db.ANALYSIS_TOP_K is not None:
            kwargs["top_k"] = db.ANALYSIS_TOP_K
        if ws:
            await ws.send_json({
                "type": "console_event", "event": "request_kwargs", "llm": "analysis",
                "label": "Session Report", "kwargs": kwargs, "timestamp": _now_iso(),
            })
        resp = await db.analysis_client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content.strip()
        usage = resp.usage

        # ── Console: log the report response ──
        if ws:
            await ws.send_json({
                "type": "console_event",
                "event": "response",
                "llm": "analysis",
                "model": db.ANALYSIS_MODEL,
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
            "description": persona.get("description", ""),
            "personality": persona.get("personality", ""),
            "what_it_tests": persona.get("what_it_tests", ""),
        }

    return report


def response_style_max_tokens(response_style: str, default: int = 2000) -> int:
    """Map response_style to a max_tokens cap that matches the word-count target.
    Tight caps force the LLM to finish naturally; auto-continue handles overflow.
    ~1.3 tokens/word for English (DeepSeek V4 Pro is ~3.4 tok/word for prose).
    short: ~150 words → 400 tokens max; moderate: ~300 words → 800 tokens max."""
    caps = {'short': 500, 'moderate': 800}
    return caps.get(response_style, default)


async def maybe_auto_continue(
    client, kwargs: dict, initial_resp, auto_continue: bool,
    response_style: str, send_fn=None, max_continuations: int = 3,
) -> tuple:
    """If initial response was truncated (finish_reason=length), auto-continue
    by appending partial content as an assistant message and re-calling the API.
    Logs each continuation via send_fn as console_event.
    Does NOT re-log the initial response (call site handles that).
    Returns (concatenated_content, final_finish_reason)."""
    raw_content = initial_resp.choices[0].message.content or ""
    finish_reason = initial_resp.choices[0].finish_reason

    cont_count = 0
    while (auto_continue and response_style != "short"
           and finish_reason == "length" and cont_count < max_continuations):
        cont_count += 1
        cont_kwargs = dict(kwargs)
        cont_kwargs["messages"] = list(kwargs["messages"]) + [
            {"role": "assistant", "content": raw_content}
        ]

        if send_fn:
            try:
                await send_fn({
                    "type": "console_event", "event": "auto_continue",
                    "content": f"Auto-continuing ({cont_count}/{max_continuations}) — previous response was truncated, continuing…",
                    "timestamp": _now_iso(),
                })
            except Exception:
                pass

        cont_resp = await client.chat.completions.create(**cont_kwargs)
        cont_content = cont_resp.choices[0].message.content or ""
        cont_finish = cont_resp.choices[0].finish_reason
        cont_usage = cont_resp.usage

        if send_fn:
            try:
                await send_fn({
                    "type": "console_event", "event": "response", "llm": "character",
                    "model": kwargs.get("model", ""), "content": cont_content,
                    "usage": {"prompt_tokens": cont_usage.prompt_tokens,
                              "completion_tokens": cont_usage.completion_tokens,
                              "total_tokens": cont_usage.total_tokens} if cont_usage else None,
                    "finish_reason": cont_finish, "timestamp": _now_iso(),
                })
            except Exception:
                pass

        raw_content += cont_content
        finish_reason = cont_finish

    return raw_content, finish_reason


def estimate_tokens(messages):
    """Rough token estimate: ~4 chars per token + 4 per message.
    Matches the frontend heuristic in estimateTokens()."""
    total = 0
    for m in messages:
        content = m.get("content", "") or ""
        total += (len(content) + 3) // 4 + 4
    return total
