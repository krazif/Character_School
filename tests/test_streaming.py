"""Tests for engine.stream_chat_completion — the streaming chat path.

Stdlib only, no network: the endpoint is faked. Run from the repo root:

    python3 -m unittest discover -s tests -v
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import engine


# ─── Fakes ───────────────────────────────────────────────────────

def delta_frame(text, finish=None, reasoning=None):
    """One SSE frame carrying content (and optionally finish_reason)."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=text, reasoning_content=reasoning),
            finish_reason=finish,
        )],
        usage=None,
    )


def usage_frame(prompt_tokens, completion_tokens):
    """The trailing usage-only frame sent when stream_options asks for it."""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def plain_response(content, finish="stop", usage=None):
    """A non-streaming ChatCompletion-shaped object."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish,
        )],
        usage=usage,
    )


class FakeAPIError(Exception):
    """Error carrying the status code the SDK attaches to API errors."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FakeEndpoint:
    """Stands in for AsyncOpenAI.chat.completions.

    *script* holds one item per create() call:
      - list          → frames of a stream to return
      - Exception     → raise it
      - anything else → return it as a non-streaming response
    """

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._script:
            raise AssertionError("endpoint called more times than the script provides")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, list):
            async def _stream():
                for frame in item:
                    yield frame
            return _stream()
        return item


def make_client(script):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeEndpoint(script)))


def collect():
    """Return (on_delta, deltas) where deltas is a list of (text, speaker)."""
    seen = []

    async def on_delta(text, speaker=None):
        seen.append((text, speaker))

    return on_delta, seen


# ─── Tests ───────────────────────────────────────────────────────

class StreamChatCompletionTests(unittest.TestCase):

    def test_streams_and_assembles_full_content(self):
        on_delta, seen = collect()
        client = make_client([
            [delta_frame("Hel"), delta_frame("lo "), delta_frame("world", finish="stop"),
             usage_frame(10, 3)],
        ])
        kwargs = dict(model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

        resp = asyncio.run(engine.stream_chat_completion(client, kwargs, on_delta=on_delta))

        self.assertEqual(resp.choices[0].message.content, "Hello world")
        self.assertEqual("".join(t for t, _ in seen), "Hello world")
        self.assertEqual(resp.choices[0].finish_reason, "stop")
        self.assertEqual(resp.usage.completion_tokens, 3)
        # Usage only comes back if we ask for it
        self.assertTrue(client.chat.completions.calls[0]["stream"])
        self.assertEqual(
            client.chat.completions.calls[0]["stream_options"], {"include_usage": True},
        )

    def test_deltas_are_throttled_but_lossless(self):
        on_delta, seen = collect()
        frames = [delta_frame(str(i % 10)) for i in range(500)]
        frames.append(delta_frame("", finish="stop"))
        client = make_client([frames])
        kwargs = dict(model="m", messages=[])

        resp = asyncio.run(engine.stream_chat_completion(client, kwargs, on_delta=on_delta))

        expected = "".join(str(i % 10) for i in range(500))
        self.assertEqual(resp.choices[0].message.content, expected)
        self.assertEqual("".join(t for t, _ in seen), expected)
        # 500 tokens must not become 500 websocket frames
        self.assertLess(len(seen), 50)

    def test_rp_prefix_is_held_then_dropped(self):
        on_delta, seen = collect()
        client = make_client([[
            delta_frame("[Li"), delta_frame("sa]: "), delta_frame("Hello"), delta_frame(" there"),
        ]])
        kwargs = dict(model="m", messages=[])

        resp = asyncio.run(engine.stream_chat_completion(
            client, kwargs, on_delta=on_delta, hold_prefix=True,
        ))

        # The client never sees the prefix the final message will drop...
        self.assertEqual("".join(t for t, _ in seen), "Hello there")
        # ...but the caller still gets it, because parsing needs it
        self.assertEqual(resp.choices[0].message.content, "[Lisa]: Hello there")
        self.assertEqual(resp.speaker, "Lisa")
        self.assertEqual(seen[0][1], "Lisa")

    def test_space_after_prefix_is_dropped(self):
        # A frame boundary can land exactly on ":", leaving the space that
        # follows it for the next frame. The stored message strips that space,
        # so the bubble has to strip it too or the two texts disagree.
        on_delta, seen = collect()
        client = make_client([[
            delta_frame("[P. Ramlee]:"),
            delta_frame(" The afternoon light"),
        ]])
        kwargs = dict(model="m", messages=[])

        resp = asyncio.run(engine.stream_chat_completion(
            client, kwargs, on_delta=on_delta, hold_prefix=True,
        ))

        self.assertEqual("".join(t for t, _ in seen), "The afternoon light")
        self.assertEqual(resp.choices[0].message.content, "[P. Ramlee]: The afternoon light")

    def test_reply_without_prefix_streams_immediately(self):
        on_delta, seen = collect()
        client = make_client([[delta_frame("Hi "), delta_frame("there")]])
        kwargs = dict(model="m", messages=[])

        resp = asyncio.run(engine.stream_chat_completion(
            client, kwargs, on_delta=on_delta, hold_prefix=True,
        ))

        self.assertEqual("".join(t for t, _ in seen), "Hi there")
        self.assertEqual(resp.speaker, None)

    def test_unterminated_prefix_is_never_dropped(self):
        # A leading "[" that never closes must still reach the client
        on_delta, seen = collect()
        body = "[" + "x" * 120
        client = make_client([[delta_frame(body), delta_frame("", finish="stop")]])
        kwargs = dict(model="m", messages=[])

        resp = asyncio.run(engine.stream_chat_completion(
            client, kwargs, on_delta=on_delta, hold_prefix=True,
        ))

        self.assertEqual(resp.choices[0].message.content, body)
        self.assertEqual("".join(t for t, _ in seen), body)

    def test_reasoning_deltas_are_not_chat_output(self):
        on_delta, seen = collect()
        client = make_client([[
            delta_frame(None, reasoning="thinking out loud"),
            delta_frame("The answer"),
        ]])
        kwargs = dict(model="m", messages=[])

        resp = asyncio.run(engine.stream_chat_completion(client, kwargs, on_delta=on_delta))

        self.assertEqual(resp.choices[0].message.content, "The answer")
        self.assertEqual("".join(t for t, _ in seen), "The answer")

    def test_finish_reason_inferred_from_max_tokens(self):
        # Endpoint omitted finish_reason, but it used every last token
        client = make_client([[
            delta_frame("cut off here"),
            usage_frame(10, 100),
        ]])
        kwargs = dict(model="m", messages=[], max_tokens=100)

        resp = asyncio.run(engine.stream_chat_completion(client, kwargs))

        self.assertEqual(resp.choices[0].finish_reason, "length")

    def test_falls_back_when_stream_options_rejected(self):
        on_delta, seen = collect()
        client = make_client([
            FakeAPIError("stream_options is not supported", 400),
            [delta_frame("works anyway", finish="stop")],
        ])
        kwargs = dict(model="m", messages=[])

        resp = asyncio.run(engine.stream_chat_completion(client, kwargs, on_delta=on_delta))

        calls = client.chat.completions.calls
        self.assertEqual(len(calls), 2)
        self.assertIn("stream_options", calls[0])
        self.assertNotIn("stream_options", calls[1])   # retried without it
        self.assertTrue(calls[1]["stream"])
        self.assertEqual(resp.choices[0].message.content, "works anyway")
        self.assertEqual("".join(t for t, _ in seen), "works anyway")

    def test_falls_back_to_plain_call_when_stream_rejected(self):
        client = make_client([
            FakeAPIError("stream_options is not supported", 400),
            FakeAPIError("streaming not supported here", 400),
            plain_response("whole reply at once"),
        ])
        kwargs = dict(model="m", messages=[])

        on_delta, seen = collect()
        resp = asyncio.run(engine.stream_chat_completion(client, kwargs, on_delta=on_delta))

        calls = client.chat.completions.calls
        self.assertEqual(len(calls), 3)
        self.assertNotIn("stream", calls[2])
        self.assertEqual(resp.choices[0].message.content, "whole reply at once")
        self.assertEqual(resp.choices[0].finish_reason, "stop")
        # Nothing to stream, so no deltas
        self.assertEqual(seen, [])

    def test_rate_limit_is_raised_without_retrying(self):
        client = make_client([FakeAPIError("slow down", 429)])
        kwargs = dict(model="m", messages=[])

        with self.assertRaises(FakeAPIError):
            asyncio.run(engine.stream_chat_completion(client, kwargs))
        self.assertEqual(len(client.chat.completions.calls), 1)

    def test_connection_error_is_raised_without_retrying(self):
        client = make_client([FakeAPIError("Connection error.")])
        kwargs = dict(model="m", messages=[])

        with self.assertRaises(FakeAPIError):
            asyncio.run(engine.stream_chat_completion(client, kwargs))
        self.assertEqual(len(client.chat.completions.calls), 1)


class AutoContinueTests(unittest.TestCase):

    def test_continuation_streams_into_the_same_bubble(self):
        on_delta, seen = collect()
        client = make_client([
            [delta_frame(" and it kept going", finish="stop")],
        ])
        kwargs = dict(model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=50)
        initial = engine.StreamedCompletion("First half", "length", None)

        content, finish = asyncio.run(engine.maybe_auto_continue(
            client, kwargs, initial, True, "moderate", on_delta=on_delta,
        ))

        self.assertEqual(content, "First half and it kept going")
        self.assertEqual(finish, "stop")
        self.assertEqual("".join(t for t, _ in seen), " and it kept going")
        # The continuation must ask for streaming too
        self.assertTrue(client.chat.completions.calls[0]["stream"])

    def test_no_continuation_when_not_truncated(self):
        client = make_client([])   # script empty — any call would fail the test
        kwargs = dict(model="m", messages=[])
        initial = engine.StreamedCompletion("done", "stop", None)

        content, finish = asyncio.run(engine.maybe_auto_continue(
            client, kwargs, initial, True, "moderate",
        ))

        self.assertEqual((content, finish), ("done", "stop"))
        self.assertEqual(client.chat.completions.calls, [])


if __name__ == "__main__":
    unittest.main()
