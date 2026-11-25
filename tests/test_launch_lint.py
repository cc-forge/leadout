"""Tests for launch.py lint (SPEC 12.5): every banned phrase, marker, titles, lengths."""
import tempfile
import unittest
from pathlib import Path

import helpers
from helpers import run_cli

import launch

URL = "https://example.org/" + "a" * 60


class Kit:
    def __init__(self, files):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        for name, text in files.items():
            (self.dir / name).write_text(text, encoding="utf-8")

    def lint(self):
        return launch.lint_dir(self.dir)

    def close(self):
        self._t.cleanup()


class LintTests(unittest.TestCase):
    def kit(self, files):
        k = Kit(files)
        self.addCleanup(k.close)
        return k

    def test_each_banned_phrase_is_an_error(self):
        phrases = ["Please upvote this", "vote for us on HN", "vote up the post", "pls star it",
                   "Please star the repo", "give it a star", "give us a star", "Star this repo",
                   "star the repo if you like it", "star-for-star anyone", "a star exchange",
                   "support us by starring", "support me by voting", "UPVOTES welcome"]
        for phrase in phrases:
            r = self.kit({"checklist.md": f"intro\n{phrase}\n"}).lint()
            self.assertEqual(len(r["errors"]), 1, phrase)
            self.assertIn("checklist.md:2", r["errors"][0])

    def test_clean_text_passes(self):
        r = self.kit({"checklist.md": "We started the repo in 2025. Voting is not needed.\n"
                                      "The star schema is explained below.\n"}).lint()
        self.assertEqual(r["errors"], [])

    def test_allow_marker(self):
        r = self.kit({"checklist.md": "Never ask anyone to upvote <!-- lint-allow -->\n"}).lint()
        self.assertEqual(r["errors"], [])

    def test_show_hn_titles(self):
        good = "- Show HN: Widget – turns CSV into charts"
        bad_prefix = "- Widget – turns CSV into charts"
        too_long = "- Show HN: Widget – " + "x" * 80
        text = f"# Show HN\n\n## Title options\n\n{good}\n{bad_prefix}\n{too_long}\n\n## URL\n\n- not a title\n"
        r = self.kit({"show-hn.md": text}).lint()
        self.assertEqual(len(r["errors"]), 2)
        self.assertTrue(all("show-hn.md" in e for e in r["errors"]))

    def test_show_hn_superlative_is_a_warning(self):
        r = self.kit({"show-hn.md": "## Title options\n\n- Show HN: Widget – the fastest charts\n"}).lint()
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("superlative" in w for w in r["warnings"]))

    def reddit(self, a, b, ta="Title A", tb="Title B"):
        return (f"## r/one\n\n**Title:** {ta}\n\n**Body:**\n\n{a}\n\n"
                f"## r/two\n\n**Title:** {tb}\n\n**Body:**\n\n{b}\n")

    def test_reddit_identical_titles(self):
        r = self.kit({"reddit.md": self.reddit("alpha " * 20, "completely different " * 10,
                                               "Same", "same")}).lint()
        self.assertEqual(len(r["errors"]), 1)
        self.assertIn("same title", r["errors"][0])

    def test_reddit_similar_bodies(self):
        body = "I built a tool that turns CSV files into charts from the terminal."
        r = self.kit({"reddit.md": self.reddit(body, body + " Feedback welcome.")}).lint()
        self.assertEqual(len(r["errors"]), 1)
        self.assertIn("too similar", r["errors"][0])

    def test_reddit_different_bodies_pass(self):
        a = "I wanted a quick way to chart CSV data without opening a spreadsheet at all."
        b = "Maintainers: here is how the plotting backend avoids copying large buffers twice."
        r = self.kit({"reddit.md": self.reddit(a, b)}).lint()
        self.assertEqual(r["errors"], [])

    def test_todo_only_bodies_are_not_compared(self):
        t = "TODO(post for r/one, a different angle from the other posts)"
        r = self.kit({"reddit.md": self.reddit(t, t.replace("one", "two"))}).lint()
        self.assertEqual(r["errors"], [])

    def test_x_length_counts_urls_as_23(self):
        ok = "a" * 250 + " " + URL           # 311 raw, 274 counted
        too_long = "b" * 270 + " " + URL      # 331 raw, 294 counted
        text = f"### Post 1\n\n{ok}\n\n### Post 2\n\n{too_long}\n"
        r = self.kit({"x-thread.md": text}).lint()
        self.assertEqual(len(r["errors"]), 1)
        self.assertIn("294 characters", r["errors"][0])

    def test_x_hashtags_warning(self):
        r = self.kit({"x-thread.md": "### Post 1\n\nhi #a #b #c\n"}).lint()
        self.assertTrue(any("hashtags" in w for w in r["warnings"]))

    def test_devto_rules(self):
        head = "---\ntitle: The ultimate guide\npublished: true\ntags: [a, b, c, d, e]\n---\nbody\n"
        r = self.kit({"devto.md": head}).lint()
        self.assertEqual(len(r["errors"]), 2)
        self.assertTrue(any("superlative" in w for w in r["warnings"]))
        ok = "---\ntitle: Widget\npublished: false\ntags: [a, b, c, d]\n---\nbody\n"
        self.assertEqual(self.kit({"devto.md": ok}).lint()["errors"], [])

    def test_todo_counts_are_warnings(self):
        r = self.kit({"objections.md": "TODO(a)\nTODO(b)\n"}).lint()
        self.assertEqual(r["errors"], [])
        self.assertIn("2 TODO placeholder(s)", r["warnings"][0])

    def test_cli_exit_codes(self):
        k = self.kit({"checklist.md": "please upvote\n"})
        code, out, _e = run_cli(launch.main, ["lint", "--dir", str(k.dir)])
        self.assertEqual(code, 1)
        self.assertIn("1 error(s)", out)
        k2 = self.kit({"checklist.md": "fine\n"})
        self.assertEqual(run_cli(launch.main, ["lint", "--dir", str(k2.dir)])[0], 0)
        self.assertEqual(run_cli(launch.main, ["lint", "--dir", str(k.dir / "nope")])[0], 2)


if __name__ == "__main__":
    unittest.main()
