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
        # 150 words ≈ 230 tokens at the measured 1.44–1.59 tokens/word, but the
        # model's natural reply overshoots the target (its opener is 500+ words),
        # so the ceiling sits far above the word target and must not bite.
        self.assertEqual(engine.response_style_max_tokens("short", 2000), 800)
        self.assertGreater(800, 150 * 1.59)

    def test_moderate_ceiling_has_headroom_above_the_word_target(self):
        self.assertEqual(engine.response_style_max_tokens("moderate", 2000), 1100)
        self.assertGreater(1100, 300 * 1.59)

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
        # densest measured ratio (1.59 tok/word) 800 tokens is ~500 words —
        # well short of an unbounded reply, and short/flexible stay apart.
        self.assertLess(800 / 1.44, 600)
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


class LengthNoteTests(unittest.TestCase):
    """length_note_for_reply / append_length_note — the author's-note position."""

    def test_short_note_states_the_target(self):
        note = engine.length_note_for_reply("short", ["a brief reply."]) or ""
        self.assertIn("under 150 words", note)
        self.assertNotIn("ran longer", note)

    def test_history_aware_clause_only_when_recent_replies_overshot(self):
        long_reply = " ".join(["word"] * 200)   # 200 words > 150 bound
        note = engine.length_note_for_reply("short", [long_reply]) or ""
        self.assertIn("ran longer", note)
        note = engine.length_note_for_reply("moderate", [long_reply]) or ""
        self.assertNotIn("ran longer", note)     # 200 < 300 bound

    def test_only_the_last_three_replies_count(self):
        old_long = " ".join(["word"] * 200)
        recent_ok = "a short reply."
        note = engine.length_note_for_reply("short", [old_long, old_long, old_long,
                                                      recent_ok, recent_ok, recent_ok]) or ""
        self.assertNotIn("ran longer", note)

    def test_flexible_and_unknown_get_no_note(self):
        self.assertIsNone(engine.length_note_for_reply("flexible", []))
        self.assertIsNone(engine.length_note_for_reply(None, []))
        self.assertIsNone(engine.length_note_for_reply("weird", []))

    def test_long_style_note_exists_but_never_blames_history(self):
        note = engine.length_note_for_reply("long", [" ".join(["word"] * 5)]) or ""
        self.assertIn("300 words", note)
        self.assertNotIn("ran longer", note)

    def test_note_appends_to_the_last_user_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        out = engine.append_length_note(msgs, "[Reply length — ...]")
        self.assertEqual(out[-1]["role"], "user")
        self.assertEqual(out[-1]["content"], "hello\n\n[Reply length — ...]")
        # input list untouched
        self.assertEqual(msgs[-1]["content"], "hello")

    def test_note_becomes_its_own_message_when_last_is_assistant(self):
        msgs = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "there"}]
        out = engine.append_length_note(msgs, "[n]")
        self.assertEqual(len(out), 3)
        self.assertEqual(out[-1], {"role": "user", "content": "[n]"})

    def test_none_note_changes_nothing(self):
        msgs = [{"role": "user", "content": "hi"}]
        self.assertIs(engine.append_length_note(msgs, None), msgs)

    def test_system_prompt_instruction_still_present(self):
        self.assertIn("under 150 words", engine.response_style_instruction("short") or "")
        self.assertIsNone(engine.response_style_instruction(None))


if __name__ == "__main__":
    unittest.main()
