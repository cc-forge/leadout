"""stats.py: collection, history file, merge rules, report sections, CLI, install-workflow."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
import common
import stats

NOW = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.timezone.utc)
HIST = helpers.FIXTURES / "stats"
LOCAL, ACTIONS, HIDDEN = str(HIST / "local.jsonl"), str(HIST / "actions.jsonl"), str(
    HIST / "hidden.jsonl")
DEMO = "example-org/demo"


def _star(ts):
    return {"starred_at": ts, "user": {"login": "someone"}}


# Responses of a repository the viewer can push to (traffic visible). Synthetic: the
# recorded fixtures come from a public repository without push access (tests/fixtures/gh).
DEMO_GH = {
    "repos/example-org/demo": {"body": {"full_name": DEMO, "stargazers_count": 8,
                                        "forks_count": 2, "subscribers_count": 3,
                                        "open_issues_count": 1}},
    ("repos/example-org/demo/stargazers?per_page=100", stats.STAR_ACCEPT): {
        "headers": {"Link": '<https://api.github.com/repositories/1/stargazers?per_page=100'
                            '&page=2>; rel="next", <https://api.github.com/repositories/1/'
                            'stargazers?per_page=100&page=3>; rel="last"'},
        "body": [_star("2026-10-01T10:00:00Z"), _star("2026-10-02T10:00:00Z"),
                 _star("2026-10-03T10:00:00Z")]},
    ("repos/example-org/demo/stargazers?per_page=100&page=2", stats.STAR_ACCEPT): {
        "body": [_star("2026-11-01T10:00:00Z"), _star("2026-11-02T10:00:00Z"),
                 _star("2026-11-03T10:00:00Z")]},
    ("repos/example-org/demo/stargazers?per_page=100&page=3", stats.STAR_ACCEPT): {
        "body": [_star("2026-11-24T10:00:00Z"), _star("2026-11-24T11:00:00Z")]},
    "repos/example-org/demo/traffic/views": {"body": {"count": 30, "uniques": 12, "views": [
        {"timestamp": "2026-11-23T00:00:00Z", "count": 10, "uniques": 4},
        {"timestamp": "2026-11-24T00:00:00Z", "count": 20, "uniques": 9}]}},
    "repos/example-org/demo/traffic/clones": {"body": {"count": 3, "uniques": 2, "clones": [
        {"timestamp": "2026-11-24T00:00:00Z", "count": 3, "uniques": 2}]}},
    "repos/example-org/demo/traffic/popular/referrers": {"body": [
        {"referrer": "news.ycombinator.com", "count": 18, "uniques": 7}]},
    "repos/example-org/demo/traffic/popular/paths": {"body": [
        {"path": "/example-org/demo", "title": "demo", "count": 25, "uniques": 10}]},
    "repos/example-org/demo/releases?per_page=100&page=1": {"body": [
        {"tag_name": "v1.0.0", "published_at": "2026-11-01T10:00:00Z", "draft": False,
         "body": "notes", "assets": [{"name": "demo.tar.gz", "download_count": 7}]},
        {"tag_name": "v1.1.0", "published_at": None, "draft": True, "assets": []}]},
}


def demo_fixtures(folder, changes=None):
    for key, rec in {**DEMO_GH, **(changes or {})}.items():
        ep, accept = key if isinstance(key, tuple) else (key, None)
        full = {"status": 200, "stderr": "", "headers": {}, **rec}
        Path(folder, common.fixture_key(ep, accept) + ".json").write_text(json.dumps(full))


def load(*paths, slug=DEMO):
    with mock.patch("sys.stderr"):
        return stats.load_history(list(paths), slug)


class CollectTests(unittest.TestCase):
    def test_owned_repo_with_traffic_and_star_pages(self):
        with tempfile.TemporaryDirectory() as tmp, helpers.gh_fixtures(tmp):
            demo_fixtures(tmp)
            snap, warnings = stats.collect(DEMO, now=NOW)
            partial, _ = stats.collect(DEMO, max_star_pages=1, now=NOW)
        self.assertEqual(warnings, [])
        self.assertEqual((snap["collected_at"], snap["source"]), ("2026-11-25T12:00:00Z", "local"))
        self.assertEqual(snap["public"], {"stars": 8, "forks": 2, "watchers": 3, "open_issues": 1})
        self.assertTrue(snap["stargazers"]["complete"])
        self.assertEqual(len(snap["stargazers"]["events"]), 8)
        self.assertEqual(snap["stargazers"]["events"][0], "2026-10-01T10:00:00Z")
        self.assertNotIn("someone", json.dumps(snap))  # no usernames stored
        self.assertFalse(partial["stargazers"]["complete"])
        self.assertEqual(partial["stargazers"]["events"],
                         ["2026-11-24T10:00:00Z", "2026-11-24T11:00:00Z"])
        t = snap["traffic"]
        self.assertEqual(t["views"]["days"], [{"date": "2026-11-23", "count": 10, "uniques": 4},
                                              {"date": "2026-11-24", "count": 20, "uniques": 9}])
        self.assertEqual(t["clones"]["count"], 3)
        self.assertEqual(t["referrers"][0]["referrer"], "news.ycombinator.com")
        self.assertEqual(t["paths"][0]["title"], "demo")
        self.assertEqual(snap["releases"], [{"tag": "v1.0.0", "published_at":
                                             "2026-11-01T10:00:00Z", "assets": [
                                                 {"name": "demo.tar.gz", "download_count": 7}]}])

    def test_zero_stars_needs_no_stargazer_call(self):
        with tempfile.TemporaryDirectory() as tmp, helpers.gh_fixtures(tmp):
            demo_fixtures(tmp, {"repos/example-org/demo": {"body": {"stargazers_count": 0}}})
            for f in Path(tmp).glob("*stargazers*"):
                f.unlink()
            snap, _ = stats.collect(DEMO, now=NOW)
        self.assertEqual(snap["stargazers"], {"available": True, "error": None,
                                              "complete": True, "events": []})

    def test_recorded_public_repo_without_push_access(self):
        with helpers.gh_fixtures("hyperfine"), mock.patch("sys.stderr"):
            snap, warnings = stats.collect("sharkdp/hyperfine", now=NOW)
        self.assertEqual([w["code"] for w in warnings], ["W_STARS_HIDDEN", "W_NO_PUSH"])
        self.assertEqual(snap["traffic"], {"available": False, "error": "no_push_access",
                                           "views": None, "clones": None, "referrers": None,
                                           "paths": None})
        self.assertEqual(snap["stargazers"], {"available": False, "error": "hidden",
                                              "complete": False, "events": []})
        self.assertGreater(snap["public"]["stars"], 0)
        self.assertTrue(snap["releases"])
        self.assertTrue(all("download_count" in a for r in snap["releases"]
                            for a in r["assets"]))


class HistoryTests(unittest.TestCase):
    def test_append_creates_self_ignoring_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, ".leadout", "stats.jsonl")
            stats.append_history(path, {"collected_at": "a", "repo": DEMO})
            stats.append_history(path, {"collected_at": "b", "repo": DEMO})
            self.assertEqual(Path(tmp, ".leadout", ".gitignore").read_text(), "*\n")
            lines = path.read_text().splitlines()
            self.assertEqual([json.loads(x)["collected_at"] for x in lines], ["a", "b"])
            other = Path(tmp, "data", "s.jsonl")
            stats.append_history(other, {"collected_at": "c"})
            self.assertFalse(Path(tmp, "data", ".gitignore").exists())

    def test_load_merges_dedupes_filters_and_warns(self):
        snaps, warnings = load(LOCAL, ACTIONS)
        self.assertEqual([s["collected_at"] for s in snaps], [
            "2026-11-10T06:00:00Z", "2026-11-17T06:00:00Z", "2026-11-24T06:00:00Z",
            "2026-11-25T06:17:00Z"])
        self.assertEqual(snaps[1]["source"], "local")  # first reading of a timestamp wins
        self.assertEqual(warnings, [{"code": "E_HISTORY_CORRUPT",
                                     "message": f"{LOCAL} line 3 is not valid JSON; skipped."}])

    def test_missing_history(self):
        with self.assertRaises(common.RLError) as cm:
            load("/nonexistent/stats.jsonl")
        self.assertEqual(cm.exception.code, "E_BAD_ARGS")


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snaps, warnings = load(LOCAL, ACTIONS)
        cls.r = stats.report(snaps, DEMO, NOW, "2026-11-20", 3, 30, warnings)
        cls.day = {d["date"]: d for d in cls.r["daily"]}

    def test_header_and_totals(self):
        h, t = self.r["header"], self.r["totals"]
        self.assertEqual((h["snapshots"], h["stars_source"], h["stars_since"]),
                         (4, "events", "2026-11-08"))
        self.assertEqual((h["traffic_available"], h["traffic_as_of"]), (True, "2026-11-24"))
        self.assertEqual((t["stars"], t["stars_change"], t["forks_change"]), (21, 1, 0))
        self.assertEqual((t["views_14d"], t["uniques_14d"], t["clones_14d"]), (150, 80, 12))
        self.assertEqual(t["downloads_total"], 16)

    def test_overlapping_windows_latest_wins(self):
        self.assertEqual(self.day["2026-11-10"]["views"], 9)   # B (9) replaces A (1)
        self.assertEqual(self.day["2026-11-17"]["views"], 10)  # C (10) replaces B (2)
        self.assertEqual(self.day["2026-11-08"]["uniques"], 3)
        self.assertIsNone(self.day["2026-11-11"]["views"])

    def test_star_events_dedupe_and_coverage(self):
        stars = {d: v["stars"] for d, v in self.day.items()}
        self.assertEqual(stars["2026-11-09"], 2)   # 12:00 appears in A and B, counted once
        self.assertEqual(stars["2026-11-16"], 2)   # 10:00 appears in B and C, counted once
        self.assertEqual(stars["2026-11-20"], 3)
        self.assertEqual(stars["2026-11-11"], 0)
        self.assertIsNone(stars["2026-11-25"])     # after the last visible reading
        self.assertEqual(self.r["daily"][0]["date"], "2026-11-08")
        self.assertTrue(self.day["2026-11-25"]["partial"])
        self.assertEqual(sum(v or 0 for v in stars.values()), 12)

    def test_stars_from_snapshots_when_hidden(self):
        snaps, _ = load(HIDDEN, slug="acme/hidden")
        r = stats.report(snaps, "acme/hidden", NOW)
        day = {d["date"]: d["stars"] for d in r["daily"]}
        self.assertEqual(r["header"]["stars_source"], "snapshots")
        self.assertEqual((day["2026-11-20"], day["2026-11-21"], day["2026-11-22"],
                          day["2026-11-23"]), (None, 5, None, 5))
        self.assertIsNone(r["referrers"])
        md = stats.render_markdown(r)
        self.assertIn("from snapshots", md)
        self.assertIn("Not available (traffic needs push access).", md)

    def test_referrers_and_paths(self):
        ref = self.r["referrers"]
        self.assertEqual((ref["as_of"], ref["compared_with"], ref["period_stars"]),
                         ("2026-11-24", "2026-11-17", 9))
        rows = [(x["referrer"], x["share"], x["change"], x["new"]) for x in ref["rows"]]
        self.assertEqual(rows, [("news.ycombinator.com", 80.0, 80, False),
                                ("github.com", 20.0, 5, False), ("reddit.com", 10.0, 15, True)])
        self.assertEqual(len(self.r["paths"]), 10)
        self.assertEqual(self.r["paths"][-1]["path"], "/example-org/demo/stargazers")

    def test_before_after_launch(self):
        m = {x["metric"]: x for x in self.r["launch"]["metrics"]}
        self.assertEqual((m["stars/day"]["before"], m["stars/day"]["after"],
                          m["stars/day"]["change"], m["stars/day"]["ratio"]),
                         (0.33, 1.33, 1.0, 4.0))
        self.assertIsNone(m["views/day"]["before"])  # 1 of 3 days known: insufficient
        self.assertEqual(m["views/day"]["after"], 33.33)
        self.assertEqual(m["unique visitors/day"]["after"], 20.0)
        self.assertEqual((m["clones/day"]["before"], m["clones/day"]["after"]), (None, 4.0))

    def test_releases(self):
        self.assertEqual(self.r["releases"], [
            {"tag": "v1.1.0", "asset": "tool.tar.gz", "downloads": 3, "change": 0},
            {"tag": "v1.0.0", "asset": "tool.tar.gz", "downloads": 13, "change": 1}])

    def test_markdown(self):
        md = stats.render_markdown(self.r)
        self.assertTrue(md.startswith("# Repository stats: example-org/demo\n"))
        self.assertIn("| 2026-11-25 (partial) | – | – | – | – | – |", md)
        self.assertIn("| Views/day | insufficient data | 33.33 | – |".lower(), md.lower())
        self.assertIn("| stars/day | 0.33 | 1.33 | +1 (×4) |", md)
        self.assertIn("| reddit.com | 15 | 10 | 10.0% | new |", md)
        self.assertIn("context, not attribution", md)

    def test_empty_history(self):
        with self.assertRaises(common.RLError):
            stats.report([], DEMO, NOW)


class CliTests(unittest.TestCase):
    def test_report_json(self):
        with mock.patch.object(common, "utc_now", return_value=NOW):
            code, out, err = helpers.run_cli(stats.main, [
                "report", DEMO, "--history", LOCAL, "--history", ACTIONS,
                "--launch-date", "2026-11-20", "--window", "3", "--json"])
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertEqual(r["header"]["snapshots"], 4)
        self.assertEqual(r["warnings"][0]["code"], "E_HISTORY_CORRUPT")
        self.assertIn("line 3 is not valid JSON", err)

    def test_collect_then_default_run(self):
        ticks = iter(NOW + dt.timedelta(minutes=n) for n in range(10))
        with tempfile.TemporaryDirectory() as tmp, helpers.gh_fixtures(tmp), \
                mock.patch.object(common, "utc_now", side_effect=lambda: next(ticks)):
            demo_fixtures(tmp)
            hist = os.path.join(tmp, ".leadout", "stats.jsonl")
            code, out, err = helpers.run_cli(stats.main, ["collect", DEMO, "--history", hist])
            self.assertEqual(code, 0, err)
            self.assertIn("8 stars", out)
            self.assertIn("Snapshot appended", err)
            code, out, err = helpers.run_cli(stats.main, [DEMO, "--history", hist, "--json"])
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["header"]["snapshots"], 2)
            code, out, _ = helpers.run_cli(stats.main, ["collect", DEMO, "--history", hist,
                                                        "--no-save", "--json"])
            self.assertEqual(json.loads(out)["public"]["stars"], 8)
            self.assertEqual(len(Path(hist).read_text().splitlines()), 2)

    def test_bad_targets_and_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = helpers.run_cli(stats.main, ["collect", tmp])
        self.assertEqual(code, 2)
        self.assertIn("has no GitHub remote", err)
        code, _, err = helpers.run_cli(stats.main, ["report", DEMO, "--history", LOCAL,
                                                    "--launch-date", "20/11"])
        self.assertEqual(code, 2)
        code, _, _ = helpers.run_cli(stats.main, ["report", DEMO, "--window", "0"])
        self.assertEqual(code, 2)


class InstallWorkflowTests(unittest.TestCase):
    FILES = [".github/workflows/leadout-stats.yml", ".github/leadout/stats.py",
             ".github/leadout/common.py"]

    def test_template(self):
        text = stats.WORKFLOW_FILES[0][1].read_text(encoding="utf-8")
        for needle in ('cron: "17 6 * * *"', "workflow_dispatch", "contents: write",
                       "secrets.LEADOUT_TOKEN || github.token", "--source actions",
                       "leadout-stats"):
            self.assertIn(needle, text)

    def test_print_only_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = helpers.run_cli(stats.main, ["install-workflow", tmp])
            self.assertEqual(code, 0)
            for f in self.FILES:
                self.assertIn(f, out)
                self.assertFalse(Path(tmp, f).exists())
            self.assertIn("==> .github/workflows/leadout-stats.yml <==", out)

    def test_write_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = helpers.run_cli(stats.main, ["install-workflow", tmp, "--write"])
            self.assertEqual(code, 4)
            self.assertIn("not a git repository", err)
            repo = helpers.make_repo(Path(tmp) / "r", {"README.md": "x"})
            code, _, err = helpers.run_cli(stats.main, ["install-workflow", str(repo), "--write"])
            self.assertEqual(code, 4)
            self.assertIn("default branch (main)", err)
            helpers.git(repo, "switch", "--quiet", "-c", "leadout/stats-workflow")
            code, out, err = helpers.run_cli(stats.main, ["install-workflow", str(repo),
                                                          "--write"])
            self.assertEqual(code, 0, err)
            for (rel, src), f in zip(stats.WORKFLOW_FILES, self.FILES):
                self.assertEqual(rel, f)
                self.assertEqual((repo / f).read_text(encoding="utf-8"),
                                 src.read_text(encoding="utf-8"))
            self.assertEqual(helpers.git(repo, "diff", "--cached", "--name-only"), "")
            code, _, err = helpers.run_cli(stats.main, ["install-workflow", str(repo),
                                                        "--write"])
            self.assertEqual(code, 4)
            self.assertIn("already exists", err)
            code, _, _ = helpers.run_cli(stats.main, ["install-workflow", str(repo), "--write",
                                                      "--overwrite"])
            self.assertEqual(code, 0)
        code, _, _ = helpers.run_cli(stats.main, ["install-workflow", DEMO])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
