"""OpenAI-compatible mock endpoint for streaming checks.

    python tests/mock_llm.py --port 8999 [--reject-stream]

Replies are streamed word by word (40 ms apart) so a correct client sees
many frames over time instead of one lump. Behaviour is steered by the
message text:

  TRUNCATE   first call answers with finish_reason="length", the follow-up
             (auto-continue) call answers "stop" — exercises auto-continue
  NOPREFIX   omits the leading "[Name]:" RP prefix

--reject-stream answers 400 to any request carrying stream=true, which is
what an older OpenAI-compatible server does; the client must fall back to a
plain request rather than break.
"""
import argparse
import asyncio
import json
import os
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

REPLY = (
    "The afternoon light stretched across the floor as she set the cup down "
    "and waited, fingers folded, watching the door as if the answer might "
    "walk through it at any moment now, unhurried and entirely uninvited"
)
CONTINUED = ", and that is exactly how the rest of it went"
TRUNCATED = REPLY[: len(REPLY) // 2]
# Long enough that any sane per-style token budget cuts it mid-sentence,
# and punctuated so the client has sentence boundaries to trim back to
LONG_REPLY = " ".join([REPLY + "." for _ in range(13)])

# Same assumption the client budgets with (engine._TOKENS_PER_WORD)
TOKENS_PER_WORD = 1.4
WORDS_PER_FRAME = 2
FRAME_DELAY = float(os.environ.get("MOCK_FRAME_DELAY", "0.04"))
REJECT_STREAM = False


def speaker_from(messages) -> str:
    """Pull the character name the prompt declares, if any."""
    for m in messages:
        content = m.get("content") or ""
        match = re.search(r"--- (.+?) ---", content) or re.search(r"=== YOU ARE (.+?) ===", content)
        if match:
            return match.group(1).strip()
    return "Ramlee"


def is_roleplay(messages) -> bool:
    return any("CHARACTERS IN THIS SCENE" in (m.get("content") or "") for m in messages)


def last_user_text(messages) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()

    if body.get("stream") and REJECT_STREAM:
        return JSONResponse(
            {"error": {"message": "Unknown parameter: 'stream'", "type": "invalid_request_error",
                       "param": "stream", "code": "unknown_parameter"}},
            status_code=400,
        )

    messages = body.get("messages", [])
    user_text = last_user_text(messages)
    is_continuation = bool(messages) and messages[-1].get("role") == "assistant"

    if is_continuation:
        reply, finish = CONTINUED, "stop"
    elif "TRUNCATE" in user_text:
        reply, finish = TRUNCATED, "length"
    elif "LONGREPLY" in user_text:
        reply, finish = LONG_REPLY, "stop"
    else:
        reply, finish = REPLY, "stop"

    prefix = ""
    if is_roleplay(messages) and "NOPREFIX" not in user_text:
        prefix = f"[{speaker_from(messages)}]: "

    full = prefix + reply
    max_tokens = body.get("max_tokens") or 4096

    # Honour max_tokens like a real server: stop mid-reply when the budget
    # runs out, so the client's response-style cap is actually exercised.
    max_words = max(1, int(max_tokens / TOKENS_PER_WORD))
    words = full.split(" ")
    if len(words) > max_words:
        full = " ".join(words[:max_words])
        finish = "length"

    if not body.get("stream"):
        return {
            "id": "chatcmpl-mock", "object": "chat.completion", "created": 1, "model": body.get("model", "mock"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": full},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 12, "completion_tokens": len(full.split()),
                      "total_tokens": 12 + len(full.split())},
        }

    async def event_stream():
        words = full.split(" ")
        chunks = [words[i:i + WORDS_PER_FRAME] for i in range(0, len(words), WORDS_PER_FRAME)]
        for i, group in enumerate(chunks):
            text = (" " if i else "") + " ".join(group)
            is_last = i == len(chunks) - 1
            frame = {
                "id": "chatcmpl-mock", "object": "chat.completion.chunk", "created": 1,
                "model": body.get("model", "mock"),
                "choices": [{"index": 0, "delta": {"content": text},
                             "finish_reason": finish if is_last else None}],
            }
            yield f"data: {json_dumps(frame)}\n\n"
            await asyncio_sleep(FRAME_DELAY)
        if (body.get("stream_options") or {}).get("include_usage"):
            yield ("data: " + json_dumps({
                "id": "chatcmpl-mock", "object": "chat.completion.chunk", "created": 1,
                "model": body.get("model", "mock"), "choices": [],
                "usage": {"prompt_tokens": 12, "completion_tokens": len(full.split()),
                          "total_tokens": 12 + len(full.split())},
            }) + "\n\n")
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def json_dumps(obj) -> str:
    return json.dumps(obj)


async def asyncio_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def main() -> None:
    global REJECT_STREAM
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8999)
    parser.add_argument("--reject-stream", action="store_true")
    args = parser.parse_args()
    REJECT_STREAM = args.reject_stream
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error")


if __name__ == "__main__":
    main()
