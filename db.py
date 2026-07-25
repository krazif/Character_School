"""
Character School — backward-compat shim.

All config lives in config.py; all database logic lives in database.py.
This module re-exports both so `import db` + `db.X` keeps working.

Uses module-level __getattr__ (PEP 562) for LIVE delegation — after
reload_config() updates a global in config.py, `db.CHAT_MODEL` etc.
resolve to the new value at access time, not a stale import-time copy.
"""
import config as _config
import database as _database

# Names that live in each module (for __dir__ / introspection)
_CONFIG_NAMES = [
    "APP_VERSION", "load_config", "save_config", "reload_config",
    "CHARACTERS_DIR", "PERSONAS_DIR", "APP_DIR", "STATIC_DIR", "UPLOAD_DIR",
    "CHAT_BASE_URL", "CHAT_API_KEY", "CHAT_MODEL", "CHAT_TEMPERATURE",
    "CHAT_MAX_TOKENS", "CHAT_ENABLE_THINKING", "CHAT_TOP_P", "CHAT_TOP_K",
    "ANALYSIS_BASE_URL", "ANALYSIS_API_KEY", "ANALYSIS_MODEL",
    "ANALYSIS_TEMPERATURE", "ANALYSIS_MAX_TOKENS", "ANALYSIS_TOP_P", "ANALYSIS_TOP_K",
    "chat_client", "analysis_client",
    "SUMMARY_BASE_URL", "SUMMARY_API_KEY", "SUMMARY_MODEL",
    "SUMMARY_TEMPERATURE", "SUMMARY_MAX_TOKENS", "SUMMARY_TOP_P", "SUMMARY_TOP_K",
    "summary_client", "PRESETS",
    "IMAGEGEN_BASE_URL", "IMAGEGEN_ENABLED", "IMAGEGEN_NEGATIVE", "IMAGEGEN_WORKFLOW",
    "IMAGEGEN_WIDTH", "IMAGEGEN_HEIGHT", "IMAGEGEN_STEPS", "IMAGEGEN_CFG_SCALE",
    "IMAGEGEN_SAMPLER", "IMAGEGEN_SCHEDULER", "IMAGEGEN_SEED", "IMAGEGEN_RANDOM_SEED",
    "IMAGEGEN_AUTO_ENABLED", "IMAGEGEN_AUTO_BASE_URL", "IMAGEGEN_AUTO_API_KEY",
    "IMAGEGEN_AUTO_MODEL", "IMAGEGEN_AUTO_TEMPERATURE", "IMAGEGEN_AUTO_MAX_TOKENS",
    "IMAGEGEN_AUTO_INTERVAL", "IMAGEGEN_AUTO_NEGATIVE", "imagegen_auto_client",
    "_cfg",
]

_DATABASE_NAMES = [
    "DB_PATH", "init_db", "DEFAULT_STACK_CONFIG", "get_stack_config",
    "build_llm_messages_from_stack",
    "db_create_session", "db_add_message", "db_get_llm_messages",
    "db_get_assistant_messages", "db_delete_message", "db_clear_messages",
    "db_get_session",
    "db_rp_create_session", "db_rp_add_message", "db_rp_get_messages",
    "db_rp_get_session", "db_rp_save_console_events", "db_rp_list_sessions",
    "db_rp_delete_session", "db_rp_delete_message", "db_rp_delete_single_message",
    "db_rp_add_swipe", "db_rp_switch_swipe", "db_rp_truncate_after",
    "db_rp_count_messages", "db_rp_update_settings", "db_rp_add_character",
    "db_rp_remove_character", "db_rp_fork_session", "db_rp_set_persona",
    "db_school_create_session", "db_school_update_message_analysis",
    "db_school_add_message", "db_school_get_messages", "db_school_get_session",
    "db_school_list_sessions", "db_school_delete_session",
    "db_school_delete_message", "db_school_delete_single_message",
    "db_school_add_swipe", "db_school_switch_swipe", "db_school_truncate_after",
    "db_school_update_session_meta", "db_school_update_settings",
    "db_school_save_console_events", "db_school_count_messages",
    "db_school_get_assistant_messages", "db_school_fork_session",
    "db_school_set_persona", "db_school_to_rp",
]


def __getattr__(name):
    """Live delegation — always resolves to the current module global."""
    if name in _CONFIG_NAMES or name.startswith("_"):
        try:
            return getattr(_config, name)
        except AttributeError:
            pass
    if name in _DATABASE_NAMES:
        return getattr(_database, name)
    # Fallback: try both modules
    if hasattr(_config, name):
        return getattr(_config, name)
    if hasattr(_database, name):
        return getattr(_database, name)
    raise AttributeError(f"module 'db' has no attribute {name!r}")


def __dir__():
    return sorted(set(_CONFIG_NAMES + _DATABASE_NAMES))
