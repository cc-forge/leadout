"""Audit a repository's launch readiness: rubric checks, a 0-100 score, ranked fixes.

Read-only: it never writes inside the target repository.

Usage:
    python3 scripts/audit.py [TARGET] [--json] [--offline] [--rubric FILE]
                             [--type TYPE] [--as-of YYYY-MM-DD]
                             [--facts FILE] [--save-facts FILE]

Examples:
    python3 scripts/audit.py sharkdp/hyperfine
    python3 scripts/audit.py . --offline --json
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import common
import detect as detect_mod
import facts as facts_mod

D = Decimal
PHASES = ("fix", "visuals", "launch", "manual")
STATUS_ICON = {"pass": "✅", "partial": "⚠️", "fail": "❌", "skip": "➖", "unknown": "❔"}

# ---------------------------------------------------------------- markdown helpers

FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
ATX = re.compile(r"^\s{0,3}(#{1,6})(?:\s+(.*?))?\s*#*\s*$")
HTML_H = re.compile(r"^\s*<h([1-6])\b[^>]*>(.*?)(?:</h\1>|$)", re.I)
SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*\|")
UNCLOSED_TAG = re.compile(r"<[A-Za-z/!][^>]*$")


def _prep(text: str) -> list[str]:
    """Lines with HTML comments and front matter blanked (line numbers preserved)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                lines[:i + 1] = [""] * (i + 1)
                break
    return lines


def _code_mask(lines: list[str]) -> list[bool]:
    """True for fence lines and the lines inside fenced code blocks."""
    # ponytail: fenced blocks only; indented code blocks count as prose
    mask, fence = [], None
    for line in lines:
        m = FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)[0]
            mask.append(fence is not None)
            continue
        mask.append(True)
        if m and m.group(1)[0] == fence:
            fence = None
    return mask


def _plain(s: str) -> str:
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", s)
    s = re.sub(r"<[^>]*>", "", s)
    s = re.sub(r"\*\*|__|\*|`|~~", "", s)
    s = re.sub(r"^\s*>\s?", "", html.unescape(s))
    return " ".join(s.split())


def _decorative(line: str) -> bool:
    """A line made only of images, badges, links and HTML wrappers."""
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
    s = re.sub(r"\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[[^\]]*\]\[[^\]]*\]", "", s)
    s = re.sub(r"<[^>]*>", "", s)
    s = UNCLOSED_TAG.sub("", s)
    s = re.sub(r"\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]", "", html.unescape(s), flags=re.I)
    return re.sub(r"[\s|·•\-–—:,./>*_#]+", "", s) == ""


def _is_heading(lines, mask, i) -> bool:
    line = lines[i]
    if ATX.match(line) or HTML_H.match(line):
        return True
    nxt = i + 1
    return (nxt < len(lines) and not mask[nxt] and bool(SETEXT.match(lines[nxt]))
            and bool(line.strip()) and not line.lstrip().startswith(("|", "-", "*", ">")))


def _headings(lines, mask) -> list[tuple]:
    """(index, level, text) for ATX, single-line <hN> and setext headings outside code."""
    out = []
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        m = ATX.match(line)
        if m:
            out.append((i, len(m.group(1)), _plain(m.group(2) or "")))
            continue
        m = HTML_H.match(line)
        if m:
            out.append((i, int(m.group(1)), _plain(m.group(2))))
            continue
        if _is_heading(lines, mask, i):
            out.append((i, 1 if lines[i + 1].strip().startswith("=") else 2, _plain(line)))
    return out


def _sections(lines, mask) -> list[tuple]:
    """(title, body_lines, body_mask); a body runs to the next heading of equal or higher level."""
    heads = _headings(lines, mask)
    out = []
    for n, (i, level, title) in enumerate(heads):
        end = next((j for j, lv, _ in heads[n + 1:] if lv <= level), len(lines))
        out.append((title, lines[i + 1:end], mask[i + 1:end]))
    return out


def _first_block(body, mask) -> list[str] | None:
    block, started = [], False
    for line, m in zip(body, mask):
        if not started:
            if m:
                started = True
            continue
        if not m or FENCE.match(line):
            return block
        block.append(line)
    return block if started else None


def _unknown(facts: dict, section: str):
    return None, (facts.get("unavailable") or {}).get(section, "not collected")


def _repo_name(facts: dict) -> str:
    t = facts.get("target") or {}
    if t.get("slug"):
        return t["slug"].split("/", 1)[1]
    return Path(t.get("path") or ".").name


def _q(text: str, n: int = 50) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def needs_readme(fn):
    """Wrap a README evaluator: no README -> 0, README unavailable -> unknown."""
    def wrapped(facts, ptype, params):
        r = facts.get("readme")
        if not r:
            reason = (facts.get("unavailable") or {}).get("readme")
            return (None, reason) if reason else (0.0, "No README.")
        lines = _prep(r.get("text") or "")
        return fn(lines, _code_mask(lines), facts, ptype, params)
    wrapped.__name__ = fn.__name__
    return wrapped


# ---------------------------------------------------------------- README evaluators


def readme_exists(facts, ptype, p):
    r = facts.get("readme")
    if r:
        return 1.0, f"{r.get('path', 'README')} found."
    reason = (facts.get("unavailable") or {}).get("readme")
    return (None, reason) if reason else (0.0, "No README at the root, .github/ or docs/.")


@needs_readme
def readme_tagline(lines, mask, facts, ptype, p):
    count, para, in_tag = 0, [], False
    for i, line in enumerate(lines):
        if not line.strip():
            if para:
                break
            continue
        count += 1
        if not para and count > p["max_lines"]:
            break
        if in_tag or mask[i]:
            if in_tag and ">" in line:
                in_tag = False
            if para:
                break
            continue
        if (_is_heading(lines, mask, i) or SETEXT.match(line)
                or line.lstrip().startswith("|") or _decorative(line)):
            in_tag = bool(UNCLOSED_TAG.search(line))
            if para:
                break
            continue
        para.append(line)
    text = _plain(" ".join(para))
    if not text:
        return 0.0, f"No prose paragraph in the first {p['max_lines']} lines."
    if text.lower().startswith(("todo", "lorem", "this project was bootstrapped with")):
        return 0.0, f'First paragraph is placeholder text ("{_q(text, 40)}").'
    n = len(text)
    if p["min_chars"] <= n <= p["max_chars"]:
        return 1.0, f"First paragraph is {n} characters."
    if n > p["max_chars"]:
        return 0.5, f"First paragraph is {n} characters (max {p['max_chars']})."
    return 0.5, f"First paragraph is {n} characters (min {p['min_chars']})."


BADGE_HOSTS = ("img.shields.io", "badgen.net", "badge.fury.io", "codecov.io", "coveralls.io",
               "deepwiki.com", "app.codacy.com", "api.securityscorecards.dev",
               "goreportcard.com", "pkg.go.dev", "docs.rs", "crates.io", "api.netlify.com",
               "snyk.io")
MEDIA_RES = (
    re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)"),
    re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']?([^\"'\s>]+)", re.I),
    re.compile(r"<(?:video|source)\b[^>]*?\bsrc(?:set)?\s*=\s*[\"']?([^\"'\s>]+)", re.I),
)
URL_RE = re.compile(r"https?://[^\s)\"'<>\]]+")
VIDEO_HOST = re.compile(r"(?:youtube\.com/|youtu\.be/|asciinema\.org/)", re.I)
ATTACHMENT = re.compile(r"github\.com/user-attachments/assets/|user-images\.githubusercontent"
                        r"\.com/|private-user-images\.githubusercontent\.com/", re.I)


def _media_kind(url: str) -> str | None:
    """"rich", "svg", or None for a badge."""
    pu = urlparse(url)
    host = pu.netloc.lower().split(":")[0]
    path = pu.path.lower()
    if any(host == h or host.endswith("." + h) for h in BADGE_HOSTS) or "badge" in path:
        return None
    if VIDEO_HOST.search(url):
        return "rich"
    last = path.rsplit("/", 1)[-1]
    # ponytail: no extension (e.g. GitHub user-attachments) counts as an image or video
    return "svg" if last.endswith(".svg") else "rich"


@needs_readme
def readme_visual(lines, mask, facts, ptype, p):
    rich = svg = after = None
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        urls = [u for rx in MEDIA_RES for u in rx.findall(line)]
        for u in URL_RE.findall(line):
            if VIDEO_HOST.search(u) or (line.strip() == u and ATTACHMENT.search(u)):
                urls.append(u)
        for u in urls:
            kind = _media_kind(u)
            if kind is None:
                continue
            name = _q(urlparse(u).path.rsplit("/", 1)[-1] or u, 60)
            if i + 1 > p["max_lines"]:
                after = after or (i + 1, name)
            elif kind == "rich":
                rich = rich or (i + 1, name)
            else:
                svg = svg or (i + 1, name)
    if rich:
        return 1.0, f"Media in the first {p['max_lines']} lines: {rich[1]} (line {rich[0]})."
    if svg:
        return 0.5, (f"Only SVG images in the first {p['max_lines']} lines "
                     f"(often a logo): {svg[1]}.")
    if after:
        return 0.5, f"Media only after line {p['max_lines']} (line {after[0]})."
    return 0.0, "No image or video in the README (badges excluded)."


INSTALL_H = re.compile(r"install|installation|getting started|quick ?start|setup|download", re.I)
USAGE_H = re.compile(r"usage|example|examples|quick ?start|getting started|tutorial|how to use"
                     r"|demo|commands", re.I)
PLUGIN_INSTALL = re.compile(r"(?:^|[\s`$>])(?:/plugin install|claude plugin install)\b")


def _install_score(title, body, mask, ptype):
    text = "\n".join(body)
    if ptype == "claude-plugin" and PLUGIN_INSTALL.search(text):
        return 1.0, f'"{_q(title)}" has a plugin install command.'
    if ptype == "desktop-app" and re.search(r"/releases\b", text):
        return 1.0, f'"{_q(title)}" links to a release download.'
    block = _first_block(body, mask)
    if block is None:
        return 0.25, f'"{_q(title)}" has no code block.'
    cmds = []
    for line in block:
        s = line.strip()
        if s and not s.startswith("#"):
            cmds.append(re.sub(r"^[$>]\s+", "", s))
    n = len(cmds)
    if n == 1:
        return 1.0, f'"{_q(title)}": the first code block is 1 command.'
    if 2 <= n <= 3:
        return 0.5, f'"{_q(title)}": the first code block has {n} command lines.'
    return 0.25, f'"{_q(title)}": the first code block has {n} lines (want 1).'


@needs_readme
def readme_install(lines, mask, facts, ptype, p):
    best = None
    for title, body, bmask in _sections(lines, mask):
        if INSTALL_H.search(title):
            res = _install_score(title, body, bmask, ptype)
            if best is None or res[0] > best[0]:
                best = res
    return best or (0.0, "No install section (Installation, Getting started, Quick start...).")


@needs_readme
def readme_usage(lines, mask, facts, ptype, p):
    first = None
    for title, body, bmask in _sections(lines, mask):
        if not USAGE_H.search(title):
            continue
        first = first or title
        prose = [ln for ln, m in zip(body, bmask) if not m]
        if any(bmask) or any(TABLE_SEP.match(ln) for ln in prose) or any(
                re.search(r"!\[|<img\b", ln, re.I) for ln in prose):
            return 1.0, f'"{_q(title)}" has a code block, table or image.'
    if first:
        return 0.5, f'"{_q(first)}" has no code block, table or image.'
    return 0.0, "No usage or example section."


@needs_readme
def readme_contributing(lines, mask, facts, ptype, p):
    for _, _, title in _headings(lines, mask):
        if re.search(r"contribut", title, re.I):
            return 1.0, f'Section "{_q(title)}".'
    text = "\n".join(lines)
    if re.search(r"\]\([^)]*contributing[^)]*\)|href\s*=\s*[\"'][^\"']*contributing", text,
                 re.I):
        return 1.0, "Links to CONTRIBUTING."
    return 0.0, "No contributing section or link."


@needs_readme
def readme_length(lines, mask, facts, ptype, p):
    s = "\n".join(ln for ln, m in zip(lines, mask) if not m)
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\]\([^)]*\)", "]", s)
    s = re.sub(r"<[^>]+>", " ", s)
    n = len(re.findall(r"[^\W_]+(?:['’.-][^\W_]+)*", s))
    lo, hi = p["min_words"], p["max_words"]
    if lo <= n <= hi:
        return 1.0, f"{n} words."
    if lo / 2 <= n < lo:
        return 0.5, f"{n} words (min {lo})."
    if hi < n <= hi * 2:
        return 0.5, f"{n} words (max {hi})."
    return 0.0, f"{n} words (want {lo}-{hi})."


# ---------------------------------------------------------------- metadata evaluators


def meta_description(facts, ptype, p):
    repo = facts.get("repo")
    if repo is None:
        return _unknown(facts, "repo")
    d = (repo.get("description") or "").strip()
    if not d:
        return 0.0, "No repository description."
    if d.lower() == _repo_name(facts).lower():
        return 0.5, "The description only repeats the repository name."
    if len(d) < 10:
        return 0.5, f"Description is {len(d)} characters (min 10)."
    if len(d) > 350:
        return 0.5, f"Description is {len(d)} characters (max 350)."
    return 1.0, f"Description is {len(d)} characters."


def meta_topics(facts, ptype, p):
    repo = facts.get("repo")
    if repo is None:
        return _unknown(facts, "repo")
    topics = list(repo.get("topics") or [])
    if not topics:
        return 0.0, "No topics."
    det = detect_mod.detect(facts)
    hay = " ".join([repo.get("description") or "", (facts.get("readme") or {}).get("text", ""),
                    repo.get("primary_language") or "", ptype, *det["frameworks"]]).lower()
    pert = [t for t in topics if any(len(tok) >= 3 and tok in hay
                                     for tok in t.lower().split("-"))]
    bad = [t for t in topics if t not in pert]
    n, mc = len(topics), p["min_count"]
    s = common.round_half_up(D(min(n, mc)) / D(mc) * D(len(pert)) / D(n), 2)
    ev = f"{n} topics (want {mc}), {len(pert)} pertinent"
    return s, ev + (f"; not pertinent: {', '.join(bad)}." if bad else ".")


def meta_homepage(facts, ptype, p):
    repo = facts.get("repo")
    if repo is None:
        return _unknown(facts, "repo")
    hp = (repo.get("homepage") or "").strip()
    if re.match(r"https?://\S+$", hp):
        return 1.0, f"Homepage: {_q(hp, 60)}."
    return 0.0, "No homepage URL."


def meta_social_preview(facts, ptype, p):
    repo = facts.get("repo")
    if repo is None:
        return _unknown(facts, "repo")
    v = repo.get("uses_custom_og_image")
    if v is None:
        return None, "not reported by GitHub"
    if v:
        return 1.0, "A custom social preview is set."
    return 0.0, "GitHub shows the default social preview."


LICENSE_RE = re.compile(r"^(?:LICEN[CS]E|COPYING)(?:[.-][^/]*)?$", re.I)


def meta_license(facts, ptype, p):
    lic = next((f for f in facts.get("files") or [] if LICENSE_RE.match(f)), None)
    repo = facts.get("repo")
    if repo is not None:
        spdx = repo.get("license_spdx")
        if spdx and spdx != "NOASSERTION":
            return 1.0, f"License: {spdx}."
        if lic:
            return 0.5, f"{lic} exists but GitHub does not recognise the license."
        return 0.0, "No license."
    if lic:
        reason = (facts.get("unavailable") or {}).get("repo", "not collected")
        return 0.5, f"{lic} exists; the SPDX id is unknown ({reason})."
    return 0.0, "No license file."


# ---------------------------------------------------------------- distribution evaluators

REGISTRY = re.compile(r"\b(pip3? install|pipx install|uv tool install|uvx|npm (?:i|install)|npx"
                      r"|pnpm add|yarn add|cargo install|go install|brew install|gem install"
                      r"|composer require|docker (?:pull|run)|winget install|scoop install)\b"
                      r"(.*)", re.I)
SEMVER = re.compile(r"^v?\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
CHANGELOG_RE = re.compile(r"^(?:docs/)?(?:CHANGELOG|CHANGES|HISTORY|NEWS|RELEASES)(?:[.-][^/]*)?$",
                          re.I)


def _package_names(facts: dict) -> list[str]:
    names = {_repo_name(facts)}
    for m in (facts.get("manifests") or {}).values():
        for key in ("name", "module"):
            if isinstance(m.get(key), str) and m[key]:
                names.add(m[key])
    return sorted(n for n in names if n)


def _registry_install(facts: dict) -> str | None:
    text = (facts.get("readme") or {}).get("text") or ""
    names = _package_names(facts)
    for line in text.splitlines():
        m = REGISTRY.search(line)
        if not m:
            continue
        rest = re.sub(r"(?:git\+)?[a-z]+://\S+", " ", m.group(2))  # URLs are not registries
        for n in names:
            if re.search(rf"(?<![\w.-]){re.escape(n)}(?![\w-])", rest, re.I):
                return _q(line.strip().strip("`$> "), 60)
    return None


def _latest_release(facts: dict):
    return next((r for r in facts["releases"] if not r.get("draft")), None)


def dist_release(facts, ptype, p):
    if ptype == "claude-plugin" and ".claude-plugin/marketplace.json" in (facts.get("files")
                                                                          or []):
        return 1.0, "Installable from its plugin marketplace (.claude-plugin/marketplace.json)."
    cmd = _registry_install(facts)
    if cmd:
        return 1.0, f"The README installs from a registry: {cmd}."
    if facts.get("releases") is None:
        return _unknown(facts, "releases")
    latest = _latest_release(facts)
    if latest is None:
        return 0.0, "No release and no registry install command in the README."
    if latest.get("assets"):
        return 1.0, f"Latest release {latest['tag']} has {len(latest['assets'])} asset(s)."
    return 0.5, f"Latest release {latest['tag']} has no downloadable assets."


def dist_semver(facts, ptype, p):
    rels, tags = facts.get("releases"), facts.get("tags")
    if rels is None and tags is None:
        return _unknown(facts, "releases")
    rel_tags = [r["tag"] for r in rels or [] if not r.get("draft") and r.get("tag")]
    all_tags = rel_tags + list(tags or [])
    if not all_tags:
        return 0.0, "No releases or tags."
    latest = rel_tags[0] if rel_tags else all_tags[0]
    kind = "release" if rel_tags else "tag"
    if SEMVER.match(latest):
        return 1.0, f"Latest {kind} {latest} is semver."
    if any(SEMVER.match(t) for t in all_tags):
        return 0.5, f"Latest {kind} {latest} is not semver."
    return 0.0, f"No semver tag ({latest})."


def dist_changelog(facts, ptype, p):
    hit = next((f for f in facts.get("files") or [] if CHANGELOG_RE.match(f)), None)
    if hit:
        return 1.0, f"{hit} found."
    if facts.get("releases") is None:
        return _unknown(facts, "releases")
    latest = _latest_release(facts)
    if latest is None:
        return 0.0, "No changelog file and no releases."
    if latest.get("body_chars", 0) >= 50:
        return 1.0, f"Release {latest['tag']} has notes ({latest['body_chars']} characters)."
    return 0.5, f"Release {latest['tag']} notes are {latest.get('body_chars', 0)} characters."


# ---------------------------------------------------------------- community evaluators

COMM_FILES = {
    "contributing": (re.compile(r"^(?:\.github/|docs/)?CONTRIBUTING(?:\.(?:md|rst|txt))?$",
                                re.I), "CONTRIBUTING"),
    "code_of_conduct": (re.compile(r"^(?:\.github/|docs/)?CODE_OF_CONDUCT(?:\.(?:md|rst|txt))?$",
                                   re.I), "CODE_OF_CONDUCT"),
    "issue_template": (re.compile(r"^\.github/ISSUE_TEMPLATE/(?!config\.ya?ml$)[^/]+\.(?:md|ya?ml)$"
                                  r"|^(?:\.github/|docs/)?ISSUE_TEMPLATE\.md$", re.I),
                       "issue templates"),
    "pull_request_template": (re.compile(r"^(?:\.github/|docs/)?pull_request_template\.md$"
                                         r"|^(?:\.github/|docs/)?PULL_REQUEST_TEMPLATE/", re.I),
                              "pull request template"),
    "security": (re.compile(r"^(?:\.github/|docs/)?SECURITY\.md$", re.I), "SECURITY.md"),
}


def comm_file(facts, ptype, p):
    rx, label = COMM_FILES[p["file"]]
    hit = next((f for f in facts.get("files") or [] if rx.match(f)), None)
    if hit:
        return 1.0, f"{hit} found."
    if (facts.get("community") or {}).get(p["file"]):
        return 1.0, f"The GitHub community profile reports {label} (organization default)."
    return 0.0, f"No {label} found."


def comm_gfi(facts, ptype, p):
    repo = facts.get("repo")
    if repo is None:
        return _unknown(facts, "repo")
    if not repo.get("has_issues"):
        return "skip", "issues are disabled"
    issues = facts.get("issues")
    if issues is None:
        return _unknown(facts, "issues")
    labels = issues.get("gfi_labels") or []
    if not labels:
        return 0.0, "No good-first-issue label."
    if issues.get("gfi_open", 0) >= 1:
        return 1.0, f'{issues["gfi_open"]} open issue(s) labelled "{labels[0]}".'
    return 0.5, f'Label "{labels[0]}" exists but no open issue has it.'


# ---------------------------------------------------------------- health evaluators

WORKFLOW_RE = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
FAILED = ("failure", "timed_out", "startup_failure")


def health_recent_commit(facts, ptype, p):
    repo = facts.get("repo")
    if repo and repo.get("archived"):
        return 0.0, "The repository is archived."
    last = facts.get("last_commit_at")
    if not last:
        un = facts.get("unavailable") or {}
        return None, un.get("last_commit_at") or un.get("repo") or "no commits"
    d = common.days_between(last, facts["as_of"])
    fresh, stale = p["fresh_days"], p["stale_days"]
    if d <= fresh:
        return 1.0, f"Last commit {d} days ago ({last[:10]})."
    if d >= stale:
        return 0.0, f"Last commit {d} days ago ({last[:10]}, stale after {stale})."
    s = common.round_half_up(D(stale - d) / D(stale - fresh), 2)
    return s, f"Last commit {d} days ago ({last[:10]})."


def health_ci(facts, ptype, p):
    files = facts.get("files") or []
    wf = [f for f in files if WORKFLOW_RE.match(f)]
    other = [f for f in files if f in facts_mod.OTHER_CI or f.startswith(".buildkite/")]
    if not wf and not other:
        return 0.0, "No CI configuration."
    if not wf:
        return 0.5, f"CI configured outside GitHub Actions ({other[0]}); result unknown."
    run = (facts.get("ci") or {}).get("latest_default_branch_run")
    if run is None:
        reason = (facts.get("unavailable") or {}).get("repo") or "no completed run"
        return 0.5, f"{len(wf)} workflow file(s); latest result unknown ({reason})."
    concl, when = run.get("conclusion"), (run.get("created_at") or "")[:10]
    if concl == "success":
        return 1.0, f"The latest default-branch run succeeded ({when})."
    if concl in FAILED:
        return 0.25, f"The latest default-branch run ended with {concl} ({when})."
    return 0.5, f"The latest default-branch run ended with {concl} ({when})."


def health_issue_response(facts, ptype, p):
    repo = facts.get("repo")
    if repo is None:
        return _unknown(facts, "repo")
    if not repo.get("has_issues"):
        return "skip", "issues are disabled"
    issues = facts.get("issues")
    if issues is None:
        return _unknown(facts, "issues")
    sample = issues.get("open_sample") or []
    if not sample:
        return 1.0, "No open issues."
    late = [i for i in sample if i.get("comments", 0) == 0 and i.get("created_at")
            and common.days_between(i["created_at"], facts["as_of"]) > p["response_days"]]
    ratio = D(len(late)) / D(len(sample))
    ev = (f"{len(late)} of {len(sample)} open issues have no reply after "
          f"{p['response_days']} days.")
    if ratio <= D(str(p["tolerance"])):
        return 1.0, ev
    return common.round_half_up(1 - ratio, 2), ev


# ---------------------------------------------------------------- rubric

EVALUATORS = {
    "readme_exists": (readme_exists, {}),
    "readme_tagline": (readme_tagline, {"max_lines": 15, "min_chars": 20, "max_chars": 300}),
    "readme_visual": (readme_visual, {"max_lines": 50}),
    "readme_install": (readme_install, {}),
    "readme_usage": (readme_usage, {}),
    "readme_contributing": (readme_contributing, {}),
    "readme_length": (readme_length, {"min_words": 300, "max_words": 5000}),
    "meta_description": (meta_description, {}),
    "meta_topics": (meta_topics, {"min_count": 5}),
    "meta_homepage": (meta_homepage, {}),
    "meta_social_preview": (meta_social_preview, {}),
    "meta_license": (meta_license, {}),
    "dist_release": (dist_release, {}),
    "dist_semver": (dist_semver, {}),
    "dist_changelog": (dist_changelog, {}),
    "comm_file": (comm_file, {"file": "contributing"}),
    "comm_gfi": (comm_gfi, {}),
    "health_recent_commit": (health_recent_commit, {"fresh_days": 90, "stale_days": 365}),
    "health_ci": (health_ci, {}),
    "health_issue_response": (health_issue_response, {"response_days": 7, "tolerance": 0.1}),
}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_check(c, cat_ids, levels, seen, bad) -> None:
    if not isinstance(c, dict):
        bad("every check must be an object")
    cid = c.get("id")
    if not isinstance(cid, str) or not re.fullmatch(r"[a-z]+\.[a-z0-9_]+", cid):
        bad(f"check id {cid!r} must look like category.name")
    if cid in seen:
        bad(f"duplicate check id {cid}")
    seen.add(cid)
    if c.get("category") not in cat_ids:
        bad(f"{cid}: unknown category {c.get('category')!r}")
    if c.get("type") not in EVALUATORS:
        bad(f"{cid}: unknown type {c.get('type')!r}")
    if not _num(c.get("weight")) or c["weight"] <= 0:
        bad(f"{cid}: weight must be a number > 0")
    if c.get("effort") not in levels:
        bad(f"{cid}: effort must be one of {', '.join(sorted(levels))}")
    if c.get("phase") not in PHASES:
        bad(f"{cid}: phase must be one of {', '.join(PHASES)}")
    if not isinstance(c.get("title"), str) or not c["title"]:
        bad(f"{cid}: title is required")
    if not isinstance(c.get("fix", ""), str):
        bad(f"{cid}: fix must be text")
    if not isinstance(c.get("enabled", True), bool):
        bad(f"{cid}: enabled must be true or false")
    skip = c.get("skip_types", [])
    if not isinstance(skip, list) or not all(t in detect_mod.TYPES for t in skip):
        bad(f"{cid}: skip_types must list project types")
    params = c.get("params", {})
    if not isinstance(params, dict):
        bad(f"{cid}: params must be an object")
    defaults = EVALUATORS[c["type"]][1]
    for k, v in params.items():
        if k not in defaults:
            bad(f"{cid}: unknown param {k!r}")
        if _num(defaults[k]) != _num(v) or isinstance(defaults[k], str) != isinstance(v, str):
            bad(f"{cid}: param {k} has the wrong type")
    if c["type"] == "comm_file" and params.get("file", "contributing") not in COMM_FILES:
        bad(f"{cid}: file must be one of {', '.join(sorted(COMM_FILES))}")


def validate_rubric(rubric, path: str = "<rubric>") -> None:
    """Raise E_RUBRIC_INVALID with the first problem found."""
    def bad(detail):
        raise common.RLError("E_RUBRIC_INVALID", path=path, detail=detail)

    if not isinstance(rubric, dict) or rubric.get("schema") != 1:
        bad("schema must be 1")
    levels = rubric.get("effort_levels")
    if not isinstance(levels, dict) or not levels or not all(
            _num(v) and v > 0 for v in levels.values()):
        bad("effort_levels must map names to numbers > 0")
    grades = rubric.get("grades")
    if not isinstance(grades, list) or not grades or not all(
            isinstance(g, list) and len(g) == 2 and _num(g[0]) and isinstance(g[1], str)
            for g in grades):
        bad("grades must be a list of [threshold, letter]")
    cats = rubric.get("categories")
    if not isinstance(cats, list) or not cats or not all(
            isinstance(c, dict) and isinstance(c.get("id"), str)
            and isinstance(c.get("title"), str) for c in cats):
        bad("categories must be a list of {id, title}")
    cat_ids = [c["id"] for c in cats]
    if len(set(cat_ids)) != len(cat_ids):
        bad("duplicate category id")
    checks = rubric.get("checks")
    if not isinstance(checks, list) or not checks:
        bad("checks must be a non-empty list")
    seen: set = set()
    for c in checks:
        _check_check(c, cat_ids, levels, seen, bad)


def load_rubric(path) -> dict:
    """Load and validate a rubric JSON file; adds `_sha256` of the file bytes."""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        raise common.RLError("E_RUBRIC_INVALID", path=str(path),
                             detail=f"cannot read it ({e.strerror or e})") from None
    try:
        rubric = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise common.RLError("E_RUBRIC_INVALID", path=str(path),
                             detail=f"not valid JSON ({e})") from None
    validate_rubric(rubric, str(path))
    rubric["_sha256"] = hashlib.sha256(raw).hexdigest()
    return rubric


def default_rubric_path() -> Path:
    local = Path(".leadout") / "rubric.json"
    return local if local.is_file() else common.PLUGIN_ROOT / "scripts" / "rubric.json"


# ---------------------------------------------------------------- scoring


def _status(s) -> str:
    if s == "skip":
        return "skip"
    if s is None:
        return "unknown"
    return "pass" if s >= 1 else ("fail" if s <= 0 else "partial")


def _r1(x: Decimal) -> float:
    return common.round_half_up(x, 1)


def _warnings(facts: dict) -> list[dict]:
    target = facts.get("target") or {}
    label = target.get("slug") or target.get("path") or "repository"
    repo = facts.get("repo") or {}
    out = []
    if repo.get("private"):
        out.append(common.warning("W_PRIVATE", slug=label))
    if repo.get("archived"):
        out.append(common.warning("W_ARCHIVED", slug=label))
    if facts.get("files_truncated"):
        out.append(common.warning("W_TREE_TRUNCATED", slug=label))
    if (facts.get("unavailable") or {}).get("repo") == "no_network":
        out.append(common.warning("W_NO_NETWORK", slug=label))
    return out


def evaluate(facts: dict, rubric: dict, project_type: str | None = None) -> dict:
    """Score RepoFacts against a rubric. Pure: same input, byte-identical JSON output."""
    det = detect_mod.detect(facts)
    ptype = project_type or det["type"]
    rows = []
    for c in rubric["checks"]:
        if not c.get("enabled", True):
            continue
        if ptype in c.get("skip_types", []):
            s, ev = "skip", f"not applicable to {ptype}"
        else:
            fn, defaults = EVALUATORS[c["type"]]
            s, ev = fn(facts, ptype, {**defaults, **c.get("params", {})})
            if s not in ("skip", None):
                s = common.round_half_up(s, 2)
        rows.append((c, s, ev))
    scored = [(c, s) for c, s, _ in rows if s not in ("skip", None)]
    w_e = sum((D(str(c["weight"])) for c, _ in scored), D(0))
    w_ns = sum((D(str(c["weight"])) for c, s, _ in rows if s != "skip"), D(0))
    gained = sum((D(str(c["weight"])) * D(str(s)) for c, s in scored), D(0))
    score = common.round_half_up(100 * gained / w_e) if w_e else 0
    coverage = common.round_half_up(100 * w_e / w_ns) if w_ns else 0

    checks, fixes, cat_pts, cat_max = [], [], {}, {}
    for c, s, ev in rows:
        known = s not in ("skip", None)
        pts = D(100) * D(str(c["weight"])) / w_e if known and w_e else D(0)
        lost = pts * (1 - D(str(s))) if known else D(0)
        cat_pts[c["category"]] = cat_pts.get(c["category"], D(0)) + (pts - lost)
        cat_max[c["category"]] = cat_max.get(c["category"], D(0)) + pts
        checks.append({"id": c["id"], "category": c["category"], "title": c["title"],
                       "status": _status(s), "score": s if s != "skip" else None,
                       "points": _r1(pts), "lost": _r1(lost), "evidence": ev,
                       "phase": c["phase"], "effort": c["effort"]})
        if known and s < 1:
            prio = lost / D(str(rubric["effort_levels"][c["effort"]]))
            fixes.append((-prio, -lost, c["id"], c, ev, lost))
    fixes.sort(key=lambda f: f[:3])
    grade = rubric["grades"][-1][1]
    for threshold, letter in rubric["grades"]:
        if score >= threshold:
            grade = letter
            break
    sha = rubric.get("_sha256") or hashlib.sha256(
        common.dumps({k: v for k, v in rubric.items() if k != "_sha256"}).encode()).hexdigest()
    return {
        "schema": 1, "target": facts.get("target") or {}, "as_of": facts.get("as_of"),
        "rubric": {"name": rubric.get("name"), "version": rubric.get("version"), "sha256": sha},
        "project_type": ptype,
        "detection": {"type": det["type"], "evidence": det["evidence"],
                      "language": det["language"], "frameworks": det["frameworks"],
                      "overridden": ptype != det["type"]},
        "score": score, "grade": grade, "coverage": coverage,
        "categories": [{"id": cat["id"], "title": cat["title"],
                        "points": _r1(cat_pts.get(cat["id"], D(0))),
                        "max": _r1(cat_max.get(cat["id"], D(0)))}
                       for cat in rubric["categories"]],
        "checks": checks,
        "fixes": [{"rank": n + 1, "id": f[2], "gain": _r1(f[5]), "effort": f[3]["effort"],
                   "phase": f[3]["phase"],
                   "text": f"{f[3].get('fix', '').strip()} {f[4]}".strip()}
                  for n, f in enumerate(fixes)],
        "warnings": _warnings(facts),
    }


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(result: dict) -> str:
    """The human report (SPEC 9.6)."""
    t = result.get("target") or {}
    label = t.get("slug") or Path(t.get("path") or ".").name
    det = result.get("detection") or {}
    if det.get("overridden"):
        how = f"set with --type; detected: {det.get('type')}"
    else:
        how = "detected: " + (", ".join(det.get("evidence") or []) or "no specific marker")
    rub = result["rubric"]
    out = [f"# Launch readiness: {label}: {result['score']}/100 ({result['grade']})", "",
           f"Audited {result['as_of']} · rubric \"{rub['name']}\" {rub['version']} · "
           f"coverage {result['coverage']}% · type: {result['project_type']} ({how})", ""]
    for w in result.get("warnings") or []:
        out += [f"> Warning: {w['message']}", ""]
    unknown = [c for c in result["checks"] if c["status"] == "unknown"]
    if result["coverage"] < 100 and unknown:
        out += ["Coverage is below 100%: no data for " + ", ".join(
            f"{c['id']} ({c['evidence']})" for c in unknown) + ".", ""]
    out += ["| Category | Score |", "|---|---|"]
    out += [f"| {c['title']} | {c['points']:.1f} / {c['max']:.1f} |"
            for c in result["categories"]]
    out += ["", "## Fixes, highest impact per effort first", ""]
    if result["fixes"]:
        out += ["| # | Fix | Gain | Effort | Phase |", "|---|---|---|---|---|"]
        out += [f"| {f['rank']} | {_cell(f['text'])} | +{f['gain']:.1f} | {f['effort']} | "
                f"{f['phase']} |" for f in result["fixes"]]
    else:
        out.append("No fixes needed: every evaluated check passes.")
    out += ["", "## All checks", "", "| | Check | Evidence |", "|---|---|---|"]
    for c in result["checks"]:
        if c["status"] in ("skip", "unknown"):
            word = "skipped" if c["status"] == "skip" else "unknown"
            out.append(f"| {STATUS_ICON[c['status']]} | {_cell(c['title'])} "
                       f"({word}: {_cell(c['evidence'])}) | |")
        else:
            out.append(f"| {STATUS_ICON[c['status']]} | {_cell(c['title'])} | "
                       f"{_cell(c['evidence'])} |")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- CLI


def _inside(path: str, root: str) -> bool:
    p, r = os.path.realpath(path), os.path.realpath(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="audit.py", description="Audit a GitHub repository for launch readiness "
                                     "(read-only, deterministic 0-100 score).",
        epilog="Example: python3 scripts/audit.py sharkdp/hyperfine --as-of 2026-11-25")
    ap.add_argument("target", nargs="?", default=".", help="path, GitHub URL or owner/repo")
    ap.add_argument("--json", action="store_true", help="machine output on stdout")
    ap.add_argument("--offline", action="store_true", help="no gh calls (local path only)")
    ap.add_argument("--rubric", help="rubric JSON (default: ./.leadout/rubric.json, "
                                     "else the bundled one)")
    ap.add_argument("--type", choices=detect_mod.TYPES, help="override the detected type")
    ap.add_argument("--as-of", help="reference date YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--facts", help="evaluate saved RepoFacts JSON, no network")
    ap.add_argument("--save-facts", help="also write the collected facts to this new file")
    args = ap.parse_args(argv)
    as_of = common.check_date(args.as_of, "--as-of") if args.as_of else None
    rubric = load_rubric(args.rubric or default_rubric_path())
    if args.facts:
        facts = common.read_json(args.facts)
        if not isinstance(facts, dict) or facts.get("schema") != 1:
            raise common.RLError("E_BAD_ARGS", detail=f"{args.facts} is not RepoFacts schema 1")
        if as_of:
            facts["as_of"] = as_of
    else:
        target = common.resolve_target(args.target)
        if args.save_facts and target["kind"] == "local" and _inside(args.save_facts,
                                                                     target["path"]):
            raise common.RLError("E_BAD_ARGS", detail="--save-facts must point outside the "
                                                      "audited repository")
        facts = facts_mod.collect_facts(target, offline=args.offline, as_of=as_of)
        if args.save_facts:
            common.write_json(args.save_facts, facts)
            common.err(f"Facts written to {args.save_facts}")
    result = evaluate(facts, rubric, args.type)
    for w in result["warnings"]:
        common.err(f"leadout: warning: {w['message']}")
    common.out(common.dumps(result) if args.json else render_markdown(result).rstrip("\n"))
    return 0


def main(argv: list[str] | None = None) -> int:
    return common.run_main(_main, argv)


if __name__ == "__main__":
    sys.exit(main())
