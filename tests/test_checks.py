"""Every audit evaluator (pass/partial/fail/skip/unknown), project detection, TOML reader."""
from __future__ import annotations

import unittest

import helpers
import audit
import detect
import facts as facts_mod

BASE = helpers.load_facts("base")
FILLER = " ".join(["Widget reads tabular data and draws a chart for it."] * 40)  # 400 words


def ev(kind, facts, ptype="cli", **params):
    fn, defaults = audit.EVALUATORS[kind]
    return fn(facts, ptype, {**defaults, **params})


def with_readme(text, **changes):
    f = helpers.deep(BASE)
    f["readme"] = {"path": "README.md", "text": text}
    f.update(changes)
    return f


def changed(path: str, value):
    """Copy of BASE with one dotted key replaced."""
    f = helpers.deep(BASE)
    node = f
    *parents, last = path.split(".")
    for p in parents:
        node = node[p]
    node[last] = value
    return f


class ReadmeTests(unittest.TestCase):
    def test_no_readme_scores_zero_everywhere(self):
        f = changed("readme", None)
        self.assertEqual(ev("readme_exists", f)[0], 0.0)
        for kind in ("readme_tagline", "readme_visual", "readme_install", "readme_usage",
                     "readme_contributing", "readme_length"):
            self.assertEqual(ev(kind, f), (0.0, "No README."), kind)

    def test_readme_unavailable_is_unknown(self):
        f = changed("readme", None)
        f["unavailable"] = {"readme": "forbidden"}
        self.assertEqual(ev("readme_exists", f), (None, "forbidden"))
        self.assertEqual(ev("readme_tagline", f), (None, "forbidden"))

    def test_exists(self):
        self.assertEqual(ev("readme_exists", BASE)[0], 1.0)

    def test_tagline_pass_after_badges(self):
        text = ("# tool\n[![a](https://img.shields.io/a)](x) [![b](https://img.shields.io/b)](y)"
                "\n\nTool converts Markdown tables to CSV in one command.\n")
        s, e = ev("readme_tagline", with_readme(text))
        self.assertEqual(s, 1.0)
        self.assertIn("52 characters", e)

    def test_tagline_html_centered_header(self):
        text = ('<p align="center">\n  <img src="assets/logo.svg" width="200">\n</p>\n'
                '<h1 align="center">Tool</h1>\n'
                '<p align="center">A fast tool for building static sites quickly.</p>\n')
        self.assertEqual(ev("readme_tagline", with_readme(text))[0], 1.0)

    def test_tagline_multiline_img_tag_is_skipped(self):
        text = '<img\n  src="logo.png"\n  width="100">\n\nSmall tool that renames photos by date.\n'
        self.assertEqual(ev("readme_tagline", with_readme(text))[0], 1.0)

    def test_tagline_setext_heading_and_front_matter(self):
        text = ("---\ntitle: x\n---\nTool\n====\n\n"
                "Tool keeps your dotfiles in sync across machines.\n")
        self.assertEqual(ev("readme_tagline", with_readme(text))[0], 1.0)

    def test_tagline_too_long_or_short(self):
        long = "# t\n\n" + "word " * 80 + "\n"
        self.assertEqual(ev("readme_tagline", with_readme(long))[0], 0.5)
        self.assertEqual(ev("readme_tagline", with_readme("# t\n\nShort.\n"))[0], 0.5)

    def test_tagline_placeholder_and_missing(self):
        self.assertEqual(ev("readme_tagline", with_readme("# t\n\nTODO: describe it here.\n"))[0],
                         0.0)
        cra = "# app\n\nThis project was bootstrapped with Create React App.\n"
        self.assertEqual(ev("readme_tagline", with_readme(cra))[0], 0.0)
        late = "# t\n" + "\n".join(f"## h{i}" for i in range(20)) + "\nA real sentence here ok.\n"
        self.assertEqual(ev("readme_tagline", with_readme(late))[0], 0.0)

    def test_visual_gif_pass_and_badges_excluded(self):
        self.assertEqual(ev("readme_visual", BASE)[0], 1.0)
        badges = "# t\n[![ci](https://github.com/a/b/actions/workflows/ci.yml/badge.svg)](x)\n" \
                 "![cov](https://codecov.io/gh/a/b/graph.png)\n"
        self.assertEqual(ev("readme_visual", with_readme(badges))[0], 0.0)

    def test_visual_svg_logo_only(self):
        text = '<p align="center"><img src="logo.svg"></p>\n\nTool.\n'
        s, e = ev("readme_visual", with_readme(text))
        self.assertEqual(s, 0.5)
        self.assertIn("SVG", e)

    def test_visual_demo_after_line_50(self):
        text = "# t\n" + "\n".join(f"line {i}" for i in range(60)) + "\n![demo](demo.gif)\n"
        s, e = ev("readme_visual", with_readme(text))
        self.assertEqual(s, 0.5)
        self.assertIn("after line 50", e)

    def test_visual_video_link_and_attachment(self):
        yt = "# t\n[![video](thumb.svg)](https://www.youtube.com/watch?v=abc)\n"
        self.assertEqual(ev("readme_visual", with_readme(yt))[0], 1.0)
        att = "# t\n\nhttps://github.com/user-attachments/assets/1234-abcd\n"
        self.assertEqual(ev("readme_visual", with_readme(att))[0], 1.0)

    def test_visual_ignores_code_and_comments(self):
        text = "# t\n<!-- ![x](demo.gif) -->\n```\n![y](demo.png)\n```\n"
        self.assertEqual(ev("readme_visual", with_readme(text))[0], 0.0)

    def test_install_one_command(self):
        s, e = ev("readme_install", BASE)
        self.assertEqual(s, 1.0)
        self.assertIn("Installation", e)

    def test_install_multiline_block(self):
        two = "# t\n## Install\n```sh\n$ git clone x\n# comment\n$ make install\n```\n"
        self.assertEqual(ev("readme_install", with_readme(two))[0], 0.5)
        five = "# t\n## Install\n```\na\nb\nc\nd\ne\n```\n"
        self.assertEqual(ev("readme_install", with_readme(five))[0], 0.25)
        prose = "# t\n## Getting started\nDownload it and run it.\n"
        self.assertEqual(ev("readme_install", with_readme(prose))[0], 0.25)
        self.assertEqual(ev("readme_install", with_readme("# t\n\nNothing.\n"))[0], 0.0)

    def test_install_best_section_and_subsections(self):
        text = ("# t\n## Quick start\nRead on.\n## Installation\n### macOS\n```\nbrew install t\n"
                "```\n")
        self.assertEqual(ev("readme_install", with_readme(text))[0], 1.0)

    def test_install_plugin_line(self):
        text = "# p\n## Install\nRun `/plugin install demo@demo` inside Claude Code.\n"
        self.assertEqual(ev("readme_install", with_readme(text), "claude-plugin")[0], 1.0)
        self.assertEqual(ev("readme_install", with_readme(text), "cli")[0], 0.25)

    def test_install_desktop_release_link(self):
        text = ("# app\n## Download\n"
                "Get it from [releases](https://github.com/a/b/releases/latest).\n")
        self.assertEqual(ev("readme_install", with_readme(text), "desktop-app")[0], 1.0)
        self.assertEqual(ev("readme_install", with_readme(text), "cli")[0], 0.25)

    def test_usage(self):
        self.assertEqual(ev("readme_usage", BASE)[0], 1.0)
        table = "# t\n## Commands\n| cmd | what |\n|---|---|\n| a | b |\n"
        self.assertEqual(ev("readme_usage", with_readme(table))[0], 1.0)
        bare = "# t\n## Usage\nRun it.\n"
        self.assertEqual(ev("readme_usage", with_readme(bare))[0], 0.5)
        self.assertEqual(ev("readme_usage", with_readme("# t\n## About\nx\n"))[0], 0.0)

    def test_contributing(self):
        self.assertEqual(ev("readme_contributing", BASE)[0], 1.0)
        link = "# t\nPRs welcome, see [the guide](docs/CONTRIBUTING.md).\n"
        self.assertEqual(ev("readme_contributing", with_readme(link))[0], 1.0)
        self.assertEqual(ev("readme_contributing", with_readme("# t\nx\n"))[0], 0.0)

    def test_length(self):
        self.assertEqual(ev("readme_length", with_readme("# t\n\n" + FILLER))[0], 1.0)
        half = " ".join(["word"] * 200)
        self.assertEqual(ev("readme_length", with_readme(half))[0], 0.5)
        self.assertEqual(ev("readme_length", BASE)[0], 0.0)
        code = "```\n" + " ".join(["code"] * 400) + "\n```\n"
        self.assertEqual(ev("readme_length", with_readme(code))[0], 0.0)


class MetadataTests(unittest.TestCase):
    def test_offline_is_unknown(self):
        f = changed("repo", None)
        f["unavailable"] = {"repo": "offline"}
        for kind in ("meta_description", "meta_topics", "meta_homepage", "meta_social_preview"):
            self.assertEqual(ev(kind, f), (None, "offline"), kind)

    def test_description(self):
        self.assertEqual(ev("meta_description", BASE)[0], 1.0)
        self.assertEqual(ev("meta_description", changed("repo.description", "widget"))[0], 0.5)
        self.assertEqual(ev("meta_description", changed("repo.description", "Charts"))[0], 0.5)
        self.assertEqual(ev("meta_description", changed("repo.description", ""))[0], 0.0)

    def test_topics(self):
        self.assertEqual(ev("meta_topics", BASE)[0], 1.0)
        s, e = ev("meta_topics", changed("repo.topics", ["csv", "blockchain"]))
        self.assertEqual(s, 0.2)  # 2/5 count x 1/2 pertinent
        self.assertIn("not pertinent: blockchain", e)
        self.assertEqual(ev("meta_topics", changed("repo.topics", []))[0], 0.0)

    def test_homepage(self):
        self.assertEqual(ev("meta_homepage", BASE)[0], 1.0)
        self.assertEqual(ev("meta_homepage", changed("repo.homepage", "widget.org"))[0], 0.0)

    def test_social_preview(self):
        self.assertEqual(ev("meta_social_preview", BASE)[0], 1.0)
        self.assertEqual(ev("meta_social_preview", changed("repo.uses_custom_og_image",
                                                           False))[0], 0.0)
        self.assertIsNone(ev("meta_social_preview", changed("repo.uses_custom_og_image",
                                                            None))[0])

    def test_license(self):
        self.assertEqual(ev("meta_license", BASE)[0], 1.0)
        self.assertEqual(ev("meta_license", changed("repo.license_spdx", "NOASSERTION"))[0], 0.5)
        f = changed("repo.license_spdx", None)
        f["files"] = ["README.md"]
        self.assertEqual(ev("meta_license", f)[0], 0.0)
        offline = changed("repo", None)
        self.assertEqual(ev("meta_license", offline)[0], 0.5)
        offline["files"] = ["README.md"]
        self.assertEqual(ev("meta_license", offline)[0], 0.0)


class DistributionTests(unittest.TestCase):
    def test_release_from_registry_readme(self):
        s, e = ev("dist_release", BASE)
        self.assertEqual(s, 1.0)
        self.assertIn("cargo install widget", e)

    def test_release_git_url_is_not_a_registry(self):
        f = with_readme("# t\n```\npip install git+https://github.com/acme/widget\n```\n")
        f["releases"] = []
        self.assertEqual(ev("dist_release", f)[0], 0.0)

    def test_release_assets(self):
        f = with_readme("# t\n")
        self.assertEqual(ev("dist_release", f)[0], 1.0)
        f["releases"][0]["assets"] = []
        self.assertEqual(ev("dist_release", f)[0], 0.5)
        f["releases"] = [{"tag": "v2", "draft": True, "assets": [{"name": "x"}]}]
        self.assertEqual(ev("dist_release", f)[0], 0.0)
        f["releases"] = None
        f["unavailable"] = {"releases": "offline"}
        self.assertEqual(ev("dist_release", f), (None, "offline"))

    def test_release_plugin_marketplace(self):
        f = with_readme("# p\n", releases=None)
        f["files"] = [".claude-plugin/marketplace.json", ".claude-plugin/plugin.json"]
        self.assertEqual(ev("dist_release", f, "claude-plugin")[0], 1.0)

    def test_semver(self):
        self.assertEqual(ev("dist_semver", BASE)[0], 1.0)
        f = changed("releases", [{"tag": "nightly", "draft": False, "assets": []}])
        self.assertEqual(ev("dist_semver", f)[0], 0.5)
        f["tags"] = ["latest"]
        self.assertEqual(ev("dist_semver", f)[0], 0.0)
        f["releases"], f["tags"] = [], []
        self.assertEqual(ev("dist_semver", f)[0], 0.0)
        local = changed("releases", None)
        local["tags"] = ["v0.3.1-beta.2"]
        self.assertEqual(ev("dist_semver", local)[0], 1.0)
        local["tags"] = None
        self.assertIsNone(ev("dist_semver", local)[0])

    def test_changelog(self):
        self.assertEqual(ev("dist_changelog", BASE)[0], 1.0)
        f = changed("files", ["README.md"])
        self.assertEqual(ev("dist_changelog", f)[0], 1.0)  # release body 420 chars
        f["releases"][0]["body_chars"] = 10
        self.assertEqual(ev("dist_changelog", f)[0], 0.5)
        f["releases"] = []
        self.assertEqual(ev("dist_changelog", f)[0], 0.0)
        f["releases"] = None
        self.assertIsNone(ev("dist_changelog", f)[0])
        self.assertEqual(ev("dist_changelog", changed("files", ["docs/HISTORY.rst"]))[0], 1.0)


class CommunityTests(unittest.TestCase):
    def test_files_found(self):
        for name in ("contributing", "code_of_conduct", "issue_template",
                     "pull_request_template", "security"):
            self.assertEqual(ev("comm_file", BASE, file=name)[0], 1.0, name)

    def test_files_missing_and_org_default(self):
        f = changed("files", ["README.md", ".github/ISSUE_TEMPLATE/config.yml"])
        f["community"] = {"contributing": True, "issue_template": False}
        self.assertEqual(ev("comm_file", f, file="contributing")[0], 1.0)  # org default
        self.assertEqual(ev("comm_file", f, file="issue_template")[0], 0.0)  # config only
        self.assertEqual(ev("comm_file", f, file="security")[0], 0.0)
        f["files"] = ["docs/PULL_REQUEST_TEMPLATE/feature.md", "ISSUE_TEMPLATE.md"]
        self.assertEqual(ev("comm_file", f, file="pull_request_template")[0], 1.0)
        self.assertEqual(ev("comm_file", f, file="issue_template")[0], 1.0)

    def test_good_first_issue(self):
        self.assertEqual(ev("comm_gfi", BASE)[0], 1.0)
        self.assertEqual(ev("comm_gfi", changed("issues.gfi_open", 0))[0], 0.5)
        self.assertEqual(ev("comm_gfi", changed("issues.gfi_labels", []))[0], 0.0)
        self.assertEqual(ev("comm_gfi", changed("repo.has_issues", False))[0], "skip")
        f = changed("issues", None)
        f["unavailable"] = {"issues": "forbidden"}
        self.assertEqual(ev("comm_gfi", f), (None, "forbidden"))


class HealthTests(unittest.TestCase):
    def test_recent_commit(self):
        self.assertEqual(ev("health_recent_commit", BASE)[0], 1.0)
        self.assertEqual(ev("health_recent_commit", changed("last_commit_at",
                                                            "2025-01-01T00:00:00Z"))[0], 0.0)
        # 182 days: (365 - 182) / (365 - 90) = 0.6654... -> 0.67
        self.assertEqual(ev("health_recent_commit", changed("last_commit_at",
                                                            "2026-05-27T23:59:00Z"))[0], 0.67)
        s, e = ev("health_recent_commit", changed("repo.archived", True))
        self.assertEqual((s, e), (0.0, "The repository is archived."))
        f = changed("last_commit_at", None)
        f["unavailable"] = {"last_commit_at": "no_git"}
        self.assertEqual(ev("health_recent_commit", f), (None, "no_git"))

    def test_ci(self):
        self.assertEqual(ev("health_ci", BASE)[0], 1.0)
        f = changed("ci.latest_default_branch_run", {"conclusion": "failure",
                                                     "created_at": "2026-11-20T10:05:00Z"})
        self.assertEqual(ev("health_ci", f)[0], 0.25)
        self.assertEqual(ev("health_ci", changed("ci.latest_default_branch_run", None))[0], 0.5)
        self.assertEqual(ev("health_ci", changed("files", [".travis.yml"]))[0], 0.5)
        self.assertEqual(ev("health_ci", changed("files", ["README.md"]))[0], 0.0)

    def test_issue_response(self):
        self.assertEqual(ev("health_issue_response", BASE)[0], 1.0)  # #13 is 1 day old
        sample = [{"number": n, "created_at": "2026-10-01T00:00:00Z", "comments": c}
                  for n, c in ((1, 0), (2, 0), (3, 1), (4, 3))]
        s, e = ev("health_issue_response", changed("issues.open_sample", sample))
        self.assertEqual(s, 0.5)
        self.assertIn("2 of 4", e)
        self.assertEqual(ev("health_issue_response", changed("issues.open_sample", []))[0], 1.0)
        self.assertEqual(ev("health_issue_response", changed("repo.has_issues", False))[0],
                         "skip")
        self.assertIsNone(ev("health_issue_response", changed("issues", None))[0])


class DetectTests(unittest.TestCase):
    def kind(self, files, manifests=None):
        return detect.detect({"files": files, "manifests": manifests or {}})["type"]

    def test_types(self):
        self.assertEqual(self.kind(["skills/a/SKILL.md"]), "claude-plugin")
        self.assertEqual(self.kind(["package.json"], {"package.json": {
            "dependencies": ["electron"]}}), "desktop-app")
        self.assertEqual(self.kind(["index.html"]), "website")
        self.assertEqual(self.kind(["index.html", "package.json"],
                                   {"package.json": {"bin": ["x"]}}), "cli")
        self.assertEqual(self.kind(["Cargo.toml", "src/main.rs"], {"Cargo.toml": {}}), "cli")
        self.assertEqual(self.kind(["go.mod", "cmd/tool/main.go"]), "cli")
        self.assertEqual(self.kind(["go.mod", "lib.go"]), "library")
        self.assertEqual(self.kind(["pyproject.toml"], {"pyproject.toml": {"scripts": []}}),
                         "library")
        self.assertEqual(self.kind(["notes.txt"]), "other")

    def test_skill_folders_do_not_override_a_cli(self):
        # cli/cli ships skills/*/SKILL.md next to its Go command
        self.assertEqual(self.kind(["go.mod", "cmd/gh/main.go", "skills/gh/SKILL.md"]), "cli")
        self.assertEqual(self.kind(["skills/a/SKILL.md", "README.md"]), "claude-plugin")

    def test_python_library_with_a_command(self):
        py = {"pyproject.toml": {"name": "Flask", "scripts": ["flask"]}}
        lib = detect.detect({"files": ["pyproject.toml"], "manifests": py,
                             "readme": {"text": "```python\nfrom flask import Flask\n```"}})
        tool = detect.detect({"files": ["pyproject.toml"], "manifests": py,
                              "readme": {"text": "```sh\nflask run\n```"}})
        self.assertEqual((lib["type"], tool["type"]), ("library", "cli"))

    def test_flutter_desktop_in_pub_workspace(self):
        # localsend layout: workspace pubspec at the root, the Flutter app in app/
        files = ["pubspec.yaml", "Cargo.toml", "app/pubspec.yaml", "app/linux/my_application.cc"]
        self.assertEqual(self.kind(files, {"Cargo.toml": {}}), "desktop-app")
        self.assertEqual(self.kind(["pubspec.yaml", "macos/Runner.xcodeproj/project.pbxproj"]),
                         "desktop-app")
        # a runner file without its pubspec is not Flutter
        self.assertNotEqual(self.kind(["app/linux/my_application.cc"]), "desktop-app")

    def test_base_and_language_fallback(self):
        d = detect.detect(BASE)
        self.assertEqual((d["type"], d["evidence"], d["language"]),
                         ("cli", ["Cargo binary"], "Rust"))
        self.assertEqual(detect.detect({"files": [], "ext_bytes": {".py": 10, ".js": 5}})[
            "language"], "Python")


class TomlTests(unittest.TestCase):
    TEXT = ('[project]\nname = "pkg"  # comment\nkeywords = ["a", \'b\']\ndependencies = [\n'
            '  "requests>=2",\n  "rich",\n]\n\n[project.scripts]\npkg = "pkg.cli:main"\n\n'
            '[[bin]]\nname = "tool"\n')

    def test_regex_reader(self):
        t = facts_mod.toml_regex(self.TEXT)
        self.assertEqual(t["project"]["name"], "pkg")
        self.assertEqual(t["project"]["keywords"], ["a", "b"])
        self.assertEqual(t["project"]["dependencies"], ["requests>=2", "rich"])
        self.assertEqual(t["project"]["scripts"], {"pkg": "pkg.cli:main"})
        self.assertEqual(t["bin"], [{"name": "tool"}])

    def test_manifest_reduction(self):
        m = facts_mod.reduce_manifest("pyproject.toml", self.TEXT)
        self.assertEqual((m["name"], m["scripts"], m["dependencies"]),
                         ("pkg", ["pkg"], ["requests", "rich"]))
        pkg = facts_mod.reduce_manifest("package.json", '{"name": "x", "bin": "cli.js"}')
        self.assertEqual(pkg["bin"], ["x"])


if __name__ == "__main__":
    unittest.main()
