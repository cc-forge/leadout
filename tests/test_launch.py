"""Tests for launch.py: init, calendar, subreddits, awesome search."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from helpers import gh_fixtures, make_repo, run_cli

import common
import launch

CARGO = '[package]\nname = "demo"\nversion = "0.1.0"\n\n[[bin]]\nname = "demo"\n'


class CalendarTests(unittest.TestCase):
    def test_dates_and_time_conversion(self):
        cal = launch.calendar_rows("2026-12-02", "Europe/Rome")
        rows = {r["what"].split(";")[0].split(" (")[0]: r["when"] for r in cal["rows"]}
        self.assertEqual(cal["rows"][0]["when"], "2026-11-18 (Wed)")
        self.assertEqual(cal["rows"][3]["when"], "2026-12-02 14:00 CET (Wed)")
        self.assertEqual(cal["rows"][4]["when"], "2026-12-02 16:00 CET (Wed)")
        self.assertEqual(cal["rows"][-1]["when"], "2026-12-16 (Wed)")
        self.assertEqual(len(cal["notes"]), 1)
        self.assertIn("not measured data", cal["notes"][0])
        self.assertTrue(rows)

    def test_time_zone_can_change_the_date(self):
        cal = launch.calendar_rows("2026-12-02", "Asia/Tokyo")
        self.assertEqual(cal["rows"][3]["when"], "2026-12-02 22:00 JST (Wed)")
        cal = launch.calendar_rows("2026-12-02", "Pacific/Auckland")
        self.assertTrue(cal["rows"][3]["when"].startswith("2026-12-03 02:00"))

    def test_weekend_launch_keeps_date_and_notes_preference(self):
        cal = launch.calendar_rows("2026-12-05", "UTC")  # a Saturday
        self.assertEqual(cal["rows"][3]["when"], "2026-12-05 13:00 UTC (Sat)")
        self.assertIn("Saturday", cal["notes"][1])

    def test_relative_without_date(self):
        cal = launch.calendar_rows(None)
        self.assertEqual(cal["rows"][0]["when"], "D-14")
        self.assertEqual(cal["rows"][3]["when"], "D 08:00 America/New_York")

    def test_cli_errors(self):
        self.assertEqual(run_cli(launch.main, ["calendar", "--launch-date", "2026-13-01"])[0], 2)
        code, _o, err = run_cli(launch.main, ["calendar", "--launch-date", "2026-12-02",
                                              "--timezone", "Mars/Base"])
        self.assertEqual(code, 2)
        self.assertIn("unknown timezone", err)
        code, out, _e = run_cli(launch.main, ["calendar", "--launch-date", "2026-12-02",
                                              "--timezone", "UTC", "--json"])
        self.assertEqual(json.loads(out)["rows"][3]["when"], "2026-12-02 13:00 UTC (Wed)")


class SubredditTests(unittest.TestCase):
    ENTRY = {"name": "x", "url": "u", "rules_url": "r", "fits_types": ["cli"],
             "fits_languages": [], "self_promotion": "Summary.", "flair": None,
             "verified_at": "2026-11-01"}

    def patched(self, entries):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        (Path(t.name) / "subreddits.json").write_text(json.dumps({"schema": 1,
                                                                  "subreddits": entries}))
        return mock.patch.object(launch, "LAUNCH_TEMPLATES", Path(t.name))

    def test_shipped_list_has_only_verified_entries(self):
        data = common.read_json(launch.LAUNCH_TEMPLATES / "subreddits.json")
        for s in data["subreddits"]:
            self.assertTrue(s["verified_at"] and s["rules_url"] and s["self_promotion"])

    def test_filter_by_type_and_language(self):
        rust = dict(self.ENTRY, name="rust", fits_types=[], fits_languages=["Rust"])
        mac = dict(self.ENTRY, name="mac", fits_types=["desktop-app"])
        with self.patched([self.ENTRY, rust, mac]):
            names = lambda **kw: [s["name"] for s in launch.subreddits(**kw)["subreddits"]]
            self.assertEqual(names(ptype="cli"), ["x", "rust"])
            self.assertEqual(names(ptype="cli", language="Python"), ["x"])
            self.assertEqual(names(ptype="desktop-app", language="rust"), ["rust", "mac"])
            self.assertEqual(len(names()), 3)

    def test_stale_entries_flagged(self):
        with self.patched([self.ENTRY]):
            r = launch.subreddits(today=dt.date(2026, 11, 25))
            self.assertEqual(r["stale"], [])
            r = launch.subreddits(today=dt.date(2027, 6, 1))
            self.assertEqual(r["stale"], ["x"])

    def test_empty_list_message(self):
        with self.patched([]):
            code, out, _e = run_cli(launch.main, ["subreddits"])
        self.assertEqual(code, 0)
        self.assertIn("No verified subreddit entries", out)


def fixture(folder, endpoint, body, status=200):
    path = Path(folder) / f"{common.fixture_key(endpoint)}.json"
    path.write_text(json.dumps({"status": status, "body": body, "stderr": "", "headers": {}}))


class AwesomeTests(unittest.TestCase):
    def repo(self, name, stars, pushed, archived=False):
        return {"full_name": name, "html_url": f"https://github.com/{name}",
                "stargazers_count": stars, "pushed_at": pushed, "archived": archived}

    def test_search_filters_sorts_and_finds_contributing(self):
        items = [self.repo("a/awesome-rust", 500, "2026-11-01T00:00:00Z"),
                 self.repo("b/awesome-old", 900, "2025-01-01T00:00:00Z"),
                 self.repo("c/awesome-archived", 800, "2026-11-01T00:00:00Z", archived=True),
                 self.repo("d/awesome-small", 50, "2026-10-01T00:00:00Z"),
                 self.repo("e/awesome-tie", 50, "2026-10-01T00:00:00Z")]
        with tempfile.TemporaryDirectory() as t:
            for q in launch.awesome_queries(["rust"]):
                fixture(t, "search/repositories?q=" + launch.urllib.parse.quote(q, safe="")
                        + "&sort=stars&order=desc&per_page=30", {"items": items})
            fixture(t, "repos/a/awesome-rust/contents",
                    [{"name": "README.md", "html_url": "x"},
                     {"name": "contributing.md", "html_url": "https://github.com/a/c"}])
            fixture(t, "repos/d/awesome-small/contents", {"message": "Not Found"}, status=404)
            fixture(t, "repos/e/awesome-tie/contents", [])
            with gh_fixtures(t):
                rows = launch.search_awesome(["rust"], limit=3, today=dt.date(2026, 11, 25))
        self.assertEqual([r["full_name"] for r in rows],
                         ["a/awesome-rust", "d/awesome-small", "e/awesome-tie"])
        self.assertEqual(rows[0]["contributing_url"], "https://github.com/a/c")
        self.assertIsNone(rows[1]["contributing_url"])
        self.assertIn("| [a/awesome-rust]", launch.awesome_table(rows))

    def test_queries_capped_at_five_and_deduped(self):
        qs = launch.awesome_queries(["rust", "cli", "csv", "rust"])
        self.assertEqual(len(qs), 5)
        self.assertEqual(qs[0], "awesome rust in:name topic:awesome")
        self.assertEqual(qs[1], "awesome-rust in:name")
        self.assertEqual(len(set(qs)), 5)

    def test_empty_table_message(self):
        self.assertIn("No active awesome lists", launch.awesome_table([]))


class InitTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._t.name) / "r",
                              {"Cargo.toml": CARGO, "src/main.rs": "fn main() {}\n"})

    def tearDown(self):
        self._t.cleanup()

    def init(self, *extra):
        return run_cli(launch.main, ["init", str(self.root), "--offline", *extra])

    def test_init_creates_the_kit_and_lints_without_errors(self):
        code, out, _e = self.init("--launch-date", "2026-12-02", "--timezone", "Europe/Rome")
        self.assertEqual(code, 0)
        self.assertIn("Nothing was published", out)
        kit = self.root / "launch"
        self.assertEqual(sorted(p.name for p in kit.iterdir()), sorted(launch.KIT_FILES))
        self.assertIn("2026-12-02 14:00 CET (Wed)", (kit / "calendar.md").read_text())
        self.assertIn("## r/TODO(subreddit)", (kit / "reddit.md").read_text())
        hn = (kit / "show-hn.md").read_text()
        self.assertIn("Show HN: demo – TODO(what_it_does_1)", hn)
        self.assertIn("published: false", (kit / "devto.md").read_text())
        code, out, _e = run_cli(launch.main, ["lint", "--dir", str(kit)])
        self.assertEqual(code, 0, out)
        self.assertIn("0 error(s)", out)

    def test_existing_files_are_kept_unless_overwrite(self):
        self.init()
        (self.root / "launch/show-hn.md").write_text("mine\n")
        code, out, _e = self.init()
        self.assertEqual(code, 0)
        self.assertIn("kept", out)
        self.assertEqual((self.root / "launch/show-hn.md").read_text(), "mine\n")
        self.init("--overwrite")
        self.assertIn("Show HN", (self.root / "launch/show-hn.md").read_text())

    def test_relative_calendar_without_date(self):
        self.init()
        self.assertIn("| D-14 |", (self.root / "launch/calendar.md").read_text())

    def test_init_needs_a_local_checkout_and_a_valid_date(self):
        self.assertEqual(run_cli(launch.main, ["init", "acme/widget"])[0], 4)
        self.assertEqual(self.init("--launch-date", "soon")[0], 2)

    def test_reddit_sections_use_verified_entries(self):
        sub = {"name": "x", "url": "u", "rules_url": "https://r/rules", "fits_types": [],
               "fits_languages": [], "self_promotion": "Allowed on Fridays.", "flair": "Project",
               "verified_at": "2026-11-01"}
        text = launch._reddit_sections([sub, dict(sub, name="y")])
        self.assertIn("## r/x", text)
        self.assertIn("Allowed on Fridays. Flair: Project.", text)
        self.assertIn("TODO(title for r/y)", text)


if __name__ == "__main__":
    unittest.main()
