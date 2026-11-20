"""Tests for response-style length enforcement.

    python3 -m unittest discover -s tests
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db
import engine
from test_streaming import delta_frame, make_client


class StyleBudgetTests(unittest.TestCase):
    """response_style_max_tokens — the hard ceiling per style."""

    def test_short_ceiling_has_headroom_above_the_word_target(self):
        # 150 words ≈ 230 tokens at the measured 1.44–1.59 tokens/word; the
        # ceiling sits well above that so a short reply can finish on its own.
        self.assertEqual(engine.response_style_max_tokens("short", 2000), 450)
        self.assertGreater(450, 150 * 1.59)

    def test_moderate_ceiling_has_headroom_above_the_word_target(self):
        self.assertEqual(engine.response_style_max_tokens("moderate", 2000), 700)
        self.assertGreater(700, 300 * 1.59)

    def test_long_and_flexible_have_no_ceiling(self):
        self.assertEqual(engine.response_style_max_tokens("long", 2000), 2000)
        self.assertEqual(engine.response_style_max_tokens("flexible", 2000), 2000)

    def test_unknown_or_missing_style_keeps_global_budget(self):
        self.assertEqual(engine.response_style_max_tokens(None, 2000), 2000)
        self.assertEqual(engine.response_style_max_tokens("", 2000), 2000)

    def test_smaller_global_budget_still_wins(self):
        # A global max_tokens below the style target must not be raised
        self.assertEqual(engine.response_style_max_tokens("short", 100), 100)

    def test_ceiling_still_cuts_a_runaway_reply(self):
        # The headroom must not make the ceiling useless: at the model's
        # densest measured ratio (1.59 tok/word) 450 tokens is ~283 words —
        # well short of an unbounded reply, and short/flexible stay apart.
        self.assertLess(450 / 1.44, 400)
        self.assertLess(engine.response_style_max_tokens("short", 2000),
                        engine.response_style_max_tokens("moderate", 2000))



class StyleCapTruncationTests(unittest.TestCase):
    """maybe_auto_continue must not extend a style-capped reply."""

    def _initial(self, content, finish):
        return engine.StreamedCompletion(content, finish, None)

    def test_capped_reply_is_left_as_is_and_not_continued(self):
        # The cap stopped the model mid-sentence. Every token it did produce
        # is kept (nothing is trimmed) and the API must not be called again.
        kwargs = dict(model="m", messages=[], max_tokens=db.CHAT_MAX_TOKENS - 1)
        raw = "One full sentence ends here. Then a tail that was cut off mid"
        initial = self._initial(raw, "length")
        client = make_client([])   # any create() call would fail the test
        events = []

        async def send_fn(payload):
            events.append(payload)

        content, finish = asyncio.run(engine.maybe_auto_continue(
            client, kwargs, initial, True, send_fn=send_fn,
        ))

        self.assertEqual(content, raw)
        self.assertEqual(finish, "length")
        self.assertEqual(client.chat.completions.calls, [], "must not call the API again")
        self.assertEqual(events, [], "must not log a trim event")

    def test_global_budget_truncation_still_auto_continues(self):
        # Same truncation, but caused by the global budget — that one continues
        kwargs = dict(model="m", messages=[{"role": "user", "content": "hi"}],
                      max_tokens=db.CHAT_MAX_TOKENS)
        initial = self._initial("First half", "length")
        client = make_client([[delta_frame(" and it kept going", finish="stop")]])

        content, finish = asyncio.run(engine.maybe_auto_continue(
            client, kwargs, initial, True,
        ))

        self.assertEqual(content, "First half and it kept going")
        self.assertEqual(finish, "stop")

    def test_capped_reply_that_stopped_naturally_is_left_alone(self):
        kwargs = dict(model="m", messages=[], max_tokens=db.CHAT_MAX_TOKENS - 1)
        initial = self._initial("A complete reply. Nothing to trim here.", "stop")
        client = make_client([])

        content, finish = asyncio.run(engine.maybe_auto_continue(
            client, kwargs, initial, True,
        ))

        self.assertEqual(content, "A complete reply. Nothing to trim here.")
        self.assertEqual(finish, "stop")


if __name__ == "__main__":
    unittest.main()
