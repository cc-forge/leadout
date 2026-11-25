"""Targets, gh error mapping, fixture/record modes, redaction, render, run_main, git helpers."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
import common

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FIXED_NOW = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.timezone.utc)


def write_fixture(folder, key, status=200, body=None, stderr="", headers=None):
    Path(folder, f"{key}.json").write_text(json.dumps(
        {"status": status, "body": body, "stderr": stderr, "headers": headers or {}}),
        encoding="utf-8")


def code_of(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except common.RLError as e:
        return e.code
    return None


class TargetTests(unittest.TestCase):
    def test_remote_forms(self):
        cases = {
            "cli/cli": "cli/cli",
            "https://github.com/cli/cli": "cli/cli",
            "https://github.com/cli/cli.git": "cli/cli",
            "https://github.com/psf/requests/tree/main/docs": "psf/requests",
            "git@github.com:sharkdp/hyperfine.git": "sharkdp/hyperfine",
            "ssh://git@github.com/owner/repo.name": "owner/repo.name",
        }
        for arg, slug in cases.items():
            with self.subTest(arg=arg):
                self.assertEqual(common.resolve_target(arg),
                                 {"kind": "remote", "path": None, "slug": slug})

    def test_invalid(self):
        for arg in ("not a target", "a/b/c", "under_score/repo", "-x/y",
                    "https://gitlab.com/a/b", "owner/.."):
            with self.subTest(arg=arg):
                with self.assertRaises(common.RLError) as cm:
                    common.resolve_target(arg)
                self.assertEqual((cm.exception.code, cm.exception.exit_code), ("E_BAD_TARGET", 2))
                self.assertIn(arg, cm.exception.message)

    def test_local_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = common.resolve_target(tmp)
            self.assertEqual((t["kind"], t["slug"]), ("local", None))
            self.assertEqual(t["path"], str(Path(tmp).resolve()))
            repo = helpers.make_repo(Path(tmp) / "r", {"a.txt": "x"},
                                     origin="git@github.com:acme/widget.git")
            self.assertEqual(common.resolve_target(str(repo))["slug"], "acme/widget")


class GhErrorTests(unittest.TestCase):
    def test_recorded_404s(self):
        with helpers.gh_fixtures("errors"):
            with self.assertRaises(common.RLError) as cm:
                common.gh_api("repos/octocat/this-repo-does-not-exist-rl")
            self.assertEqual(cm.exception.code, "E_REPO_NOT_FOUND")
            self.assertIn("octocat/this-repo-does-not-exist-rl", cm.exception.message)
            ep = "repos/sharkdp/hyperfine/contents/no-such-file.txt"
            self.assertEqual(code_of(common.gh_api, ep), "E_GH_FAILED")
            self.assertIsNone(common.gh_api(ep, allow_404=True))

    def test_recorded_403_traffic(self):
        with helpers.gh_fixtures("hyperfine"):
            with self.assertRaises(common.RLError) as cm:
                common.gh_api("repos/sharkdp/hyperfine/traffic/views")
        self.assertEqual(cm.exception.code, "E_FORBIDDEN")
        self.assertIn("traffic/views", cm.exception.message)

    def test_rate_limits_network_and_auth(self):
        reset = int(FIXED_NOW.timestamp()) + 30 * 60
        with tempfile.TemporaryDirectory() as tmp, helpers.gh_fixtures(tmp), \
                mock.patch.object(common, "utc_now", return_value=FIXED_NOW):
            write_fixture(tmp, "rate_limit", body={"resources": {"core": {"reset": reset}}})
            write_fixture(tmp, "repos_a_limited", 403, {"message": "API rate limit exceeded"},
                          "gh: API rate limit exceeded for user ID 1. (HTTP 403)\n")
            write_fixture(tmp, "repos_a_slow", 403, None, "gh: You have exceeded a secondary "
                                                          "rate limit. (HTTP 403)\n")
            write_fixture(tmp, "repos_a_offline", 0, None,
                          'Get "https://api.github.com/repos/a/offline": dial tcp: lookup '
                          "api.github.com: no such host\n")
            write_fixture(tmp, "repos_a_creds", 401, None, "gh: Bad credentials (HTTP 401)\n")
            with self.assertRaises(common.RLError) as cm:
                common.gh_api("repos/a/limited")
            self.assertEqual(cm.exception.code, "E_RATE_LIMIT")
            self.assertIn("(in 30 min)", cm.exception.message)
            self.assertEqual(code_of(common.gh_api, "repos/a/slow"), "E_SECONDARY_RATE_LIMIT")
            with self.assertRaises(common.RLError) as cm:
                common.gh_api("repos/a/offline")
            self.assertEqual(cm.exception.code, "E_NETWORK")
            self.assertIn("no such host", cm.exception.message)
            self.assertEqual(code_of(common.gh_api, "repos/a/creds"), "E_GH_AUTH")
            self.assertEqual(code_of(common.gh_api, "repos/a/none"), "E_GH_FAILED")

    def test_graphql_errors(self):
        q = "query{x}"
        key = "graphql_" + __import__("hashlib").sha256(
            (q + json.dumps({"name": "r", "owner": "o"}, sort_keys=True)).encode()).hexdigest()[:12]
        with tempfile.TemporaryDirectory() as tmp, helpers.gh_fixtures(tmp):
            write_fixture(tmp, key, body={"data": None, "errors": [{"type": "NOT_FOUND"}]})
            with self.assertRaises(common.RLError) as cm:
                common.gh_graphql(q, {"owner": "o", "name": "r"})
            self.assertEqual((cm.exception.code, "o/r" in cm.exception.message),
                             ("E_REPO_NOT_FOUND", True))
            write_fixture(tmp, key, body={"data": {"ok": 1}})
            self.assertEqual(common.gh_graphql(q, {"owner": "o", "name": "r"}), {"ok": 1})

    def test_fixture_key(self):
        self.assertEqual(common.fixture_key("repos/cli/cli/releases?per_page=10"),
                         "repos_cli_cli_releases_per_page_10")
        self.assertEqual(common.fixture_key("/repos/a/b/stargazers?per_page=100".lstrip("/"),
                                            "application/vnd.github.star+json"),
                         "repos_a_b_stargazers_per_page_100__accept_application_vnd_github"
                         "_star_json")


class GhProcessTests(unittest.TestCase):
    """The real-process path, with subprocess.run replaced by a fake gh."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"LEADOUT_FIXTURES": ""})
        self.env.start()
        common._auth_checked = False

    def tearDown(self):
        self.env.stop()
        common._auth_checked = False

    def fake(self, *results):
        calls = []
        seq = list(results)

        def run(args, *a, **kw):
            calls.append(args)
            rc, out, err = seq.pop(0)
            return subprocess.CompletedProcess(args, rc, out, err)
        return mock.patch("subprocess.run", side_effect=run), calls

    def test_gh_missing(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(code_of(common.gh_api, "repos/a/b"), "E_GH_MISSING")
            self.assertFalse(common.gh_available())

    def test_not_authenticated(self):
        patch, _ = self.fake((1, "", "You are not logged into any GitHub hosts."))
        with patch:
            self.assertEqual(code_of(common.gh_api, "repos/a/b"), "E_GH_AUTH")

    def test_success_errors_headers_and_record(self):
        link = '<https://api.github.com/repositories/1/stargazers?page=3>; rel="last"'
        patch, calls = self.fake(
            (0, "", ""),
            (0, json.dumps({"full_name": "a/b", "owner": {"login": "a", "id": 7},
                            "commit": {"author": {"name": "A", "email": "a@x.invalid"}}}), ""),
            (1, '{"message": "Not Found"}', "gh: Not Found (HTTP 404)\n"),
            (0, f"HTTP/2.0 200 OK\r\nLink: {link}\r\nX-Other: 1\r\n\r\n[1, 2]", ""),
        )
        with tempfile.TemporaryDirectory() as rec, patch, \
                mock.patch.dict(os.environ, {"LEADOUT_RECORD": rec}):
            body = common.gh_api("repos/a/b")
            self.assertEqual(body["full_name"], "a/b")
            saved = json.loads(Path(rec, "repos_a_b.json").read_text())
            self.assertEqual(saved["body"]["owner"], {"login": "a"})
            self.assertNotIn("email", saved["body"]["commit"]["author"])
            self.assertEqual(code_of(common.gh_api, "repos/a/b"), "E_REPO_NOT_FOUND")
            res = common.gh_api("repos/a/b/stargazers", accept="x/y", include_headers=True)
            self.assertEqual((res["status"], res["body"], res["headers"]["Link"]),
                             (200, [1, 2], link))
            hdrs = json.loads(Path(rec, "repos_a_b_stargazers__accept_x_y.json").read_text())
            self.assertEqual(hdrs["headers"], {"Link": link})
        self.assertEqual(calls[0][:3], ["gh", "auth", "status"])
        self.assertEqual(calls[3][-3:], ["-H", "Accept: x/y", "-i"])

    def test_paginated_concatenation(self):
        self.assertEqual(common._parse_body("[1,2]\n[3]", paginate=True), [1, 2, 3])


class OutputTests(unittest.TestCase):
    def test_redact(self):
        pat = "github_pat_" + "x" * 30
        self.assertEqual(common.redact(f"a {TOKEN} b {pat}"), "a [REDACTED] b [REDACTED]")
        self.assertEqual(common.redact("gho_short"), "gho_short")

    def test_render_todos(self):
        text, todos = common.render("# [[name]]\n[[tagline]] [[name]] [[empty]] ${{ x }}",
                                    {"name": "tool", "empty": ""})
        self.assertEqual(text, "# tool\nTODO(tagline) tool TODO(empty) ${{ x }}")
        self.assertEqual(todos, ["tagline", "empty"])

    def test_run_main_paths(self):
        def boom(argv):
            raise ValueError(f"bad {TOKEN}")

        code, out, err = helpers.run_cli(lambda a: common.run_main(boom, a), [])
        self.assertEqual(code, 5)
        self.assertIn("internal error: ValueError: bad [REDACTED]", err)
        self.assertNotIn("Traceback", err)
        with mock.patch.dict(os.environ, {"LEADOUT_DEBUG": "1"}):
            _, _, err = helpers.run_cli(lambda a: common.run_main(boom, a), [])
        self.assertIn("Traceback", err)

        def fail(argv):
            raise common.RLError("E_NOT_GIT", path="/x")

        code, out, err = helpers.run_cli(lambda a: common.run_main(fail, a), ["--json"])
        self.assertEqual(code, 4)
        self.assertIn("leadout: error: /x is not a git repository.", err)
        self.assertEqual(json.loads(out)["error"]["code"], "E_NOT_GIT")

        def interrupt(argv):
            raise KeyboardInterrupt

        code, _, err = helpers.run_cli(lambda a: common.run_main(interrupt, a), [])
        self.assertEqual((code, err.strip()), (130, "leadout: interrupted"))

    def test_warning_messages(self):
        self.assertEqual(common.warning("W_NO_PUSH", slug="a/b")["message"],
                         "Traffic data needs push access to a/b. Showing public data only.")
        self.assertEqual(common.warning("E_HISTORY_CORRUPT", path="h", n=3)["message"],
                         "h line 3 is not valid JSON; skipped.")


class FileAndTimeTests(unittest.TestCase):
    def test_write_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, "d", "f.txt")
            common.write_new_file(p, "x")
            self.assertEqual(p.read_bytes(), b"x\n")
            self.assertEqual(code_of(common.write_new_file, p, "y"), "E_FILE_EXISTS")
            common.write_new_file(p, "y", overwrite=True)
            self.assertEqual(p.read_text(), "y\n")

    def test_time(self):
        self.assertEqual(common.fmt_ts(common.parse_ts("2026-11-25T10:30:00+02:00")),
                         "2026-11-25T08:30:00Z")
        self.assertEqual(common.days_between("2026-11-20T23:59:00Z", "2026-11-25"), 5)
        self.assertEqual(code_of(common.check_date, "25/11/2026", "--as-of"), "E_BAD_ARGS")

    def test_git_branch_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = helpers.make_repo(Path(tmp) / "r", {"a.txt": "x"})
            self.assertTrue(common.is_default_branch(repo))
            self.assertTrue(common.working_tree_clean(repo))
            helpers.git(repo, "switch", "--quiet", "-c", "feature")
            self.assertFalse(common.is_default_branch(repo))
            (repo / "b.txt").write_text("y")
            self.assertFalse(common.working_tree_clean(repo))
            helpers.git(repo, "checkout", "--quiet", "--detach")
            self.assertTrue(common.is_default_branch(repo))
            self.assertEqual(code_of(common.working_tree_clean, tmp), "E_NOT_GIT")


if __name__ == "__main__":
    unittest.main()
