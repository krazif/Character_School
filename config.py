"""
Character School — configuration layer (JSONC config + LLM clients).
Split from db.py.
"""
import json
import os
import re
from pathlib import Path
from openai import AsyncOpenAI

APP_VERSION = "1.0.0"

# ─── Helpers ──────────────────────────────────────────────────────
def _normalize_float(val):
    """Return float or None. Empty/None/NaN → None."""
    if val is None or val == "" or val == "null":
        return None
    try:
        f = float(val)
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None

def _normalize_int(val):
    """Return int or None. Empty/None/NaN → None."""
    if val is None or val == "" or val == "null":
        return None
    try:
        i = int(val)
        return i
    except (ValueError, TypeError):
        return None

def _json_val(val):
    """Return JSON-safe representation: number, null, or string.
    Empty strings are normalized to null."""
    if val is None or val == "":
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    return json.dumps(val)

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
    lines.append(f'    "top_p": {_json_val(chat.get("top_p"))},')
    lines.append(f'    "top_k": {_json_val(chat.get("top_k"))},')
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
    lines.append(f'    "max_tokens": {int(ana.get("max_tokens", 1500))},')
    lines.append(f'    "top_p": {_json_val(ana.get("top_p"))},')
    lines.append(f'    "top_k": {_json_val(ana.get("top_k"))}')
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
    lines.append(f'    "max_tokens": {int(summ.get("max_tokens", 1000))},')
    lines.append(f'    "top_p": {_json_val(summ.get("top_p"))},')
    lines.append(f'    "top_k": {_json_val(summ.get("top_k"))}')
    lines.append('  },')
    imggen = cfg.get("imagegen", {})
    lines.append('')
    lines.append('  // ── Image generation endpoint (ComfyUI) — for Generate Scene feature ──')
    lines.append('  "imagegen": {')
    lines.append(f'    "enabled": {"true" if imggen.get("enabled", False) else "false"},')
    lines.append('    "base_url": ' + json.dumps(str(imggen.get("base_url", "http://127.0.0.1:8188"))) + ',')
    lines.append('    "negative_prompt": ' + json.dumps(str(imggen.get("negative_prompt", ""))) + ',')
    lines.append(f'    "width": {int(imggen.get("width", 512))},')
    lines.append(f'    "height": {int(imggen.get("height", 768))},')
    lines.append(f'    "steps": {int(imggen.get("steps", 20))},')
    lines.append(f'    "cfg_scale": {float(imggen.get("cfg_scale", 7.0))},')
    lines.append('    "sampler": ' + json.dumps(str(imggen.get("sampler", "euler"))) + ',')
    lines.append('    "scheduler": ' + json.dumps(str(imggen.get("scheduler", "normal"))) + ',')
    wf = imggen.get("workflow")
    lines.append('    "workflow": ' + (json.dumps(wf) if wf else 'null') + '  // null = use built-in default txt2img workflow')
    lines.append('  },')
    imgauto = cfg.get("imagegen_auto", {})
    lines.append('')
    lines.append('  // ── Image auto-generation (Phase 3) — small LLM decides when to generate scene images ──')
    lines.append('  "imagegen_auto": {')
    lines.append(f'    "enabled": {"true" if imgauto.get("enabled", False) else "false"},')
    lines.append('    "base_url": ' + json.dumps(str(imgauto.get("base_url", "https://openrouter.ai/api/v1"))) + ',')
    imgauto_key = imgauto.get("api_key")
    lines.append('    "api_key": ' + (json.dumps(imgauto_key) if imgauto_key else 'null') + ',')
    lines.append('    "model": ' + json.dumps(str(imgauto.get("model", "deepseek/deepseek-v4-flash"))) + ',')
    lines.append(f'    "temperature": {float(imgauto.get("temperature", 0.5))},')
    lines.append(f'    "max_tokens": {int(imgauto.get("max_tokens", 500))},')
    lines.append(f'    "interval": {int(imgauto.get("interval", 5))},' + '  // auto-generate every N messages')
    lines.append('    "negative_prompt": ' + json.dumps(str(imgauto.get("negative_prompt", ""))) + '')
    lines.append('  },')
    paths = cfg.get("paths", {})
    lines.append('')
    lines.append('  // Paths — can be absolute or relative to the app directory')
    lines.append('  "paths": {')
    _cdir = paths.get("characters_dir")
    lines.append('    "characters_dir": ' + (json.dumps(_cdir) if _cdir else 'null') + ', // null = defaults to ./characters inside app dir')
    pdir = paths.get("personas_dir")
    lines.append('    "personas_dir": ' + (json.dumps(pdir) if pdir else 'null') + '  // null = defaults to ./personas inside app dir')
    # ─── Presets ───
    presets = cfg.get("presets", {})
    if presets:
        lines.append('  },')
        lines.append('')
        lines.append('  // ── Endpoint Presets ──')
        lines.append('  "presets": ' + json.dumps(presets, indent=4).replace('\n', '\n  '))
    else:
        lines.append('  }')
    # ─── Workflows ───
    workflows = cfg.get("workflows", {})
    if workflows:
        lines[-1] = lines[-1] + ','
        lines.append('')
        lines.append('  // ── ComfyUI Workflow Presets ──')
        lines.append('  "workflows": ' + json.dumps(workflows, indent=4).replace('\n', '\n  '))
    lines.append('}')
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def reload_config():
    """Reload config from disk and update all globals + LLM clients."""
    global _cfg, CHAT_BASE_URL, CHAT_API_KEY, CHAT_MODEL, CHAT_TEMPERATURE, CHAT_MAX_TOKENS, CHAT_ENABLE_THINKING, CHAT_TOP_P, CHAT_TOP_K
    global ANALYSIS_BASE_URL, ANALYSIS_API_KEY, ANALYSIS_MODEL, ANALYSIS_TEMPERATURE, ANALYSIS_MAX_TOKENS, ANALYSIS_TOP_P, ANALYSIS_TOP_K
    global SUMMARY_BASE_URL, SUMMARY_API_KEY, SUMMARY_MODEL, SUMMARY_TEMPERATURE, SUMMARY_MAX_TOKENS, SUMMARY_TOP_P, SUMMARY_TOP_K
    global chat_client, analysis_client, summary_client, CHARACTERS_DIR, PERSONAS_DIR, PRESETS
    global IMAGEGEN_BASE_URL, IMAGEGEN_ENABLED, IMAGEGEN_NEGATIVE, IMAGEGEN_WORKFLOW
    global IMAGEGEN_WIDTH, IMAGEGEN_HEIGHT, IMAGEGEN_STEPS, IMAGEGEN_CFG_SCALE, IMAGEGEN_SAMPLER, IMAGEGEN_SCHEDULER
    global IMAGEGEN_SEED, IMAGEGEN_RANDOM_SEED
    global IMAGEGEN_AUTO_ENABLED, IMAGEGEN_AUTO_BASE_URL, IMAGEGEN_AUTO_API_KEY, IMAGEGEN_AUTO_MODEL
    global IMAGEGEN_AUTO_TEMPERATURE, IMAGEGEN_AUTO_MAX_TOKENS, IMAGEGEN_AUTO_INTERVAL, IMAGEGEN_AUTO_NEGATIVE
    global imagegen_auto_client
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
    CHAT_TOP_P = _normalize_float(_chat_cfg.get("top_p"))
    CHAT_TOP_K = _normalize_int(_chat_cfg.get("top_k"))
    ANALYSIS_BASE_URL    = os.environ.get("CHARACTERSCHOOL_ANALYSIS_BASE_URL",    _analysis_cfg.get("base_url", "https://openrouter.ai/api/v1"))
    ANALYSIS_API_KEY     = os.environ.get("CHARACTERSCHOOL_ANALYSIS_API_KEY",     _analysis_key or os.environ.get("OPENROUTER_API_KEY", ""))
    ANALYSIS_MODEL       = os.environ.get("CHARACTERSCHOOL_ANALYSIS_MODEL",       _analysis_cfg.get("model", "deepseek/deepseek-v4-flash"))
    ANALYSIS_TEMPERATURE = _analysis_cfg.get("temperature", 0.1)
    ANALYSIS_MAX_TOKENS  = _analysis_cfg.get("max_tokens", 1500)
    ANALYSIS_TOP_P = _normalize_float(_analysis_cfg.get("top_p"))
    ANALYSIS_TOP_K = _normalize_int(_analysis_cfg.get("top_k"))
    SUMMARY_BASE_URL    = os.environ.get("CHARACTERSCHOOL_SUMMARY_BASE_URL",    _summary_cfg.get("base_url", "https://openrouter.ai/api/v1"))
    SUMMARY_API_KEY     = os.environ.get("CHARACTERSCHOOL_SUMMARY_API_KEY",     _summary_key or os.environ.get("OPENROUTER_API_KEY", ""))
    SUMMARY_MODEL       = os.environ.get("CHARACTERSCHOOL_SUMMARY_MODEL",       _summary_cfg.get("model", "deepseek/deepseek-v4-flash"))
    SUMMARY_TEMPERATURE = _summary_cfg.get("temperature", 0.3)
    SUMMARY_MAX_TOKENS  = _summary_cfg.get("max_tokens", 1000)
    SUMMARY_TOP_P = _normalize_float(_summary_cfg.get("top_p"))
    SUMMARY_TOP_K = _normalize_int(_summary_cfg.get("top_k"))
    PRESETS = _cfg.get("presets", {})
    # ── Image generation (ComfyUI) config ──
    _imggen_cfg = _cfg.get("imagegen", {})
    IMAGEGEN_BASE_URL = _imggen_cfg.get("base_url", "http://127.0.0.1:8188")
    IMAGEGEN_ENABLED  = _imggen_cfg.get("enabled", False)
    IMAGEGEN_NEGATIVE = _imggen_cfg.get("negative_prompt", "")
    IMAGEGEN_WORKFLOW = _imggen_cfg.get("workflow", None)
    IMAGEGEN_WIDTH    = int(_imggen_cfg.get("width", 512))
    IMAGEGEN_HEIGHT   = int(_imggen_cfg.get("height", 768))
    IMAGEGEN_STEPS    = int(_imggen_cfg.get("steps", 20))
    IMAGEGEN_CFG_SCALE = float(_imggen_cfg.get("cfg_scale", 7.0))
    IMAGEGEN_SAMPLER  = _imggen_cfg.get("sampler", "euler")
    IMAGEGEN_SCHEDULER = _imggen_cfg.get("scheduler", "normal")
    IMAGEGEN_SEED      = int(_imggen_cfg.get("seed", 0))
    IMAGEGEN_RANDOM_SEED = _imggen_cfg.get("random_seed", True)
    # ── Image auto-generation (Phase 3) config ──
    _imgauto_cfg = _cfg.get("imagegen_auto", {})
    IMAGEGEN_AUTO_ENABLED     = _imgauto_cfg.get("enabled", False)
    IMAGEGEN_AUTO_BASE_URL    = _imgauto_cfg.get("base_url", "https://openrouter.ai/api/v1")
    IMAGEGEN_AUTO_API_KEY     = _imgauto_cfg.get("api_key") or os.environ.get("OPENROUTER_API_KEY", "")
    IMAGEGEN_AUTO_MODEL       = _imgauto_cfg.get("model", "deepseek/deepseek-v4-flash")
    IMAGEGEN_AUTO_TEMPERATURE = _imgauto_cfg.get("temperature", 0.5)
    IMAGEGEN_AUTO_MAX_TOKENS  = int(_imgauto_cfg.get("max_tokens", 500))
    IMAGEGEN_AUTO_INTERVAL    = int(_imgauto_cfg.get("interval", 5))
    IMAGEGEN_AUTO_NEGATIVE    = _imgauto_cfg.get("negative_prompt", "")
    imagegen_auto_client = AsyncOpenAI(base_url=IMAGEGEN_AUTO_BASE_URL, api_key=IMAGEGEN_AUTO_API_KEY or "not-configured")
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
UPLOAD_DIR = APP_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

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
CHAT_TOP_P = _normalize_float(_chat_cfg.get("top_p"))
CHAT_TOP_K = _normalize_int(_chat_cfg.get("top_k"))

ANALYSIS_BASE_URL    = os.environ.get("CHARACTERSCHOOL_ANALYSIS_BASE_URL",    _analysis_cfg.get("base_url", "https://openrouter.ai/api/v1"))
ANALYSIS_API_KEY     = os.environ.get("CHARACTERSCHOOL_ANALYSIS_API_KEY",     _analysis_key or os.environ.get("OPENROUTER_API_KEY", ""))
ANALYSIS_MODEL       = os.environ.get("CHARACTERSCHOOL_ANALYSIS_MODEL",       _analysis_cfg.get("model", "deepseek/deepseek-v4-flash"))
ANALYSIS_TEMPERATURE = _analysis_cfg.get("temperature", 0.1)
ANALYSIS_MAX_TOKENS  = _analysis_cfg.get("max_tokens", 1500)
ANALYSIS_TOP_P = _normalize_float(_analysis_cfg.get("top_p"))
ANALYSIS_TOP_K = _normalize_int(_analysis_cfg.get("top_k"))

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
SUMMARY_TOP_P = _normalize_float(_summary_cfg.get("top_p"))
SUMMARY_TOP_K = _normalize_int(_summary_cfg.get("top_k"))
summary_client  = AsyncOpenAI(base_url=SUMMARY_BASE_URL,  api_key=SUMMARY_API_KEY or "not-configured")

# ─── Presets ──────────────────────────────────────────────────────
PRESETS = _cfg.get("presets", {})

# ─── Image generation (ComfyUI) ────────────────────────────────────
_imggen_cfg = _cfg.get("imagegen", {})
IMAGEGEN_BASE_URL = _imggen_cfg.get("base_url", "http://127.0.0.1:8188")
IMAGEGEN_ENABLED  = _imggen_cfg.get("enabled", False)
IMAGEGEN_NEGATIVE = _imggen_cfg.get("negative_prompt", "")
IMAGEGEN_WORKFLOW = _imggen_cfg.get("workflow", None)
IMAGEGEN_WIDTH    = int(_imggen_cfg.get("width", 512))
IMAGEGEN_HEIGHT   = int(_imggen_cfg.get("height", 768))
IMAGEGEN_STEPS    = int(_imggen_cfg.get("steps", 20))
IMAGEGEN_CFG_SCALE = float(_imggen_cfg.get("cfg_scale", 7.0))
IMAGEGEN_SAMPLER  = _imggen_cfg.get("sampler", "euler")
IMAGEGEN_SCHEDULER = _imggen_cfg.get("scheduler", "normal")
IMAGEGEN_SEED      = int(_imggen_cfg.get("seed", 0))
IMAGEGEN_RANDOM_SEED = _imggen_cfg.get("random_seed", True)

# ─── Image auto-generation (Phase 3) ───────────────────────────────
_imgauto_cfg = _cfg.get("imagegen_auto", {})
IMAGEGEN_AUTO_ENABLED     = _imgauto_cfg.get("enabled", False)
IMAGEGEN_AUTO_BASE_URL    = _imgauto_cfg.get("base_url", "https://openrouter.ai/api/v1")
IMAGEGEN_AUTO_API_KEY     = _imgauto_cfg.get("api_key") or os.environ.get("OPENROUTER_API_KEY", "")
IMAGEGEN_AUTO_MODEL       = _imgauto_cfg.get("model", "deepseek/deepseek-v4-flash")
IMAGEGEN_AUTO_TEMPERATURE = _imgauto_cfg.get("temperature", 0.5)
IMAGEGEN_AUTO_MAX_TOKENS  = int(_imgauto_cfg.get("max_tokens", 500))
IMAGEGEN_AUTO_INTERVAL    = int(_imgauto_cfg.get("interval", 5))
IMAGEGEN_AUTO_NEGATIVE    = _imgauto_cfg.get("negative_prompt", "")
imagegen_auto_client = AsyncOpenAI(base_url=IMAGEGEN_AUTO_BASE_URL, api_key=IMAGEGEN_AUTO_API_KEY or "not-configured")

