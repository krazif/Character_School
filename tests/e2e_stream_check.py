"""End-to-end streaming check — drives the real WebSocket protocol.

    python tests/e2e_stream_check.py

Starts the mock LLM (tests/mock_llm.py) and a second Character School
instance on its own port, in a throwaway copy of the repo so the dev
database is never touched, then runs five scenarios:

  A  School mode   deltas arrive spread over time and match the final message
  B  RP auto mode  "[Name]:" prefix never reaches the bubble, speaker does
  C  TRUNCATE      auto-continue keeps streaming into the same bubble
  E  style 'short' ceiling does not bite a natural reply; cuts only a runaway
  D  endpoint rejects stream → plain-request fallback still answers

Exits 0 only when every assertion passes.
"""
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
PY = "/home/razif/miniconda3/bin/python3"
MOCK_PORT = 8898   # deliberately != the manual UI instance ports
APP_PORT = 7898
CONTINUED_TAIL = "and that is exactly how the rest of it went"

FAILURES = []
# Per-wait ceiling for collect_until; overridable so a hang surfaces fast
COLLECT_TIMEOUT = float(os.environ.get("E2E_COLLECT_TIMEOUT", "240"))


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def wait_http(url, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


async def collect_until(ws, predicate, timeout=None):
    """Read frames until *predicate* matches; return every frame seen."""
    timeout = timeout or COLLECT_TIMEOUT
    frames = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.5, deadline - time.monotonic()))
        data = json.loads(raw)
        frames.append((time.monotonic(), data))
        if predicate(data):
            return frames
    # Say WHAT arrived — a silent timeout tells you nothing about which
    # layer failed (an `error` frame in particular is easy to miss).
    seen = [f.get("type") for _, f in frames]
    errs = [f for _, f in frames if f.get("type") == "error"]
    raise AssertionError(f"predicate never matched; frames={seen}; errors={errs}")


def deltas_of(frames):
    return [(ts, f["text"]) for ts, f in frames if f.get("type") == "character_delta"]


def typed(frames, kind):
    return any(f.get("type") == kind for _, f in frames)


async def run_school_scenario(uri, card_name, text, expect_deltas=True, style=None):
    import websockets
    async with websockets.connect(uri, max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "start", "card_filename": "p_ramlee.json"}))
        frames = await collect_until(ws, lambda f: f.get("type") == "session_started")
        frames += await collect_until(ws, lambda f: f.get("type") == "character_message"
                                      and f.get("is_first_mes"))
        session_id = next(f["session_id"] for _, f in frames if f.get("type") == "session_started")
        if style:
            await ws.send(json.dumps({"type": "update_settings", "response_style": style}))
            frames += await collect_until(ws, lambda f: f.get("type") == "settings_updated")

        await ws.send(json.dumps({"type": "user_message", "content": text}))
        rest = await collect_until(
            ws,
            lambda f: f.get("type") == "character_message" and not f.get("is_first_mes"),
        )
        frames += rest

    final = next(f for _, f in frames if f.get("type") == "character_message"
                 and not f.get("is_first_mes"))
    d = deltas_of(frames)
    return frames, d, final, session_id


async def scenario_a_school(uri, card_name):
    print("A. School mode — incremental delivery")
    text = "Tell me about the afternoon."
    frames, deltas, final, session_id = await run_school_scenario(uri, card_name, text)

    check("typing indicator precedes the first delta",
          typed(frames, "character_typing") and deltas
          and next(i for i, (_, f) in enumerate(frames) if f.get("type") == "character_delta")
          > next(i for i, (_, f) in enumerate(frames) if f.get("type") == "character_typing"))
    check("more than one delta frame", len(deltas) > 1, f"{len(deltas)} frames")
    if len(deltas) > 1:
        span = deltas[-1][0] - deltas[0][0]
        check("deltas spread over time (not one lump)", span >= 0.3, f"span={span:.2f}s")
        print(f"         first delta at +0.00s, last at +{span:.2f}s, "
              f"{len(deltas)} frames, {sum(len(t) for _, t in deltas)} chars")
    joined = "".join(t for _, t in deltas)
    check("streamed text == final message", joined == final["content"],
          f"{len(joined)} vs {len(final['content'])} chars")
    check("typing stopped before the final message", typed(frames, "character_typing_stopped"))
    check("a reply that stopped naturally is not flagged truncated",
          final.get("truncated") is False, f"truncated={final.get('truncated')!r}")

    with urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}/api/school/sessions/{session_id}") as r:
        stored = json.load(r)
    stored_texts = [m["content"] for m in stored["messages"] if m["role"] == "assistant"
                    and not m["is_first_mes"]]
    check("final message persisted to SQLite", final["content"] in stored_texts)
    return final["content"]


async def scenario_b_rp(uri, card_name):
    print("B. RP auto mode — [Name]: prefix handling")
    import websockets
    async with websockets.connect(uri, max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "start", "characters": ["p_ramlee.json"]}))
        frames = await collect_until(ws, lambda f: f.get("type") == "session_started")
        frames += await collect_until(ws, lambda f: f.get("type") == "character_message"
                                      and f.get("is_first_mes"))
        await ws.send(json.dumps({"type": "user_message", "content": "Say something."}))
        frames += await collect_until(
            ws, lambda f: f.get("type") == "character_message" and not f.get("is_first_mes"))

    final = next(f for _, f in frames if f.get("type") == "character_message"
                 and not f.get("is_first_mes"))
    deltas = deltas_of(frames)
    joined = "".join(t for _, t in deltas)

    joined = "".join(t for _, t in deltas)
    check("prefix never reaches the bubble", "[" not in joined, repr(joined[:40]))
    check("speaker carried on the delta frames",
          all(f.get("character_name") == card_name for _, f in frames
              if f.get("type") == "character_delta"),
          f"expected {card_name!r}, got "
          f"{next((f.get('character_name') for _, f in frames if f.get('type') == 'character_delta'), None)!r}")
    check("streamed text == final message", joined == final["content"])
    check("final message is prefix-free", not final["content"].startswith("["))
    print(f"         bubble label: "
          f"{next((f.get('character_name') for _, f in frames if f.get('type') == 'character_delta'), None)!r}, "
          f"{len(deltas)} frames")


async def scenario_c_auto_continue(uri, card_name):
    print("C. TRUNCATE on an uncapped style — auto-continue keeps streaming")
    frames, deltas, final, _ = await run_school_scenario(
        uri, card_name, "TRUNCATE this please", style="long")

    events = [f for _, f in frames if f.get("type") == "console_event"]
    check("auto-continue console event fired",
          any(e.get("event") == "auto_continue" for e in events))
    check("continuation also streamed", len(deltas) > 1, f"{len(deltas)} frames")
    check("reply runs past the truncation point", final["content"].endswith(CONTINUED_TAIL),
          repr(final["content"][-60:]))
    joined = "".join(t for _, t in deltas)
    check("streamed text == final message", joined == final["content"])


async def scenario_e_style_cap(uri, card_name):
    """Pyre's model: a ceiling that does not bite. A reply that wants the
    length finishes on its own; only a runaway is cut — flagged truncated,
    never trimmed and never silently continued."""
    print("E. Response style 'short' — ceiling that does not bite, cuts only a runaway")
    import websockets

    # E1: a long-but-natural reply (~470 words) fits under the 800-token
    # ceiling and finishes on its own — no truncation at all.
    async with websockets.connect(uri, max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "start", "card_filename": "p_ramlee.json"}))
        frames = await collect_until(ws, lambda f: f.get("type") == "session_started")
        frames += await collect_until(ws, lambda f: f.get("type") == "character_message"
                                      and f.get("is_first_mes"))
        await ws.send(json.dumps({"type": "update_settings", "response_style": "short"}))
        frames += await collect_until(ws, lambda f: f.get("type") == "settings_updated")
        await ws.send(json.dumps({"type": "user_message", "content": "LONGREPLY tell me everything."}))
        frames += await collect_until(
            ws, lambda f: f.get("type") == "character_message" and not f.get("is_first_mes"))

    final = next(f for _, f in frames if f.get("type") == "character_message"
                 and not f.get("is_first_mes"))
    events = [f for _, f in frames if f.get("type") == "console_event"]
    req = next((e for e in events if e.get("event") == "request"), None)
    deltas = deltas_of(frames)
    streamed = "".join(t for _, t in deltas)
    content = final["content"]
    words = len(content.split())
    cap = req.get("max_tokens") if req else None

    check("style ceiling sent instead of the global 2000", cap == 800,
          f"max_tokens={cap}")
    check("length note rides the last user message (author's-note position)",
          bool(req) and "[Reply length —" in (req.get("messages") or [{"content": ""}])[-1].get("content", ""),
          f"last message ends: ...{(req['messages'][-1]['content'][-60:] if req else '')!r}")
    check("a long-natural reply finishes on its own — no truncation",
          final.get("truncated") is not True, f"truncated={final.get('truncated')!r}")
    check("reply reached its natural length uncut", words > 400, f"{words} words")
    check("auto-continue did NOT extend the reply",
          not any(e.get("event") == "auto_continue" for e in events))
    check("stored message is exactly what was streamed", streamed == content,
          f"streamed {len(streamed)} chars, stored {len(content)}")
    check("no trim event — trimming was removed",
          not any(e.get("event") == "style_trim" for e in events))

    # E2: a runaway reply (~1080 words) still hits the ceiling — cut, flagged
    # truncated for the client, kept verbatim, and NOT auto-continued
    # (continuing would defeat the cap).
    async with websockets.connect(uri, max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "start", "card_filename": "p_ramlee.json"}))
        frames = await collect_until(ws, lambda f: f.get("type") == "session_started")
        frames += await collect_until(ws, lambda f: f.get("type") == "character_message"
                                      and f.get("is_first_mes"))
        await ws.send(json.dumps({"type": "update_settings", "response_style": "short"}))
        frames += await collect_until(ws, lambda f: f.get("type") == "settings_updated")
        await ws.send(json.dumps({"type": "user_message", "content": "HUGEREPLY tell me everything."}))
        frames += await collect_until(
            ws, lambda f: f.get("type") == "character_message" and not f.get("is_first_mes"))

    final = next(f for _, f in frames if f.get("type") == "character_message"
                 and not f.get("is_first_mes"))
    events = [f for _, f in frames if f.get("type") == "console_event"]
    deltas = deltas_of(frames)
    streamed = "".join(t for _, t in deltas)
    content = final["content"]
    words = len(content.split())

    check("runaway reply cut at the ceiling", 500 < words < 600, f"{words} words")
    check("cut reply is flagged truncated for the client",
          final.get("truncated") is True, f"truncated={final.get('truncated')!r}")
    check("style-cap cut is not auto-continued",
          not any(e.get("event") == "auto_continue" for e in events))
    check("cut reply kept verbatim — stored == streamed", streamed == content,
          f"streamed {len(streamed)} chars, stored {len(content)}")


async def scenario_d_fallback(uri, card_name):
    print("D. Endpoint rejects stream — plain-request fallback")
    frames, deltas, final, _ = await run_school_scenario(uri, card_name, "Any answer at all.")
    check("no deltas were possible", len(deltas) == 0, f"{len(deltas)} frames")
    check("final message still correct", "afternoon light" in final["content"],
          repr(final["content"][:50]))


def start_app(workdir, env_overrides, log_path):
    env = dict(os.environ)
    env.update(env_overrides)
    log = open(log_path, "w")
    return subprocess.Popen(
        [PY, "main.py"], cwd=str(workdir), env=env,
        stdout=log, stderr=subprocess.STDOUT, text=True,
    ), log


async def main():
    import websockets  # noqa: F401  (fail fast if missing)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cs-e2e-"))
    app_dir = tmp / "app"
    shutil.copytree(
        REPO, app_dir,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "char_test.db*", "uploads",
                                      "*.pyc", "config.jsonc"),
    )
    card_name = json.loads((app_dir / "characters" / "p_ramlee.json").read_text())["data"]["name"]

    mock = subprocess.Popen(
        [PY, str(app_dir / "tests" / "mock_llm.py"), "--port", str(MOCK_PORT)],
        stdout=open(tmp / "mock.log", "w"), stderr=subprocess.STDOUT,
    )
    app = None
    app_log = None
    try:
        if not wait_http(f"http://127.0.0.1:{MOCK_PORT}/docs"):
            raise SystemExit("mock LLM never came up")
        if mock.poll() is not None:
            raise SystemExit(f"mock LLM exited immediately — port {MOCK_PORT} already in use?")

        app, app_log = start_app(app_dir, {
            "CHARACTERSCHOOL_HOST": "127.0.0.1",
            "CHARACTERSCHOOL_PORT": str(APP_PORT),
            "CHARACTERSCHOOL_CHAT_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}/v1",
            "CHARACTERSCHOOL_CHAT_API_KEY": "test-key",
            "CHARACTERSCHOOL_CHAT_MODEL": "mock-model",
            "CHARACTERSCHOOL_ANALYSIS_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}/v1",
            "CHARACTERSCHOOL_ANALYSIS_API_KEY": "test-key",
            "CHARACTERSCHOOL_ANALYSIS_MODEL": "mock-model",
            "CHARACTERSCHOOL_SUMMARY_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}/v1",
            "CHARACTERSCHOOL_SUMMARY_API_KEY": "test-key",
            "CHARACTERSCHOOL_SUMMARY_MODEL": "mock-model",
        }, tmp / "app.log")
        if not wait_http(f"http://127.0.0.1:{APP_PORT}/api/cards"):
            raise SystemExit("app never came up\n" + (tmp / "app.log").read_text())
        if app.poll() is not None:
            raise SystemExit(f"app exited immediately — port {APP_PORT} already in use?")

        ws_uri = f"ws://127.0.0.1:{APP_PORT}"
        await scenario_a_school(ws_uri + "/ws/chat", card_name)
        await scenario_b_rp(ws_uri + "/ws/rp", card_name)
        await scenario_c_auto_continue(ws_uri + "/ws/chat", card_name)
        await scenario_e_style_cap(ws_uri + "/ws/chat", card_name)

        # Swap the mock for one that refuses streaming, then re-run scenario A
        mock.terminate()
        mock.wait(timeout=10)
        mock = subprocess.Popen(
            [PY, str(app_dir / "tests" / "mock_llm.py"), "--port", str(MOCK_PORT), "--reject-stream"],
            stdout=open(tmp / "mock-reject.log", "w"), stderr=subprocess.STDOUT,
        )
        if not wait_http(f"http://127.0.0.1:{MOCK_PORT}/docs"):
            raise SystemExit("rejecting mock never came up")
        if mock.poll() is not None:
            raise SystemExit("rejecting mock exited immediately — port taken?")
        await asyncio.sleep(1)
        await scenario_d_fallback(ws_uri + "/ws/chat", card_name)
    finally:
        if app:
            app.terminate()
            try:
                app.wait(timeout=10)
            except subprocess.TimeoutExpired:
                app.kill()
            if app_log:
                app_log.close()
        if mock.poll() is None:
            mock.terminate()
            try:
                mock.wait(timeout=10)
            except subprocess.TimeoutExpired:
                mock.kill()
        # KEEP_TMP=1 leaves the throwaway copy (and app.log) behind for debugging
        if os.environ.get("KEEP_TMP"):
            print(f"\n[kept] {tmp}")
            try:
                print("--- app.log tail ---")
                print("\n".join((tmp / "app.log").read_text().splitlines()[-40:]))
            except Exception as e:
                print(f"(no log: {e})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
