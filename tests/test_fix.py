"""Tests for scripts/fix.py: plan, render, write guards, badges, topics, release notes."""
import json
import tempfile
import unittest
from pathlib import Path

import helpers
from helpers import deep, git, load_facts, make_repo, run_cli

import common
import detect
import fix

CARGO = '[package]\nname = "demo"\nversion = "0.1.0"\n\n[[bin]]\nname = "demo"\n'
CLI = {"type": "cli", "language": "Rust", "evidence": [], "frameworks": []}


def audit(*checks):
    """Hand-written audit JSON in the SPEC 9.6 shape; checks are (id, status, phase)."""
    return {"schema": 1, "project_type": "cli", "score": 50, "target": {"slug": "acme/widget"},
            "checks": [{"id": i, "status": s, "phase": p} for i, s, p in checks]}


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.facts = deep(load_facts("base"))
        self.facts["files"] = [f for f in self.facts["files"]
                               if f not in ("SECURITY.md", ".github/workflows/ci.yml")]

    def ids(self, plan):
        return [a["id"] for a in plan]

    def test_failing_checks_become_actions(self):
        a = audit(("readme.tagline", "partial", "fix"), ("readme.visual_demo", "fail", "visuals"),
                  ("comm.security", "fail", "fix"), ("health.ci", "fail", "fix"),
                  ("comm.contributing", "pass", "fix"), ("meta.topics", "partial", "fix"),
                  ("dist.release", "unknown", "fix"))
        plan = fix.build_plan(a, self.facts, CLI, None)
        self.assertEqual(self.ids(plan), ["readme", "security", "ci", "topics"])
        by = {p["id"]: p for p in plan}
        self.assertEqual(by["readme"]["check_ids"], ["readme.tagline"])
        self.assertEqual(by["ci"]["paths"], [".github/workflows/ci.yml"])
        self.assertFalse(by["security"]["exists"])
        self.assertTrue(by["readme"]["exists"])
        self.assertIn("contact", " ".join(fix.todos_in(
            "\n".join(t for _, t in fix.render_files("code-of-conduct", self.facts, CLI, None)))))

    def test_visuals_only_failure_is_not_a_fix_action(self):
        plan = fix.build_plan(audit(("readme.visual_demo", "fail", "visuals")), self.facts, CLI,
                              None)
        self.assertEqual(plan, [])

    def test_ci_not_offered_for_unsupported_language(self):
        lang = dict(CLI, language="Elixir")
        plan = fix.build_plan(audit(("health.ci", "fail", "fix")), self.facts, lang, None)
        self.assertEqual(plan[0]["kind"], "manual")
        self.assertIn("Elixir", plan[0]["summary"])

    def test_badges_action_when_readme_has_none(self):
        self.facts["readme"]["text"] = "# widget\n\nTurns CSV into charts, a long enough line.\n"
        plan = fix.build_plan(audit(("meta.license", "pass", "fix")), self.facts, CLI, None)
        self.assertEqual(self.ids(plan), ["badges"])

    def test_load_audit_rejects_wrong_shape(self):
        with tempfile.TemporaryDirectory() as t:
            bad = Path(t) / "bad.json"
            bad.write_text(json.dumps({"schema": 2, "checks": []}))
            code, _o, e = run_cli(fix.main, ["plan", t, "--audit", str(bad), "--offline"])
            self.assertEqual(code, 2)
            self.assertIn("schema 1", e)

    def test_plan_cli_json_on_temp_repo(self):
        with tempfile.TemporaryDirectory() as t:
            make_repo(Path(t) / "r", {"Cargo.toml": CARGO, "src/main.rs": "fn main() {}\n"})
            f = Path(t) / "audit.json"
            f.write_text(json.dumps(audit(("comm.pr_template", "fail", "fix"))))
            code, out, _e = run_cli(fix.main, ["plan", str(Path(t) / "r"), "--audit", str(f),
                                               "--offline", "--json"])
            data = json.loads(out)
            self.assertEqual(code, 0)
            self.assertEqual([a["id"] for a in data["actions"]], ["pr-template"])
            self.assertEqual(data["project_type"], "cli")


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.facts = deep(load_facts("base"))

    def render(self, action, facts=None, det=CLI):
        return dict(fix.render_files(action, facts or self.facts, det, None))

    def test_every_file_action_renders_without_leftover_placeholders(self):
        for action in fix.FILE_ACTIONS:
            for path, text in fix.render_files(action, self.facts, CLI, None):
                self.assertNotIn("[[", text, f"{action}:{path}")
                self.assertTrue(text.strip())

    def test_readme_uses_only_evidence(self):
        self.facts["files"] = self.facts["files"] + ["docs/demo.gif"]
        text = self.render("readme")["README.md"]
        self.assertIn("# widget", text)
        self.assertIn("Widget turns CSV files into charts from the command line.", text)
        self.assertIn("cargo install widget", text)
        self.assertIn("widget plot data.csv --out chart.png", text)
        self.assertIn("docs/demo.gif", text)
        self.assertIn("MIT", text)

    def test_missing_facts_become_todos(self):
        facts = deep(self.facts)
        facts["repo"]["description"] = ""
        facts["readme"] = None
        text = self.render("readme", facts)["README.md"]
        self.assertIn("TODO(tagline)", text)
        self.assertIn("TODO(install_command)", text)

    def test_readme_per_project_type(self):
        for ptype in detect.TYPES:
            text = self.render("readme", det=dict(CLI, type=ptype))["README.md"]
            self.assertIn("# widget", text)

    def test_claude_plugin_readme_reads_local_commands(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin/plugin.json").write_text('{"name": "demo-plugin"}')
            (root / ".claude-plugin/marketplace.json").write_text('{"name": "demo-market"}')
            (root / "commands").mkdir()
            (root / "commands/hello.md").write_text("---\ndescription: Say hello. More text.\n---\nbody\n")
            files = fix.render_files("readme", self.facts, dict(CLI, type="claude-plugin"), root)
            text = files[0][1]
            self.assertIn("/plugin install demo-plugin@demo-market", text)
            self.assertIn("| `/demo-plugin:hello` | Say hello. |", text)

    def test_contributing_commands_from_tooling(self):
        text = self.render("contributing")["CONTRIBUTING.md"]
        self.assertIn("cargo build", text)
        self.assertIn("cargo test", text)
        self.assertIn("TODO(commit_style)", text)

    def test_security_and_conduct(self):
        self.assertIn("security/advisories/new", self.render("security")["SECURITY.md"])
        self.assertIn("`v1.2.0`", self.render("security")["SECURITY.md"])
        conduct = self.render("code-of-conduct")["CODE_OF_CONDUCT.md"]
        self.assertIn("contributor-covenant.org/version/2/1", conduct)
        self.assertIn("TODO(contact)", conduct)

    def test_issue_templates_paths(self):
        paths = sorted(self.render("issue-templates"))
        self.assertEqual(paths, [".github/ISSUE_TEMPLATE/bug_report.yml",
                                 ".github/ISSUE_TEMPLATE/config.yml",
                                 ".github/ISSUE_TEMPLATE/feature_request.yml"])

    def test_ci_python_versions_from_requires_python(self):
        facts = deep(self.facts)
        facts["manifests"] = {"pyproject.toml": {"name": "x", "requires_python": ">=3.12",
                                                 "dependencies": []}}
        facts["files"] = ["pyproject.toml", "tests/test_a.py"]
        det = dict(CLI, language="Python")
        text = self.render("ci", facts, det)[".github/workflows/ci.yml"]
        self.assertIn('["3.12", "3.13", "3.14", "3.15"]', text)
        self.assertIn("python -m pytest", text)
        self.assertIn("python -m pip install -e .", text)
        self.assertIn("actions/checkout@v7", text)
        self.assertIn("permissions:\n  contents: read", text)

    def test_ci_other_languages(self):
        node = deep(self.facts)
        node["files"] = ["package.json", "package-lock.json"]
        text = self.render("ci", node, dict(CLI, language="JavaScript"))[
            ".github/workflows/ci.yml"]
        self.assertIn("run: npm ci", text)
        rust = self.render("ci")[".github/workflows/ci.yml"]
        self.assertIn("cargo build", rust)
        self.assertNotIn("--locked", rust)
        self.assertIn("go build ./...", self.render("ci", None, dict(CLI, language="Go"))[
            ".github/workflows/ci.yml"])

    def test_changelog_from_releases(self):
        text = self.render("changelog")["CHANGELOG.md"]
        self.assertIn("## [v1.2.0] - 2026-10-01", text)
        self.assertIn("[v1.2.0]: https://github.com/acme/widget/releases/tag/v1.2.0", text)
        self.assertNotIn("TODO(entries)", text)

    def test_render_rejects_non_file_action(self):
        with self.assertRaises(common.RLError):
            fix.render_files("topics", self.facts, CLI, None)


class ReadmeParsingTests(unittest.TestCase):
    def test_sections_ignore_headings_in_code(self):
        text = "# A\n\n```\n# not a heading\n```\n\n## Install\n\n```sh\n$ pip install x\n```\n"
        self.assertEqual([h for _l, h, _b in fix.split_sections(text)], ["A", "Install"])
        self.assertEqual(fix.install_command({"readme": {"text": text}}, "x"), "pip install x")

    def test_install_falls_back_to_registry_line(self):
        facts = {"readme": {"text": "Run `npm i -g tool` now\n\nnpm install tool\n"}}
        self.assertEqual(fix.install_command(facts, "tool"), "npm install tool")

    def test_git_urls_are_not_registry_installs(self):
        facts = {"readme": {"text": "pip install git+https://github.com/o/x\n"}}
        self.assertEqual(fix.install_command(facts, "x"), "")


class BadgeTests(unittest.TestCase):
    def setUp(self):
        self.facts = deep(load_facts("base"))

    def test_badges_from_facts(self):
        badges, warns = fix.build_badges(self.facts, verify=False)
        text = "\n".join(badges)
        self.assertIn("img.shields.io/github/license/acme/widget", text)
        self.assertIn("actions/workflows/ci.yml/badge.svg", text)
        self.assertIn("img.shields.io/github/v/release/acme/widget", text)
        self.assertIn("img.shields.io/github/last-commit/acme/widget", text)
        self.assertIn("img.shields.io/crates/v/widget", text)  # README runs cargo install widget
        self.assertEqual(warns, [])

    def test_no_release_no_license_no_registry(self):
        f = deep(self.facts)
        f["releases"], f["repo"]["license_spdx"], f["readme"]["text"] = [], None, "# widget\n"
        text = "\n".join(fix.build_badges(f, verify=False)[0])
        for absent in ("license", "v/release", "crates"):
            self.assertNotIn(absent, text)

    def test_verification_drops_broken_badge(self):
        def fetch(url):
            return "<svg>repo not found</svg>" if "last-commit" in url else "<svg>ok</svg>"
        badges, warns = fix.build_badges(self.facts, verify=True, fetch=fetch)
        self.assertFalse(any("last-commit" in b for b in badges))
        self.assertEqual([w["code"] for w in warns], ["W_BADGE_DROPPED"])

    def test_verification_failure_keeps_badge_with_warning(self):
        def fetch(url):
            raise OSError("offline")
        badges, warns = fix.build_badges(self.facts, verify=True, fetch=fetch)
        self.assertTrue(any("last-commit" in b for b in badges))
        self.assertIn("W_BADGE_UNVERIFIED", {w["code"] for w in warns})

    def test_no_slug(self):
        f = deep(self.facts)
        f["target"] = {"kind": "local", "slug": None, "path": "/x"}
        badges, warns = fix.build_badges(f, verify=False)
        self.assertEqual(badges, [])
        self.assertEqual(warns[0]["code"], "W_NO_SLUG")


class TopicTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(fix.normalize_topic("C++"), "cpp")
        self.assertEqual(fix.normalize_topic(" Foo Bar_baz "), "foo-bar-baz")
        self.assertEqual(fix.normalize_topic("C#"), "csharp")
        self.assertIsNone(fix.normalize_topic("!!!"))
        self.assertEqual(len(fix.normalize_topic("a" * 80)), 50)

    def test_pertinence_and_command(self):
        facts = deep(load_facts("base"))
        facts["manifests"]["Cargo.toml"]["keywords"] = ["data-viz", "clean", "zzz-nope", "cli"]
        topics = fix.propose_topics(facts, CLI)
        self.assertEqual(topics, ["data-viz", "clean"])  # zzz-nope not pertinent, cli exists
        self.assertEqual(fix.topics_command("acme/widget", topics),
                         "gh repo edit acme/widget --add-topic data-viz --add-topic clean")
        self.assertEqual(fix.topics_command("acme/widget", []), "")

    def test_total_capped_at_twenty(self):
        facts = deep(load_facts("base"))
        facts["repo"]["topics"] = [f"topic-{i}" for i in range(19)]
        facts["manifests"]["Cargo.toml"]["keywords"] = ["clean", "charts-two"]
        self.assertEqual(len(fix.propose_topics(facts, CLI)), 1)

    def test_reaches_the_audit_minimum_without_manifest_keywords(self):
        facts = deep(load_facts("base"))
        facts["repo"]["topics"] = []
        facts["manifests"] = {}
        facts["repo"]["description"] = "Fast benchmarking tool for shell commands, with statistics"
        facts["readme"]["text"] = "Benchmarking shell commands. Statistics for every benchmarking run."
        topics = fix.propose_topics(facts, CLI)
        self.assertGreaterEqual(len(topics), fix._min_topics())
        self.assertIn("benchmarking", topics)
        self.assertNotIn("fast", topics)  # stopword
        self.assertNotIn("tool", topics)

    def test_plugin_manifest_keywords_are_candidates(self):
        import facts as facts_mod
        m = facts_mod.reduce_manifest(".claude-plugin/plugin.json",
                                      '{"name": "x", "keywords": ["audit", 3, "launch"]}')
        self.assertEqual(m["keywords"], ["audit", "launch"])

    def test_claude_plugin_topic(self):
        facts = deep(load_facts("base"))
        facts["repo"]["topics"] = []
        topics = fix.propose_topics(facts, dict(CLI, type="claude-plugin"))
        self.assertIn("claude-code-plugin", topics)


def commit(path, name, msg):
    (Path(path) / name).write_text(msg, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "--quiet", "-m", msg)


class GitTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._t.name) / "r",
                              {"Cargo.toml": CARGO, "src/main.rs": "fn main() {}\n"})

    def tearDown(self):
        self._t.cleanup()

    def write(self, *extra):
        return run_cli(fix.main, ["write", *extra, "--offline"])

    def test_write_refuses_default_branch(self):
        code, _o, err = self.write("contributing", str(self.root))
        self.assertEqual(code, 4)
        self.assertIn("Refusing to write on the default branch (main)", err)
        self.assertFalse((self.root / "CONTRIBUTING.md").exists())

    def test_write_refuses_detached_head(self):
        git(self.root, "checkout", "--quiet", "--detach")
        code, _o, err = self.write("contributing", str(self.root))
        self.assertEqual(code, 4)
        self.assertIn("default branch", err)

    def test_write_on_feature_branch_then_refuses_existing(self):
        git(self.root, "switch", "-c", "leadout/fix-test")
        code, out, _e = self.write("contributing", str(self.root))
        self.assertEqual(code, 0)
        self.assertIn("wrote CONTRIBUTING.md", out)
        self.assertIn("cargo test", (self.root / "CONTRIBUTING.md").read_text())
        code, _o, err = self.write("contributing", str(self.root))
        self.assertEqual(code, 4)
        self.assertIn("already exists", err)
        code, _o, _e = self.write("contributing", str(self.root), "--overwrite")
        self.assertEqual(code, 0)

    def test_write_multi_file_action(self):
        git(self.root, "switch", "-c", "leadout/fix-test")
        self.assertEqual(self.write("issue-templates", str(self.root))[0], 0)
        self.assertTrue((self.root / ".github/ISSUE_TEMPLATE/bug_report.yml").exists())

    def test_existing_readme_needs_reviewed_content(self):
        git(self.root, "switch", "-c", "leadout/fix-test")
        (self.root / "README.md").write_text("# demo\n\nOld text.\n")
        code, _o, err = self.write("readme", str(self.root), "--overwrite")
        self.assertEqual(code, 2)
        self.assertIn("--content-file", err)
        new = Path(self._t.name) / "new.md"
        new.write_text("# demo\n\nReviewed text.\n")
        code, _o, _e = self.write("readme", str(self.root), "--overwrite",
                                  "--content-file", str(new))
        self.assertEqual(code, 0)
        self.assertIn("Reviewed text.", (self.root / "README.md").read_text())

    def test_badges_write_needs_content_file(self):
        git(self.root, "switch", "-c", "leadout/fix-test")
        self.assertEqual(self.write("badges", str(self.root))[0], 2)

    def test_write_outside_git(self):
        plain = Path(self._t.name) / "plain"
        plain.mkdir()
        code, _o, err = self.write("contributing", str(plain))
        self.assertEqual(code, 4)
        self.assertIn("not a git repository", err)

    def test_write_needs_local_target(self):
        code, _o, err = self.write("contributing", "acme/widget")
        self.assertEqual(code, 4)
        self.assertIn("Clone", err)

    def test_release_notes_grouping(self):
        git(self.root, "tag", "v0.1.0")
        for i, msg in enumerate(["feat: add plot command", "fix(core): handle empty file",
                                 "docs: explain install", "chore: bump deps",
                                 "tidy things up", "feat!: new output format"]):
            commit(self.root, f"f{i}.txt", msg)
        code, out, _e = run_cli(fix.main, ["release-notes", str(self.root), "--offline",
                                           "--title", "v0.2.0"])
        self.assertEqual(code, 0)
        order = [out.index(h) for h in ("### Features", "### Bug fixes", "### Documentation",
                                        "### Maintenance", "### Other changes")]
        self.assertEqual(order, sorted(order))
        self.assertIn("- add plot command", out)
        self.assertIn("- new output format", out)
        self.assertIn("- tidy things up", out)
        self.assertNotIn("initial", out)
        self.assertNotIn("### Tests", out)
        self.assertIn("## v0.2.0", out)

    def test_release_notes_without_tags_covers_history(self):
        commit(self.root, "a.txt", "feat: first")
        _c, out, _e = run_cli(fix.main, ["release-notes", str(self.root), "--offline"])
        self.assertIn("- first", out)
        self.assertIn("- initial", out)

    def test_release_notes_bad_ref(self):
        code, _o, err = run_cli(fix.main, ["release-notes", str(self.root), "--offline",
                                           "--from", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("nope", err)

    def test_topics_and_badges_cli(self):
        code, out, _e = run_cli(fix.main, ["topics", str(self.root), "--offline", "--json"])
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn("rust", data["topics"])
        code, out, err = run_cli(fix.main, ["badges", str(self.root), "--offline", "--no-verify"])
        self.assertEqual(code, 0)
        self.assertIn("No GitHub remote", err)


if __name__ == "__main__":
    unittest.main()
