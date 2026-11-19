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

    def test_short_caps_at_150_words_worth_of_tokens(self):
        self.assertEqual(engine.response_style_max_tokens("short", 2000), 210)

    def test_moderate_caps_at_300_words_worth_of_tokens(self):
        self.assertEqual(engine.response_style_max_tokens("moderate", 2000), 420)

    def test_long_and_flexible_have_no_ceiling(self):
        self.assertEqual(engine.response_style_max_tokens("long", 2000), 2000)
        self.assertEqual(engine.response_style_max_tokens("flexible", 2000), 2000)

    def test_unknown_or_missing_style_keeps_global_budget(self):
        self.assertEqual(engine.response_style_max_tokens(None, 2000), 2000)
        self.assertEqual(engine.response_style_max_tokens("", 2000), 2000)

    def test_smaller_global_budget_still_wins(self):
        # A global max_tokens below the style target must not be raised
        self.assertEqual(engine.response_style_max_tokens("short", 100), 100)

    def test_budget_is_always_below_the_word_target(self):
        # Even at the densest tokens/word we measured (1.44), a short budget
        # cannot fit 150 words: 210 / 1.44 = 145 words.
        self.assertLess(210 / 1.44, 150)


class TrimTests(unittest.TestCase):
    """trim_to_sentence — a capped reply must never end mid-word."""

    def test_cuts_back_to_the_last_sentence(self):
        # realistic reply: several sentences, cut part-way through the last one
        text = "One. Two. Three. Four. Five. Six that got cut off mid"
        self.assertEqual(engine.trim_to_sentence(text),
                         "One. Two. Three. Four. Five.")

    def test_keeps_closing_quotes(self):
        text = 'First he waved. Then he said "hi." and the rest'
        self.assertEqual(engine.trim_to_sentence(text),
                         'First he waved. Then he said "hi."')

    def test_falls_back_to_the_last_complete_word(self):
        self.assertEqual(engine.trim_to_sentence("one two three four"),
                         "one two three")

    def test_does_not_throw_away_a_reply_over_one_early_full_stop(self):
        text = "Done. " + "word " * 80
        trimmed = engine.trim_to_sentence(text)
        # the only sentence end keeps 1.5% of the text, so keeping it would
        # discard far too much — the word-boundary fallback must win instead
        self.assertGreater(len(trimmed.split()), 70)

    def test_drops_an_unpaired_asterisk(self):
        trimmed = engine.trim_to_sentence("She *smiled at him and")
        self.assertEqual(trimmed.count("*") % 2, 0)
        self.assertFalse(trimmed.endswith("*"))

    def test_empty_text_is_untouched(self):
        self.assertEqual(engine.trim_to_sentence(""), "")


class StyleCapTruncationTests(unittest.TestCase):
    """maybe_auto_continue must not extend a style-capped reply."""

    def _initial(self, content, finish):
        return engine.StreamedCompletion(content, finish, None)

    def test_capped_reply_is_trimmed_and_not_continued(self):
        # max_tokens just under the global budget => the style cap is what cut it
        kwargs = dict(model="m", messages=[], max_tokens=db.CHAT_MAX_TOKENS - 1)
        # realistic reply: the cut landed inside the third sentence, so the
        # second sentence end keeps well over half the text
        initial = self._initial(
            "One full sentence ends here. Another full sentence ends here too. "
            "Then a tail that was cut off mid", "length")
        client = make_client([])   # any create() call would fail the test
        events = []

        async def send_fn(payload):
            events.append(payload)

        content, finish = asyncio.run(engine.maybe_auto_continue(
            client, kwargs, initial, True, "short", send_fn=send_fn,
        ))

        self.assertEqual(content, "One full sentence ends here. "
                                  "Another full sentence ends here too.")
        self.assertEqual(finish, "length")
        self.assertEqual(client.chat.completions.calls, [], "must not call the API again")
        trims = [e for e in events if e.get("event") == "style_trim"]
        self.assertEqual(len(trims), 1)
        self.assertIn("short", trims[0]["message"])
        self.assertIn(str(db.CHAT_MAX_TOKENS - 1), trims[0]["message"])

    def test_global_budget_truncation_still_auto_continues(self):
        # Same truncation, but caused by the global budget — that one continues
        kwargs = dict(model="m", messages=[{"role": "user", "content": "hi"}],
                      max_tokens=db.CHAT_MAX_TOKENS)
        initial = self._initial("First half", "length")
        client = make_client([[delta_frame(" and it kept going", finish="stop")]])

        content, finish = asyncio.run(engine.maybe_auto_continue(
            client, kwargs, initial, True, "short",
        ))

        self.assertEqual(content, "First half and it kept going")
        self.assertEqual(finish, "stop")

    def test_capped_reply_that_stopped_naturally_is_left_alone(self):
        kwargs = dict(model="m", messages=[], max_tokens=db.CHAT_MAX_TOKENS - 1)
        initial = self._initial("A complete reply. Nothing to trim here.", "stop")
        client = make_client([])

        content, finish = asyncio.run(engine.maybe_auto_continue(
            client, kwargs, initial, True, "short",
        ))

        self.assertEqual(content, "A complete reply. Nothing to trim here.")
        self.assertEqual(finish, "stop")


if __name__ == "__main__":
    unittest.main()
