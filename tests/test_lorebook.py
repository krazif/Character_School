"""Lorebook unit tests: keyword matching, ST insertion order, placement.

Covers the three bugs fixed 2026-11-21:
  1. order was sorted DESCENDING (highest order furthest from chat) — ST
     puts larger order numbers closer to the end of the context;
  2. the Position dropdown did nothing — placement is now real;
  3. zero test coverage for this subsystem.
Also covers the silent-drop failure mode: injection with no leading
system message used to vanish without a trace.
"""
import asyncio
import json
import unittest

import lorebook


def entry(**kw):
    base = {"uid": 0, "key": [], "keysecondary": [], "comment": "", "content": "",
            "constant": False, "selective": False, "order": 100, "position": 1,
            "disable": False, "extensions": {}}
    base.update(kw)
    return base


def book(name="Book", entries=None):
    return {"name": name, "description": "",
            "entries": {str(i): e for i, e in enumerate(entries or [])}}


def msgs(*contents):
    return [{"role": "user" if i % 2 == 0 else "character", "content": c}
            for i, c in enumerate(contents)]


class TestKeywordMatching(unittest.TestCase):
    def test_keyword_triggers(self):
        e = entry(key=["sword"], content="A blade.")
        act = lorebook.scan_for_active_entries(book(entries=[e]), msgs("he drew the SWORD"))
        self.assertEqual(len(act), 1)

    def test_no_keyword_no_trigger(self):
        e = entry(key=["sword"], content="A blade.")
        act = lorebook.scan_for_active_entries(book(entries=[e]), msgs("just chatting"))
        self.assertEqual(act, [])

    def test_multiphrase_keyword(self):
        e = entry(key=["dark tower"], content="Lo!")
        self.assertEqual(len(lorebook.scan_for_active_entries(
            book(entries=[e]), msgs("the Dark Tower rose"))), 1)
        self.assertEqual(lorebook.scan_for_active_entries(
            book(entries=[e]), msgs("the tower was dark")), [])

    def test_selective_needs_both(self):
        e = entry(key=["tower"], keysecondary=["dark"], selective=True, content="Lo!")
        self.assertEqual(len(lorebook.scan_for_active_entries(
            book(entries=[e]), msgs("a dark tower"))), 1)
        self.assertEqual(lorebook.scan_for_active_entries(
            book(entries=[e]), msgs("a bright tower")), [])

    def test_disabled_never_fires_even_if_constant(self):
        e = entry(key=["tower"], content="x", constant=True, disable=True)
        self.assertEqual(lorebook.scan_for_active_entries(
            book(entries=[e]), msgs("tower")), [])

    def test_scan_depth_window(self):
        e = entry(key=["apple"], content="x")
        old = [{"role": "user", "content": c}
               for c in ("apple", "filler one", "filler two")]
        self.assertEqual(lorebook.scan_for_active_entries(book(entries=[e]), old, scan_depth=2), [])
        self.assertEqual(len(lorebook.scan_for_active_entries(
            book(entries=[e]), old, scan_depth=3)), 1)


class TestInsertionOrder(unittest.TestCase):
    """ST: constants first, then ascending order — order 100 appears BEFORE
    order 250, so higher order sits closer to the end of the context."""

    def test_scan_sorts_constants_first_then_ascending(self):
        entries = [
            entry(key=["x"], order=250, content="late"),
            entry(key=["x"], order=100, content="early"),
            entry(key=["x"], order=50, content="const", constant=True),
        ]
        act = lorebook.scan_for_active_entries(book(entries=entries), msgs("x"))
        self.assertEqual([e["content"] for e in act], ["const", "early", "late"])

    def test_block_order_higher_order_last(self):
        entries = [
            entry(key=["x"], order=250, content="LATE-CONTENT"),
            entry(key=["x"], order=100, content="EARLY-CONTENT"),
        ]
        blocks = lorebook.build_lorebook_blocks([book(entries=entries)], msgs("x"))
        text = blocks[lorebook.POSITION_AFTER_SYSTEM]
        self.assertLess(text.index("EARLY-CONTENT"), text.index("LATE-CONTENT"))

    def test_order_merges_across_books(self):
        b1 = book("Alpha", [entry(key=["x"], order=200, content="A200")])
        b2 = book("Beta", [entry(key=["x"], order=100, content="B100")])
        blocks = lorebook.build_lorebook_blocks([b1, b2], msgs("x"))
        text = blocks[lorebook.POSITION_AFTER_SYSTEM]
        self.assertLess(text.index("B100"), text.index("A200"))

    def test_constants_stay_above_ordered_entries(self):
        entries = [
            entry(key=["x"], order=900, content="ordered-high"),
            entry(key=["x"], order=1, content="the-constant", constant=True),
        ]
        blocks = lorebook.build_lorebook_blocks([book(entries=entries)], msgs("x"))
        text = blocks[lorebook.POSITION_AFTER_SYSTEM]
        self.assertLess(text.index("the-constant"), text.index("ordered-high"))


class TestPlacement(unittest.TestCase):
    def _msgs(self):
        return [{"role": "system", "content": "SYS"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"}]

    def test_position_0_prepends_to_system(self):
        m = self._msgs()
        lorebook.apply_lorebook_blocks(
            m, {lorebook.POSITION_BEFORE_SYSTEM: "BEFORE", lorebook.POSITION_AFTER_SYSTEM: "",
                lorebook.POSITION_BEFORE_CHAT: ""})
        self.assertTrue(m[0]["content"].startswith("BEFORE"))
        self.assertIn("SYS", m[0]["content"])
        self.assertEqual(len(m), 3)

    def test_position_1_appends_to_system(self):
        m = self._msgs()
        lorebook.apply_lorebook_blocks(
            m, {lorebook.POSITION_BEFORE_SYSTEM: "", lorebook.POSITION_AFTER_SYSTEM: "AFTER",
                lorebook.POSITION_BEFORE_CHAT: ""})
        self.assertTrue(m[0]["content"].endswith("AFTER"))
        self.assertTrue(m[0]["content"].startswith("SYS"))
        self.assertEqual(len(m), 3)

    def test_position_2_inserts_own_message_before_first_user(self):
        m = self._msgs()
        lorebook.apply_lorebook_blocks(
            m, {lorebook.POSITION_BEFORE_SYSTEM: "", lorebook.POSITION_AFTER_SYSTEM: "",
                lorebook.POSITION_BEFORE_CHAT: "CHATLORE"})
        self.assertEqual(len(m), 4)
        self.assertEqual(m[1], {"role": "system", "content": "CHATLORE"})
        self.assertEqual(m[2]["content"], "hello")

    def test_all_three_positions_together(self):
        m = self._msgs()
        lorebook.apply_lorebook_blocks(
            m, {lorebook.POSITION_BEFORE_SYSTEM: "P0", lorebook.POSITION_AFTER_SYSTEM: "P1",
                lorebook.POSITION_BEFORE_CHAT: "P2"})
        self.assertTrue(m[0]["content"].startswith("P0"))
        self.assertTrue(m[0]["content"].endswith("P1"))
        self.assertEqual(m[1]["content"], "P2")
        self.assertEqual(len(m), 4)

    def test_no_leading_system_message_gets_created(self):
        # The old code silently DROPPED the lore when llm_messages[0]
        # wasn't a system message.
        m = [{"role": "user", "content": "hello"}]
        out = lorebook.apply_lorebook_blocks(
            m, {lorebook.POSITION_BEFORE_SYSTEM: "P0", lorebook.POSITION_AFTER_SYSTEM: "P1",
                lorebook.POSITION_BEFORE_CHAT: ""})
        self.assertEqual(out, "P0\n\nP1")
        self.assertEqual(m[0]["role"], "system")
        self.assertIn("P0", m[0]["content"])
        self.assertIn("P1", m[0]["content"])
        self.assertEqual(m[1]["content"], "hello")

    def test_empty_blocks_no_op(self):
        m = [{"role": "system", "content": "SYS"}]
        out = lorebook.apply_lorebook_blocks(
            m, {lorebook.POSITION_BEFORE_SYSTEM: "", lorebook.POSITION_AFTER_SYSTEM: "",
                lorebook.POSITION_BEFORE_CHAT: ""})
        self.assertEqual(out, "")
        self.assertEqual(m, [{"role": "system", "content": "SYS"}])

    def test_build_defaults_to_after_system(self):
        # No position field on the entry -> after system (old behavior)
        e = entry(key=["x"], content="C")
        del e["position"]
        blocks = lorebook.build_lorebook_blocks([book(entries=[e])], msgs("x"))
        self.assertIn("C", blocks[lorebook.POSITION_AFTER_SYSTEM])
        self.assertEqual(blocks[lorebook.POSITION_BEFORE_SYSTEM], "")
        self.assertEqual(blocks[lorebook.POSITION_BEFORE_CHAT], "")

    def test_build_groups_by_position(self):
        entries = [
            entry(key=["x"], content="IN-SYS", position=0),
            entry(key=["x"], content="IN-CHAT", position=2),
        ]
        blocks = lorebook.build_lorebook_blocks([book(entries=entries)], msgs("x"))
        self.assertIn("IN-SYS", blocks[0])
        self.assertNotIn("IN-CHAT", blocks[0])
        self.assertIn("IN-CHAT", blocks[2])
        self.assertEqual(blocks[1], "")


class TestInjectLorebook(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_no_lorebooks_no_event(self):
        sent = []

        async def send(d):
            sent.append(d)

        m = [{"role": "system", "content": "SYS"}]
        out = self._run(lorebook.inject_lorebook(m, {"lorebooks": "[]"}, [], send))
        self.assertEqual(out, "")
        self.assertEqual(sent, [])

    def test_bad_json_lorebooks_field_is_tolerated(self):
        sent = []

        async def send(d):
            sent.append(d)

        m = [{"role": "system", "content": "SYS"}]
        out = self._run(lorebook.inject_lorebook(m, {"lorebooks": "not json"}, [], send))
        self.assertEqual(out, "")
        self.assertEqual(sent, [])

    def test_missing_filename_skipped(self):
        sent = []

        async def send(d):
            sent.append(d)

        m = [{"role": "system", "content": "SYS"}]
        out = self._run(lorebook.inject_lorebook(
            m, {"lorebooks": json.dumps(["does_not_exist.json"])}, [], send))
        self.assertEqual(out, "")
        self.assertEqual(sent, [])


class TestBuildBlocks(unittest.TestCase):
    def test_no_active_entries_returns_empty_strings(self):
        blocks = lorebook.build_lorebook_blocks(
            [book(entries=[entry(key=["zebra"], content="x")])], msgs("nothing here"))
        self.assertEqual(set(blocks.values()), {""})

    def test_empty_content_entry_is_skipped(self):
        blocks = lorebook.build_lorebook_blocks(
            [book(entries=[entry(key=["x"], content="   ", constant=True)])], msgs("x"))
        self.assertEqual(set(blocks.values()), {""})

    def test_label_uses_comment_or_placeholder(self):
        e = entry(key=["x"], content="C", comment="The Tower", constant=True)
        blocks = lorebook.build_lorebook_blocks([book("MyBook", [e])], msgs("x"))
        self.assertIn("[MyBook — The Tower]", blocks[1])
        e2 = entry(key=["x"], content="C2", constant=True)
        blocks = lorebook.build_lorebook_blocks([book("B2", [e2])], msgs("x"))
        self.assertIn("[B2 — (unnamed)]", blocks[1])


if __name__ == "__main__":
    unittest.main()
