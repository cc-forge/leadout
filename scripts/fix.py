"""Plan and apply the fixes found by the leadout audit (SPEC sections 7.5 and 10).

Subcommands:
    plan           list the fix actions, from the audit JSON (SPEC 9.6)
    render         print the generated content of a file action (no writes)
    write          write a file action into a git checkout, on a non-default branch
    badges         badge line built from real data, each shields.io badge verified
    topics         proposed topics and the exact `gh repo edit` command (never run)
    release-notes  markdown notes from `git log`, grouped by Conventional Commit type

Usage:
    python3 scripts/fix.py plan [TARGET] [--audit FILE|-] [--type TYPE] [--offline] [--json]
    python3 scripts/fix.py render ACTION [TARGET] [--type TYPE] [--offline]
    python3 scripts/fix.py write ACTION [TARGET] [--overwrite] [--content-file FILE]
    python3 scripts/fix.py badges [TARGET] [--no-verify] [--json]
    python3 scripts/fix.py topics [TARGET] [--json]
    python3 scripts/fix.py release-notes [TARGET] [--from TAG] [--to REF] [--title T]

Example:
    python3 scripts/fix.py plan . --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import re
import shlex
import subprocess
import sys
import urllib.request
from pathlib import Path

import common
import detect as detect_mod
import facts as facts_mod

TEMPLATES = common.PLUGIN_ROOT / "templates"
SEMVER = re.compile(r"^v?\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
# ponytail: newest stable Python minor known when this was written (3.15, 2026-11-25);
# bump it when a new minor ships
PY_LATEST_MINOR = 15
BAD_BADGE_TEXT = ("not found", "invalid", "inaccessible", "no releases", "repo not found")
MEDIA_RE = re.compile(r"\.(gif|mp4|webm|mov|png|jpe?g|webp)$", re.I)
SCREENSHOT_NAME = re.compile(r"demo|screenshot|preview|screen", re.I)
LANG_CI = {"Python": "ci-python.yml", "JavaScript": "ci-node.yml", "TypeScript": "ci-node.yml",
           "Go": "ci-go.yml", "Rust": "ci-rust.yml"}
NOTE_SECTIONS = [("feat", "Features"), ("fix", "Bug fixes"), ("perf", "Performance"),
                 ("docs", "Documentation"), ("refactor", "Refactoring"), ("test", "Tests"),
                 ("build", "Maintenance"), ("ci", "Maintenance"), ("chore", "Maintenance"),
                 ("style", "Maintenance"), ("revert", "Maintenance")]
CONVENTIONAL = re.compile(r"^(\w+)(\([^)]*\))?(!)?:\s*(.+)$")

# action id -> (kind, rubric check ids that trigger it)
ACTIONS = {
    "readme": ("file", ["readme.*"]),
    "badges": ("file", []),
    "contributing": ("file", ["comm.contributing", "readme.contributing"]),
    "code-of-conduct": ("file", ["comm.code_of_conduct"]),
    "security": ("file", ["comm.security"]),
    "issue-templates": ("file", ["comm.issue_templates"]),
    "pr-template": ("file", ["comm.pr_template"]),
    "ci": ("file", ["health.ci"]),
    "changelog": ("file", ["dist.changelog"]),
    "license": ("manual", ["meta.license"]),
    "topics": ("metadata", ["meta.topics"]),
    "description": ("metadata", ["meta.description"]),
    "homepage": ("metadata", ["meta.homepage"]),
    "release": ("release", ["dist.release", "dist.semver"]),
    "good-first-issues": ("manual", ["comm.good_first_issue"]),
    "social-preview": ("manual", ["meta.social_preview"]),
}
FILE_ACTIONS = [a for a, (k, _) in ACTIONS.items() if k == "file"]
SUMMARIES = {
    "readme": "Rewrite the README from the project-type template, keeping every true fact.",
    "badges": "Add a verified badge line at the top of the README.",
    "contributing": "Add CONTRIBUTING.md with setup, tests and the pull request flow.",
    "code-of-conduct": "Add CODE_OF_CONDUCT.md that adopts Contributor Covenant 2.1.",
    "security": "Add SECURITY.md with private vulnerability reporting.",
    "issue-templates": "Add bug report and feature request issue forms.",
    "pr-template": "Add a pull request template.",
    "ci": "Add a CI workflow for the detected language.",
    "changelog": "Add CHANGELOG.md (Keep a Changelog) with one entry per release or tag.",
    "license": "Choose a license yourself (https://choosealicense.com); leadout never "
               "picks one.",
    "topics": "Add pertinent topics with gh repo edit (run: fix.py topics).",
    "description": "Write a one-sentence description with the user, then set it with "
                   "gh repo edit --description.",
    "homepage": "Set the homepage to a URL the user gives, with gh repo edit --homepage.",
    "release": "Draft a release with notes from fix.py release-notes.",
    "good-first-issues": "Pick up to 5 small open issues to label; each label needs its own "
                         "approval.",
    "social-preview": "Make the social preview with /leadout:visuals and upload it in "
                      "Settings > Social preview.",
}


# ---------------------------------------------------------------- README parsing


_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def split_sections(text: str) -> list:
    """[(level, heading, body)] for every markdown heading outside code fences."""
    secs, cur, fenced = [], None, False
    for line in (text or "").splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        m = None if fenced else _HEADING.match(line)
        if m:
            cur = [len(m.group(1)), m.group(2), []]
            secs.append(cur)
        elif cur is not None:
            cur[2].append(line)
    return [(lv, h, "\n".join(b)) for lv, h, b in secs]


def code_blocks(body: str) -> list:
    blocks, cur, fenced = [], [], False
    for line in body.splitlines():
        if _FENCE.match(line):
            if fenced and cur:
                blocks.append("\n".join(cur))
            cur, fenced = [], not fenced
        elif fenced:
            cur.append(line)
    return blocks


def command_lines(block: str) -> list:
    out = []
    for ln in block.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        out.append(re.sub(r"^[$>]\s+", "", s))
    return out


_INSTALL_H = re.compile(r"install|getting started|quick ?start|setup|download", re.I)
_USAGE_H = re.compile(r"usage|example|tutorial|how to use|demo|commands", re.I)
_REGISTRY = re.compile(
    r"^(pip3? install|pipx install|uv tool install|uvx|npm (?:i|install)|npx|pnpm add|"
    r"yarn add|cargo install|go install|brew install|gem install|composer require|"
    r"docker (?:pull|run)|winget install|scoop install)\s+\S+")


def install_command(facts: dict, name: str) -> str:
    text = (facts.get("readme") or {}).get("text") or ""
    for _lv, h, body in split_sections(text):
        if _INSTALL_H.search(h):
            for block in code_blocks(body):
                lines = command_lines(block)
                if 1 <= len(lines) <= 3:
                    return "\n".join(lines)
    for ln in text.splitlines():
        s = re.sub(r"^[$>]\s+", "", ln.strip())
        if _REGISTRY.match(s) and name and name in s and "git+" not in s and "://" not in s:
            return s
    return ""


def usage_example(facts: dict) -> str:
    text = (facts.get("readme") or {}).get("text") or ""
    for _lv, h, body in split_sections(text):
        if _USAGE_H.search(h):
            blocks = code_blocks(body)
            if blocks:
                return "\n".join(ln.rstrip() for ln in blocks[0].splitlines()).strip("\n")
    return ""


def has_badges(text: str) -> bool:
    head = "\n".join((text or "").splitlines()[:30])
    return bool(re.search(r"img\.shields\.io|/badge\.svg|badgen\.net|badge\.fury\.io", head))


# ---------------------------------------------------------------- facts to values


slug_of = common.slug_of
repo_name = common.repo_name


def tooling(facts: dict) -> dict:
    """setup/test/build/run commands, only for tools whose manifest is in the repository."""
    m = facts.get("manifests") or {}
    files = set(facts.get("files") or [])
    pkg, py, cargo = m.get("package.json"), m.get("pyproject.toml"), m.get("Cargo.toml")
    if pkg is not None:
        scripts = pkg.get("scripts") or []
        run = "npm run dev" if "dev" in scripts else ("npm start" if "start" in scripts else "")
        return {"setup": "npm ci" if "package-lock.json" in files else "npm install",
                "test": "npm test" if "test" in scripts else "",
                "build": "npm run build" if "build" in scripts else "", "run": run}
    if py is not None or "setup.py" in m:
        deps = (py or {}).get("dependencies") or []
        return {"setup": "python -m pip install -e .",
                "test": "python -m pytest" if any("pytest" in d for d in deps) else "",
                "build": "", "run": ""}
    if cargo is not None:
        return {"setup": "cargo build", "test": "cargo test", "build": "cargo build",
                "run": ""}
    if "go.mod" in files:
        return {"setup": "go build ./...", "test": "go test ./...", "build": "go build ./...",
                "run": ""}
    return {"setup": "", "test": "", "build": "", "run": ""}


def demo_path(facts: dict) -> str:
    media = [f for f in facts.get("files") or [] if MEDIA_RE.search(f)]
    for f in media:
        if re.search(r"\.(gif|mp4|webm|mov)$", f, re.I):
            return f
    for f in media:
        if SCREENSHOT_NAME.search(Path(f).name):
            return f
    return ""


def _frontmatter_description(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    d = re.search(r"^description:\s*(.+)$", m.group(1), re.M) if m else None
    if not d:
        return ""
    desc = d.group(1).strip().strip("\"'")
    return re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0][:160]


def plugin_info(root: Path | None, name: str) -> dict:
    out = {"plugin_name": "", "marketplace_name": "", "commands_table": ""}
    if root is None:
        return out
    for key, rel in (("plugin_name", ".claude-plugin/plugin.json"),
                     ("marketplace_name", ".claude-plugin/marketplace.json")):
        try:
            out[key] = str(json.loads((root / rel).read_text(encoding="utf-8")).get("name", ""))
        except (OSError, ValueError):
            pass
    plugin = out["plugin_name"] or name
    rows = []
    for f in sorted((root / "commands").glob("*.md")):
        rows.append(f"| `/{plugin}:{f.stem}` | "
                    f"{_frontmatter_description(f) or 'TODO(description)'} |")
    if rows:
        out["commands_table"] = "| Command | What it does |\n|---|---|\n" + "\n".join(rows)
    return out


def commit_style(root: Path | None) -> str:
    if root is None:
        return ""
    log = common.git(["log", "-20", "--format=%s"], root)
    subjects = [s for s in (log or "").splitlines() if s.strip()]
    if len(subjects) >= 5 and sum(bool(CONVENTIONAL.match(s)) for s in subjects) >= 0.8 * len(
            subjects):
        return "This project uses Conventional Commits: `type(scope): summary`."
    return ""


def latest_tag(facts: dict) -> str:
    for r in facts.get("releases") or []:
        if not r.get("draft"):
            return r.get("tag") or ""
    tags = facts.get("tags") or []
    return tags[0] if tags else ""


def build_values(facts: dict, ptype: str, root: Path | None) -> dict:
    """Every [[placeholder]] the templates use, from evidence only; empty means TODO."""
    repo = facts.get("repo") or {}
    slug = slug_of(facts)
    name = repo_name(facts)
    tl = tooling(facts)
    m = facts.get("manifests") or {}
    requires = (m.get("pyproject.toml") or {}).get("requires_python") or ""
    tag = latest_tag(facts)
    values = {
        "name": name, "slug": slug or "", "tagline": repo.get("description") or "",
        "badges": " ".join(build_badges(facts, verify=False)[0]),
        "install_command": install_command(facts, name),
        "usage_example": usage_example(facts), "license_name": repo.get("license_spdx") or "",
        "demo_path": demo_path(facts), "setup_command": tl["setup"],
        "test_command": tl["test"], "build_command": tl["build"] or tl["setup"],
        "run_command": tl["run"], "default_branch": repo.get("default_branch") or "",
        "commit_style": commit_style(root),
        "support": f"Open an issue at https://github.com/{slug}/issues" if slug and repo.get(
            "has_issues", True) else "",
        "contact": "", "response_time": "",
        "advisory_url": f"https://github.com/{slug}/security/advisories/new" if slug else "",
        "supported_versions": (f"The latest release is `{tag}`. TODO(which versions receive "
                               f"security fixes)") if tag else "",
        "docs_url": repo.get("homepage") or "", "live_url": repo.get("homepage") or "",
        "releases_url": f"https://github.com/{slug}/releases" if slug else "",
        "compatibility": f"Python {requires}" if requires else "",
    }
    values.update(plugin_info(root, name))
    return values


# ---------------------------------------------------------------- badges


def _fetch_svg(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "leadout"})
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - fixed https hosts
        return r.read(200_000).decode("utf-8", "replace")


def _registry_badges(facts: dict) -> list:
    """(label, image, link) for registries the README already installs from."""
    text = (facts.get("readme") or {}).get("text") or ""
    m = facts.get("manifests") or {}
    out = []
    pairs = (("package.json", "npm", r"npm (?:i|install)(?: -g)? ", "npm",
              "https://www.npmjs.com/package/{n}"),
             ("pyproject.toml", "PyPI", r"(?:pip3? install|pipx install|uv tool install) ",
              "pypi", "https://pypi.org/project/{n}/"),
             ("Cargo.toml", "crates.io", r"cargo install ", "crates",
              "https://crates.io/crates/{n}"))
    for manifest, label, cmd, key, link in pairs:
        n = (m.get(manifest) or {}).get("name")
        if n and re.search(cmd + r"[^\n]*\b" + re.escape(str(n)) + r"\b", text):
            out.append((label, f"https://img.shields.io/{key}/v/{n}", link.format(n=n)))
    return out


def build_badges(facts: dict, verify: bool = True, fetch=None):
    """Return ([markdown badge], [warning]) built only from real data (SPEC 10.4)."""
    slug = slug_of(facts)
    warnings: list = []
    if not slug:
        return [], [{"code": "W_NO_SLUG", "message": "No GitHub remote; badges need owner/repo."}]
    repo = facts.get("repo") or {}
    items = []
    if repo.get("license_spdx") and repo["license_spdx"] != "NOASSERTION":
        items.append(("License", f"https://img.shields.io/github/license/{slug}",
                      f"https://github.com/{slug}/blob/HEAD/LICENSE"))
    for wf in (facts.get("ci") or {}).get("workflows_with_runs") or []:
        base = Path(wf).name
        base_url = f"https://github.com/{slug}/actions/workflows/{base}"
        items.append(("CI", base_url + "/badge.svg", base_url))
    if any(not r.get("draft") for r in facts.get("releases") or []):
        items.append(("Release", f"https://img.shields.io/github/v/release/{slug}",
                      f"https://github.com/{slug}/releases"))
    items.append(("Last commit", f"https://img.shields.io/github/last-commit/{slug}",
                  f"https://github.com/{slug}/commits"))
    items += _registry_badges(facts)
    kept = []
    for label, img, link in items:
        if verify and "img.shields.io" in img:
            try:
                svg = (fetch or _fetch_svg)(img).lower()
            except Exception as e:  # noqa: BLE001 - offline or blocked: keep, say so
                warnings.append({"code": "W_BADGE_UNVERIFIED",
                                 "message": f"Could not verify the {label} badge ({e})."})
                svg = ""
            if any(bad in svg for bad in BAD_BADGE_TEXT):
                warnings.append({"code": "W_BADGE_DROPPED",
                                 "message": f"The {label} badge does not render; dropped."})
                continue
        kept.append(f"[![{label}]({img})]({link})")
    return kept, warnings


# ---------------------------------------------------------------- topics


def normalize_topic(raw: str) -> str | None:
    t = str(raw).strip().lower().replace("c++", "cpp").replace("c#", "csharp")
    t = re.sub(r"[\s_.]+", "-", t)
    t = re.sub(r"[^a-z0-9-]", "", t).strip("-")[:50]
    return t if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", t or "") else None


_STOPWORDS = set("""about after also been before being both build built code each every
easy fast from have into just like made make makes more most much need only other over
same simple some such take takes than that their them then there these they this those
tool tools under used uses using very what when where which while will with without your
""".split())


def _min_topics() -> int:
    """meta.topics min_count from the bundled rubric (fix.py never imports audit.py)."""
    rubric = json.loads((Path(__file__).parent / "rubric.json").read_text(encoding="utf-8"))
    check = next((c for c in rubric["checks"] if c["id"] == "meta.topics"), {})
    return int((check.get("params") or {}).get("min_count", 5))


def propose_topics(facts: dict, detection: dict) -> list:
    repo = facts.get("repo") or {}
    ptype = detection["type"]
    cands = [repo.get("primary_language") or detection.get("language") or "",
             "claude-code-plugin" if ptype == "claude-plugin" else ptype]
    cands += detection.get("frameworks") or []
    for manifest in (facts.get("manifests") or {}).values():
        cands += [k for k in manifest.get("keywords") or [] if isinstance(k, str)]
    hay = " ".join([repo.get("description") or "", (facts.get("readme") or {}).get("text") or "",
                    repo.get("primary_language") or "", detection.get("language") or "", ptype,
                    " ".join(detection.get("frameworks") or [])]).lower()
    have = {str(t).lower() for t in repo.get("topics") or []}
    # Few manifests carry keywords: fill up to the audit's minimum with words of the
    # description, most used in the README first. They are pertinent by construction.
    readme = ((facts.get("readme") or {}).get("text") or "").lower()
    freq = {w: readme.count(w) for w in re.findall(r"[a-z][a-z0-9]{3,}",
                                                    (repo.get("description") or "").lower())}
    text_terms = sorted((w for w in freq if w not in _STOPWORDS), key=lambda w: -freq[w])
    out = []

    def add(c):
        t = normalize_topic(c)
        if t and t not in have and t not in out and t != "other" and any(
                len(tok) >= 3 and tok in hay for tok in t.split("-")):
            out.append(t)

    for c in cands:  # manifests, language and type first
        add(c)
    for c in text_terms:  # description words only to reach the minimum
        if len(have) + len(out) >= _min_topics():
            break
        add(c)
    return out[:max(0, 20 - len(have))]


def topics_command(slug: str | None, topics: list) -> str:
    if not topics:
        return ""
    return " ".join(["gh", "repo", "edit", shlex.quote(slug or "OWNER/REPO")]
                    + [x for t in topics for x in ("--add-topic", shlex.quote(t))])


# ---------------------------------------------------------------- release notes and changelog


def semver_tags(path) -> list:
    out = common.git(["tag", "--list", "--sort=-v:refname"], path) or ""
    return [t for t in out.split() if SEMVER.match(t)]


def release_notes(path, frm: str | None, to: str, slug: str | None, title: str = "") -> str:
    if common.git(["rev-parse", "--is-inside-work-tree"], path) is None:
        raise common.RLError("E_NOT_GIT", path=str(path))
    if frm is None:
        tags = semver_tags(path)
        frm = tags[0] if tags and tags[0] != to else None
    rng = f"{frm}..{to}" if frm else to
    log = common.git(["log", "--no-merges", "--format=%s", rng], path)
    if log is None:
        raise common.RLError("E_BAD_ARGS", detail=f"git cannot read the range {rng}")
    groups: dict = {}
    for subject in (s.strip() for s in log.splitlines() if s.strip()):
        m = CONVENTIONAL.match(subject)
        heading = next((h for t, h in NOTE_SECTIONS if m and m.group(1).lower() == t), None)
        text = m.group(4) if heading else subject
        groups.setdefault(heading or "Other changes", []).append(text)
    order = []
    for _t, h in NOTE_SECTIONS:
        if h not in order:
            order.append(h)
    sections = "\n\n".join(f"### {h}\n\n" + "\n".join(f"- {x}" for x in groups[h])
                           for h in order + ["Other changes"] if h in groups)
    compare = f"**Full changelog**: https://github.com/{slug}/compare/{frm}...{to}" if (
        slug and frm) else ""
    tpl = (TEMPLATES / "release-notes.md").read_text(encoding="utf-8")
    text, _ = common.render(tpl, {"title": title, "sections": sections or "No commits found.",
                                  "compare_link": compare})
    return text.replace("TODO(compare_link)", "").rstrip() + "\n"


def changelog_text(facts: dict, root: Path | None) -> str:
    slug = slug_of(facts)
    entries, links = [], []
    rows = [(r["tag"], (r.get("published_at") or "")[:10]) for r in facts.get("releases") or []
            if not r.get("draft") and r.get("tag")]
    if not rows and root is not None:
        for t in facts.get("tags") or []:
            d = (common.git(["log", "-1", "--format=%cs", t], root) or "").strip()
            rows.append((t, d))
    for tag, date in rows:
        entries.append(f"## [{tag}]" + (f" - {date}" if date else "")
                       + f"\n\n- TODO(changes in {tag})")
        if slug:
            links.append(f"[{tag}]: https://github.com/{slug}/releases/tag/{tag}")
    if slug and rows:
        links.insert(0, f"[Unreleased]: https://github.com/{slug}/compare/{rows[0][0]}...HEAD")
    tpl = (TEMPLATES / "changelog.md").read_text(encoding="utf-8")
    text, _ = common.render(tpl, {"entries": "\n\n".join(entries), "links": "\n".join(links)})
    return re.sub(r"\n*TODO\((entries|links)\)\n*", "\n\n", text).rstrip() + "\n"


# ---------------------------------------------------------------- rendering of actions


def _tpl(*parts) -> str:
    return (TEMPLATES.joinpath(*parts)).read_text(encoding="utf-8")


def ci_template(facts: dict, detection: dict):
    """(template name, values) or (None, reason) when the language has no CI template."""
    lang = detection.get("language")
    name = LANG_CI.get(lang or "")
    if not name:
        return None, f"no CI template for language {lang or 'unknown'}"
    m = facts.get("manifests") or {}
    files = facts.get("files") or []
    vals = {"default_branch": (facts.get("repo") or {}).get("default_branch") or ""}
    if name == "ci-python.yml":
        req = (m.get("pyproject.toml") or {}).get("requires_python") or ""
        mm = re.search(r">=\s*3\.(\d+)", req)
        lo = int(mm.group(1)) if mm else PY_LATEST_MINOR - 1
        vals["python_versions"] = json.dumps([f"3.{i}" for i in range(lo, PY_LATEST_MINOR + 1)])
        deps = (m.get("pyproject.toml") or {}).get("dependencies") or []
        tests = any(f.startswith(("tests/", "test/")) or fnmatch.fnmatch(f, "test_*.py")
                    for f in files)
        steps = ["python -m pip install --upgrade pip"]
        if "pyproject.toml" in m or "setup.py" in m:
            steps.append("python -m pip install -e .")
        elif "requirements.txt" in files:
            steps.append("python -m pip install -r requirements.txt")
        if tests and not any("pytest" in d for d in deps):
            steps.append("python -m pip install pytest")
        vals["install_step"] = ("- name: Install\n        run: |\n"
                                + "\n".join(f"          {s}" for s in steps))
        vals["test_command"] = "python -m pytest" if tests else "python -m compileall -q ."
    elif name == "ci-node.yml":
        vals["install_command"] = "npm ci" if "package-lock.json" in files else "npm install"
    elif name == "ci-rust.yml":
        locked = " --locked" if "Cargo.lock" in files else ""
        vals["build_command"], vals["test_command"] = (f"cargo build{locked}",
                                                       f"cargo test{locked}")
    return name, vals


def render_files(action: str, facts: dict, detection: dict, root: Path | None) -> list:
    """[(repo-relative path, text)] for a file action. Pure: no writes."""
    ptype = detection["type"]
    values = build_values(facts, ptype, root)
    if action in ("readme", "badges"):
        path = (facts.get("readme") or {}).get("path") or "README.md"
        return [(path, common.render(_tpl("readme", f"{ptype}.md"), values)[0])]
    simple = {"contributing": ("CONTRIBUTING.md", "CONTRIBUTING.md"),
              "code-of-conduct": ("CODE_OF_CONDUCT.md", "CODE_OF_CONDUCT.md"),
              "security": ("SECURITY.md", "SECURITY.md"),
              "pr-template": (".github/pull_request_template.md", "PULL_REQUEST_TEMPLATE.md")}
    if action in simple:
        dest, src = simple[action]
        return [(dest, common.render(_tpl("community", src), values)[0])]
    if action == "issue-templates":
        return [(f".github/ISSUE_TEMPLATE/{n}", _tpl("community", "ISSUE_TEMPLATE", n))
                for n in ("bug_report.yml", "config.yml", "feature_request.yml")]
    if action == "ci":
        name, vals = ci_template(facts, detection)
        if not name:
            raise common.RLError("E_BAD_ARGS", detail=f"ci is not offered: {vals}")
        return [(".github/workflows/ci.yml", common.render(_tpl("workflows", name), vals)[0])]
    if action == "changelog":
        return [("CHANGELOG.md", changelog_text(facts, root))]
    raise common.RLError("E_BAD_ARGS", detail=f"{action} is not a file action; choose one of: "
                                              + ", ".join(FILE_ACTIONS))


def todos_in(text: str) -> list:
    seen: list = []
    for t in re.findall(r"TODO\(([^)]*)\)", text):
        if t not in seen:
            seen.append(t)
    return seen


# ---------------------------------------------------------------- plan


def load_audit(args, target_arg: str) -> dict:
    if args.audit:
        if args.audit == "-":
            try:
                data = json.load(sys.stdin)
            except ValueError:
                raise common.RLError("E_BAD_ARGS", detail="stdin is not audit JSON") from None
        else:
            data = common.read_json(args.audit)
    else:
        cmd = [sys.executable, str(common.PLUGIN_ROOT / "scripts" / "audit.py"), target_arg,
               "--json"] + (["--offline"] if args.offline else []) + (
            ["--type", args.type] if args.type else [])
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            raise common.RLError("E_NETWORK", detail="the audit took longer than 180 s") from None
        try:
            data = json.loads(p.stdout)
        except ValueError:
            raise common.RLError("E_GH_FAILED", detail="audit.py printed no JSON: "
                                 + (p.stderr.strip().splitlines() or ["no output"])[-1]) from None
        if isinstance(data, dict) and "error" in data:
            e = data["error"]
            raise common.RLError(e.get("code", "E_GH_FAILED"), e.get("message"), e.get("hint"))
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(
            data.get("checks"), list):
        raise common.RLError("E_BAD_ARGS", detail="the audit JSON must have schema 1 and a "
                                                  "checks list (SPEC 9.6)")
    return data


def _triggers(audit: dict, patterns: list) -> list:
    bad = []
    for c in audit["checks"]:
        if c.get("status") not in ("fail", "partial"):
            continue
        cid = c.get("id", "")
        for p in patterns:
            hit = (cid.startswith("readme.") and cid != "readme.contributing"
                   and c.get("phase") == "fix") if p == "readme.*" else cid == p
            if hit:
                bad.append(cid)
    return sorted(set(bad))


def build_plan(audit: dict, facts: dict, detection: dict, root: Path | None) -> list:
    files = set(facts.get("files") or [])
    readme_path = (facts.get("readme") or {}).get("path")
    plan = []
    for action, (kind, patterns) in ACTIONS.items():
        ids = _triggers(audit, patterns)
        paths: list = []
        reason = ""
        if action == "badges":
            if not readme_path or has_badges((facts.get("readme") or {}).get("text") or ""):
                continue
            ids = ["readme.*"]
        elif not ids:
            continue
        if action in ("readme", "badges"):
            paths = [readme_path or "README.md"]
        elif action == "ci":
            name, info = ci_template(facts, detection)
            if name:
                paths = [".github/workflows/ci.yml"]
            else:
                kind, reason = "manual", f"Not offered: {info}."
        elif kind == "file":
            paths = [p for p, _ in render_files(action, facts, detection, root)]
        todos = []
        if kind == "file":
            todos = todos_in("\n".join(t for _, t in render_files(action, facts, detection,
                                                                  root)))
        plan.append({"id": action, "check_ids": ids, "kind": kind, "paths": paths,
                     "exists": any(p in files for p in paths),
                     "summary": reason or SUMMARIES[action], "todos": todos})
    return plan


def plan_markdown(audit: dict, plan: list) -> str:
    target = audit.get("target")
    label = target.get("slug") or target.get("path") if isinstance(target, dict) else target
    head = (f"# Fix plan: {label} (score {audit.get('score', '?')}/100, "
            f"type: {audit.get('project_type', '?')})")
    if not plan:
        return head + "\n\nNothing to fix."
    rows = ["| # | Action | Kind | Files | Exists | TODOs |", "|---|---|---|---|---|---|"]
    for i, a in enumerate(plan, 1):
        rows.append(f"| {i} | `{a['id']}` | {a['kind']} | {', '.join(a['paths']) or '-'} | "
                    f"{'yes' if a['exists'] else 'no'} | {len(a['todos'])} |")
    return "\n".join([head, "", *rows, "", *[f"- `{a['id']}`: {a['summary']}" for a in plan]])


# ---------------------------------------------------------------- commands


def _context(args, need_local: bool = False):
    target = common.resolve_target(args.target)
    if need_local and target["kind"] != "local":
        raise common.RLError("E_NOT_GIT", path=args.target,
                             hint="Clone the repository first, then run the command inside it.")
    facts = facts_mod.collect_facts(args.target, offline=args.offline)
    detection = detect_mod.detect(facts)
    if args.type:
        if args.type not in detect_mod.TYPES:
            raise common.RLError("E_BAD_ARGS", detail=f"--type must be one of "
                                                      f"{', '.join(detect_mod.TYPES)}")
        detection = dict(detection, type=args.type)
    root = Path(target["path"]) if target["path"] else None
    return target, facts, detection, root


def cmd_plan(args) -> int:
    _t, facts, detection, root = _context(args)
    audit = load_audit(args, args.target)
    plan = build_plan(audit, facts, detection, root)
    if args.json:
        common.out(common.dumps({"schema": 1, "project_type": detection["type"],
                                 "score": audit.get("score"), "actions": plan}))
    else:
        common.out(plan_markdown(audit, plan))
    return 0


def cmd_render(args) -> int:
    _t, facts, detection, root = _context(args)
    files = render_files(args.action, facts, detection, root)
    for path, text in files:
        if len(files) > 1:
            common.out(f"==> {path} <==")
        sys.stdout.write(common.redact(text if text.endswith("\n") else text + "\n"))
    todos = todos_in("\n".join(t for _, t in files))
    if todos:
        common.err("todos: " + ", ".join(todos))
    return 0


def cmd_write(args) -> int:
    _t, facts, detection, root = _context(args, need_local=True)
    if common.git(["rev-parse", "--is-inside-work-tree"], root) is None:
        raise common.RLError("E_NOT_GIT", path=str(root))
    if common.is_default_branch(root):
        cur = (common.git(["symbolic-ref", "--quiet", "--short", "HEAD"], root) or "").strip()
        raise common.RLError("E_DEFAULT_BRANCH", branch=cur or "HEAD (detached)",
                             date=dt.date.today().strftime("%Y%m%d"))
    files = render_files(args.action, facts, detection, root)
    if args.content_file:
        if len(files) != 1:
            raise common.RLError("E_BAD_ARGS", detail="--content-file works with one-file "
                                                      "actions only")
        files = [(files[0][0], Path(args.content_file).read_text(encoding="utf-8"))]
    elif args.action == "badges":
        raise common.RLError("E_BAD_ARGS", detail="badges edits the existing README: pass "
                             "--content-file with the reviewed README")
    elif args.action == "readme" and (root / files[0][0]).exists():
        raise common.RLError("E_BAD_ARGS", detail="the README exists: pass --content-file with "
                             "the reviewed, merged README (the raw template would drop content)")
    for rel, _t in files:
        if (root / rel).exists() and not args.overwrite:
            raise common.RLError("E_FILE_EXISTS", path=rel)
    for rel, text in files:
        common.write_new_file(root / rel, text, overwrite=args.overwrite)
        common.out(f"wrote {rel}")
    todos = todos_in("\n".join(t for _, t in files))
    common.out("todos: " + (", ".join(todos) if todos else "none"))
    return 0


def cmd_badges(args) -> int:
    _t, facts, _d, _r = _context(args)
    badges, warns = build_badges(facts, verify=not args.no_verify)
    if args.no_verify:
        warns.append({"code": "W_BADGE_UNVERIFIED",
                      "message": "Badges were not verified (--no-verify)."})
    for w in warns:
        common.err(f"leadout: warning: {w['message']}")
    if args.json:
        common.out(common.dumps({"badges": badges, "warnings": warns}))
    else:
        common.out(" ".join(badges))
    return 0


def cmd_topics(args) -> int:
    _t, facts, detection, _r = _context(args)
    topics = propose_topics(facts, detection)
    cmd = topics_command(slug_of(facts), topics)
    if args.json:
        common.out(common.dumps({"topics": topics, "command": cmd}))
    else:
        common.out("topics: " + (", ".join(topics) or "none to add"))
        if cmd:
            common.out("command (not run): " + cmd)
    return 0


def cmd_release_notes(args) -> int:
    _t, facts, _d, root = _context(args, need_local=True)
    common.out(release_notes(root, args.frm, args.to, slug_of(facts), args.title or ""))
    return 0


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="fix.py", description="Plan and apply leadout fixes.",
        epilog="Example: python3 scripts/fix.py plan . --json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common_args(p, action: bool = False):
        if action:
            p.add_argument("action", choices=FILE_ACTIONS, help="file action id")
        p.add_argument("target", nargs="?", default=".", help="path, GitHub URL or owner/repo")
        p.add_argument("--type", help="override the detected project type")
        p.add_argument("--offline", action="store_true", help="no gh calls")
        return p

    p = common_args(sub.add_parser("plan", help="list fix actions"))
    p.add_argument("--audit", help="audit --json file (or - for stdin); default: run the audit")
    p.add_argument("--json", action="store_true")
    common_args(sub.add_parser("render", help="print generated content"), action=True)
    p = common_args(sub.add_parser("write", help="write a file action"), action=True)
    p.add_argument("--overwrite", action="store_true", help="replace existing files")
    p.add_argument("--content-file", help="write this reviewed content instead of the render")
    p = common_args(sub.add_parser("badges", help="verified badge line"))
    p.add_argument("--no-verify", action="store_true", help="skip the shields.io check")
    p.add_argument("--json", action="store_true")
    p = common_args(sub.add_parser("topics", help="proposed topics"))
    p.add_argument("--json", action="store_true")
    p = common_args(sub.add_parser("release-notes", help="notes from git log"))
    p.add_argument("--from", dest="frm", help="start tag (default: latest semver tag)")
    p.add_argument("--to", default="HEAD", help="end ref (default: HEAD)")
    p.add_argument("--title", help="release title, e.g. v1.2.0")
    args = ap.parse_args(argv)
    return {"plan": cmd_plan, "render": cmd_render, "write": cmd_write, "badges": cmd_badges,
            "topics": cmd_topics, "release-notes": cmd_release_notes}[args.cmd](args)


def main(argv: list[str] | None = None) -> int:
    return common.run_main(_main, argv)


if __name__ == "__main__":
    sys.exit(main())
