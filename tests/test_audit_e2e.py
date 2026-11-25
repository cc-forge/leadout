"""End to end: recorded gh fixtures -> audit.py --json, and offline local audits."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import helpers
import audit

AS_OF = ["--as-of", "2026-11-25"]
RUBRIC = ["--rubric", helpers.BUNDLED_RUBRIC]
EXPECTED = helpers.FIXTURES / "expected"

SAMPLE_CLI = {
    "README.md": (
        "# sample-cli\n\n"
        "[![npm](https://img.shields.io/npm/v/sample-cli)](https://npmjs.com/package/sample-cli)"
        "\n\nSample CLI renames image files by the date they were taken.\n\n"
        "## Install\n\n```sh\nnpm i -g sample-cli\n```\n\n"
        "## Usage\n\n```sh\nsample-cli ./photos\n```\n"),
    "package.json": json.dumps({"name": "sample-cli", "version": "0.3.0",
                                "bin": {"sample-cli": "src/index.js"},
                                "dependencies": {"exifr": "^7.0.0"}}),
    "LICENSE": "MIT License\n",
    "CHANGELOG.md": "# Changelog\n",
    ".github/workflows/ci.yml": "name: ci\n",
    "src/index.js": "console.log('hi')\n",
}
OFFLINE_UNKNOWN = {"meta.description", "meta.topics", "meta.homepage", "meta.social_preview",
                   "comm.good_first_issue", "health.issue_response"}


def write_tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


class RecordedRepoTests(unittest.TestCase):
    def run_hyperfine(self, *extra):
        with helpers.gh_fixtures("hyperfine"):
            return helpers.run_cli(audit.main, ["sharkdp/hyperfine", *AS_OF, *RUBRIC, *extra])

    def test_json_matches_expected(self):
        code, out, err = self.run_hyperfine("--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, (EXPECTED / "hyperfine.json").read_text(encoding="utf-8"))

    def test_other_recorded_repos_match_expected(self):
        # S6 real-world check: a library and a desktop app next to the hyperfine CLI
        for name, slug in (("requests", "psf/requests"), ("localsend", "localsend/localsend")):
            with self.subTest(name), helpers.gh_fixtures(name):
                code, out, _ = helpers.run_cli(audit.main, [slug, *AS_OF, *RUBRIC, "--json"])
                self.assertEqual(code, 0)
                self.assertEqual(out, (EXPECTED / f"{name}.json").read_text(encoding="utf-8"))

    def test_fixture_sanitizer_drops_emails(self):
        import common
        clean = common._sanitize({"message": "Co-authored-by: A <a@example.com>",
                                  "verification": {"payload": "author A <a@example.com>"},
                                  "author": {"login": "a", "email": "a@example.com"}})
        self.assertEqual(clean, {"message": "Co-authored-by: A <email>", "author": {"login": "a"}})
        self.assertEqual(common._sanitize("me@host.example.re ~/dev git@github.com:a/b Icon@2x.png"),
                         "<email> ~/dev git@github.com:a/b Icon@2x.png")

    def test_repeated_runs_are_identical(self):
        self.assertEqual(self.run_hyperfine("--json")[1], self.run_hyperfine("--json")[1])

    def test_markdown_report(self):
        code, out, _ = self.run_hyperfine()
        self.assertEqual(code, 0)
        expected = json.loads((EXPECTED / "hyperfine.json").read_text(encoding="utf-8"))
        self.assertTrue(out.startswith(
            f"# Launch readiness: sharkdp/hyperfine: {expected['score']}/100 "
            f"({expected['grade']})\n"))
        for heading in ("## Fixes, highest impact per effort first", "## All checks"):
            self.assertIn(heading, out)

    def test_saved_facts_rescore_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            facts_file = os.path.join(tmp, "facts.json")
            code, _, err = self.run_hyperfine("--json", "--save-facts", facts_file)
            self.assertEqual(code, 0, err)
            code, out, err = helpers.run_cli(audit.main, ["--facts", facts_file, "--json",
                                                          *RUBRIC])
            self.assertEqual(code, 0, err)
            self.assertEqual(out, (EXPECTED / "hyperfine.json").read_text(encoding="utf-8"))
            code, _, err = self.run_hyperfine("--save-facts", facts_file)
            self.assertEqual(code, 4)
            self.assertIn("already exists", err)


class LocalAuditTests(unittest.TestCase):
    def test_offline_fixture_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp) / "sample-cli", SAMPLE_CLI)
            code, out, err = helpers.run_cli(audit.main, [str(root), "--offline", "--json",
                                                          *AS_OF, *RUBRIC])
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertEqual((r["target"]["kind"], r["project_type"]), ("local", "cli"))
        self.assertEqual(r["detection"]["evidence"], ["package.json bin"])
        status = {c["id"]: (c["status"], c["evidence"]) for c in r["checks"]}
        unknown = {k for k, v in status.items() if v[0] == "unknown"}
        self.assertEqual(unknown, OFFLINE_UNKNOWN | {"health.recent_commit", "dist.semver"})
        self.assertEqual(status["health.recent_commit"][1], "no_git")
        self.assertEqual(status["meta.description"][1], "no_remote")
        self.assertEqual(status["readme.tagline"][0], "pass")
        self.assertEqual(status["readme.install"][0], "pass")
        self.assertEqual(status["dist.release"][0], "pass")  # npm i -g sample-cli
        self.assertEqual(status["meta.license"][0], "partial")
        self.assertEqual(status["health.ci"][0], "partial")
        self.assertLess(r["coverage"], 100)

    def test_offline_git_checkout_with_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = helpers.make_repo(Path(tmp) / "r", SAMPLE_CLI,
                                     origin="https://github.com/acme/sample-cli.git")
            helpers.git(repo, "tag", "v0.3.0")
            code, out, err = helpers.run_cli(audit.main, [str(repo), "--offline", "--json",
                                                          *AS_OF, *RUBRIC])
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertEqual(r["target"]["slug"], "acme/sample-cli")
        status = {c["id"]: (c["status"], c["evidence"]) for c in r["checks"]}
        self.assertEqual({k for k, v in status.items() if v[0] == "unknown"}, OFFLINE_UNKNOWN)
        self.assertEqual(status["meta.description"][1], "offline")
        self.assertEqual(status["dist.semver"], ("pass", "Latest tag v0.3.0 is semver."))
        self.assertEqual(status["health.recent_commit"][0], "pass")

    def test_no_network_falls_back_to_local_and_says_so(self):
        import common
        from unittest import mock

        def down(*a, **kw):
            raise common.RLError("E_NETWORK", detail="no answer within 30 s")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(common, "gh_api", down), \
                mock.patch.object(common, "gh_graphql", down):
            repo = helpers.make_repo(Path(tmp) / "r", SAMPLE_CLI,
                                     origin="https://github.com/acme/sample-cli.git")
            code, out, err = helpers.run_cli(audit.main, [str(repo), *AS_OF, *RUBRIC])
            r_code, _, r_err = helpers.run_cli(audit.main, ["acme/sample-cli", *AS_OF])
        self.assertEqual(code, 0, err)
        self.assertIn("> Warning: GitHub could not be reached", out)
        self.assertIn("meta.description (no_network)", out)
        self.assertEqual(r_code, 3)  # a remote target has nothing local to fall back on
        self.assertIn("Cannot reach GitHub", r_err)

    def test_local_rubric_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp), SAMPLE_CLI)
            rubric = json.loads(Path(helpers.BUNDLED_RUBRIC).read_text(encoding="utf-8"))
            rubric["name"] = "team rubric"
            (root / ".leadout").mkdir()
            (root / ".leadout" / "rubric.json").write_text(json.dumps(rubric))
            cwd = os.getcwd()
            os.chdir(root)
            try:
                code, out, err = helpers.run_cli(audit.main, [".", "--offline", *AS_OF])
            finally:
                os.chdir(cwd)
        self.assertEqual(code, 0, err)
        self.assertIn('rubric "team rubric"', out)

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp), SAMPLE_CLI)
            inside = str(root / "facts.json")
            code, _, err = helpers.run_cli(audit.main, [str(root), "--offline",
                                                        "--save-facts", inside])
            self.assertEqual(code, 2)
            self.assertIn("outside the audited repository", err)
            self.assertFalse(os.path.exists(inside))
        code, _, err = helpers.run_cli(audit.main, ["cli/cli", "--offline"])
        self.assertEqual(code, 2)
        self.assertIn("--offline needs a local path", err)
        code, _, err = helpers.run_cli(audit.main, [".", "--as-of", "yesterday"])
        self.assertEqual(code, 2)
        code, out, err = helpers.run_cli(audit.main, ["no such dir", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "E_BAD_TARGET")
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
