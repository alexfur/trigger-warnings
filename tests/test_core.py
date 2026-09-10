"""Unit tests for deterministic subtitle logic in trigger_warnings.core."""

import unittest

from trigger_warnings.core import (
    WARNING_TEXT,
    Cue,
    TriggerWarningsError,
    Window,
    merge_cues,
    render,
    render_ass,
    render_srt,
    verify_target_ms,
    warning_cues,
)


class TestWarningCues(unittest.TestCase):
    def test_warning_cues_converts_windows_to_warning_cues(self):
        windows = [Window(10000, 25000), Window(30000, 45000)]
        cues = warning_cues(windows)
        self.assertEqual(2, len(cues))

        self.assertEqual(10000, cues[0].start_ms)
        self.assertEqual(25000, cues[0].end_ms)
        self.assertEqual(WARNING_TEXT, cues[0].text)
        self.assertEqual("warning", cues[0].kind)
        self.assertEqual(0, cues[0].order)

        self.assertEqual(30000, cues[1].start_ms)
        self.assertEqual(45000, cues[1].end_ms)
        self.assertEqual(WARNING_TEXT, cues[1].text)
        self.assertEqual("warning", cues[1].kind)
        self.assertEqual(1, cues[1].order)

    def test_warning_cues_empty_windows(self):
        self.assertEqual([], warning_cues([]))

    def test_warning_cues_sorts_by_start_time(self):
        windows = [Window(30000, 45000), Window(10000, 25000)]
        cues = warning_cues(windows)
        self.assertEqual(10000, cues[0].start_ms)
        self.assertEqual(30000, cues[1].start_ms)


class TestMergeCuesWarningsOnly(unittest.TestCase):
    def test_merge_cues_with_none_dialogue(self):
        windows = [Window(5000, 15000)]
        cues = merge_cues(None, windows)
        self.assertEqual(1, len(cues))
        self.assertEqual("warning", cues[0].kind)
        self.assertEqual(5000, cues[0].start_ms)
        self.assertEqual(15000, cues[0].end_ms)

    def test_merge_cues_with_empty_dialogue(self):
        windows = [Window(5000, 15000)]
        cues = merge_cues([], windows)
        self.assertEqual(1, len(cues))
        self.assertEqual("warning", cues[0].kind)

    def test_merge_cues_with_dialogue_interleaves_correctly(self):
        windows = [Window(5000, 15000)]
        dialogue = [Cue(2000, 4000, "Hello", "dialogue", 0), Cue(16000, 18000, "World", "dialogue", 1)]
        cues = merge_cues(dialogue, windows)
        self.assertEqual(3, len(cues))
        self.assertEqual(["dialogue", "warning", "dialogue"], [c.kind for c in cues])


class TestRenderWarningsOnly(unittest.TestCase):
    def test_render_srt_warnings_only(self):
        windows = [Window(1000, 5000), Window(10000, 20000)]
        cues = warning_cues(windows)
        text = render_srt(cues)
        self.assertIn("1\n00:00:01,000 --> 00:00:05,000\n{\\an8}TRIGGER INCOMING", text)
        self.assertIn("2\n00:00:10,000 --> 00:00:20,000\n{\\an8}TRIGGER INCOMING", text)
        # No dialogue text present
        self.assertNotIn("dialogue", text.lower())

    def test_render_ass_warnings_only(self):
        windows = [Window(1000, 5000)]
        cues = warning_cues(windows)
        text = render_ass(cues)
        self.assertIn("[Script Info]", text)
        self.assertIn("Style: Warning", text)
        self.assertIn("Dialogue: 1,0:00:01.00,0:00:05.00,Warning,,0,0,0,,TRIGGER INCOMING", text)
        # Check no dialogue style lines are rendered in events
        for line in text.splitlines():
            if line.startswith("Dialogue:"):
                self.assertIn("Warning", line)

    def test_render_dispatcher_warnings_only(self):
        windows = [Window(2000, 4000)]
        cues = warning_cues(windows)
        srt = render(cues, ".srt")
        self.assertIn("{\\an8}TRIGGER INCOMING", srt)
        ass = render(cues, ".ass")
        self.assertIn("Style: Warning", ass)

    def test_verify_target_ms_with_warnings_only(self):
        windows = [Window(2000, 6000), Window(10000, 14000)]
        cues = warning_cues(windows)
        # Should pick the midpoint of the first warning window (4000 ms)
        target = verify_target_ms(cues, duration_ms=20000)
        self.assertEqual(4000, target)

    def test_verify_target_ms_no_warnings_raises(self):
        dialogue = [Cue(1000, 2000, "Hi", "dialogue", 0)]
        with self.assertRaises(TriggerWarningsError):
            verify_target_ms(dialogue)


if __name__ == "__main__":
    unittest.main()
