"""Collect RepoFacts (SPEC section 8) for a local checkout or a remote owner/repo.

Read-only: it never writes into the analyzed repository.

Usage:
    python3 scripts/facts.py [TARGET] [--offline] [--as-of YYYY-MM-DD] [--out FILE]

Examples:
    python3 scripts/facts.py cli/cli --as-of 2026-11-25
    python3 scripts/facts.py . --offline
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

import common

README_CAP = 200 * 1024
GFI_LABELS = ("good first issue", "good-first-issue", "first-timers-only", "beginner", "easy")
ROOT_MANIFESTS = ("package.json", "pyproject.toml", "setup.py", "setup.cfg", "Cargo.toml",
                  "go.mod", "composer.json", "Package.swift", "pubspec.yaml", "hugo.toml",
                  "config.toml", "wrangler.toml", "wrangler.json", "wrangler.jsonc")
OTHER_CI = (".gitlab-ci.yml", ".circleci/config.yml", ".travis.yml", "azure-pipelines.yml",
            "Jenkinsfile")
WALK_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv"}
OG_QUERY = ("query($owner:String!,$name:String!){repository(owner:$owner,name:$name)"
            "{usesCustomOpenGraphImage licenseInfo{spdxId} defaultBranchRef{name}}}")
_README_RE = re.compile(r"^(?:\.github/|docs/)?readme(?:\.[^/]+)?$", re.I)
_QUOTED = re.compile(r'"([^"]*)"|\'([^\']*)\'')


# ---------------------------------------------------------------- TOML


def toml_load(text: str) -> dict:
    """Parse TOML with tomllib when present (3.11+), else the small regex reader."""
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        return toml_regex(text)
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return {}


def _toml_value(v: str):
    v = v.strip()
    if v[:1] in ('"', "'"):
        end = v.find(v[0], 1)
        return v[1:end] if end > 0 else v[1:]
    if v in ("true", "false"):
        return v == "true"
    if v.startswith("["):
        return [a or b for a, b in _QUOTED.findall(v)]
    if v.startswith("{"):
        return dict(re.findall(r'([A-Za-z0-9_.-]+)\s*=\s*"([^"]*)"', v))
    try:
        return int(v)
    except ValueError:
        return v


def toml_regex(text: str) -> dict:
    """Read the TOML keys detect.py needs.

    ponytail: regex TOML reader, handles flat tables only (no multi-line strings,
    '#' inside strings is cut as a comment); tomllib replaces it on Python 3.11+.
    """
    data: dict = {}
    cur = data
    pending_key, pending = None, ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if pending_key is not None:
            pending += " " + line
            if "]" in line:
                cur[pending_key] = _toml_value(pending)
                pending_key = None
            continue
        if not line:
            continue
        m = re.fullmatch(r"\[\[\s*([^\]]+?)\s*\]\]", line)
        if m:
            *parents, last = [p.strip().strip('"') for p in m.group(1).split(".")]
            node = data
            for p in parents:
                node = node.setdefault(p, {})
            node.setdefault(last, []).append({})
            cur = node[last][-1]
            continue
        m = re.fullmatch(r"\[\s*([^\]]+?)\s*\]", line)
        if m:
            cur = data
            for p in m.group(1).split("."):
                cur = cur.setdefault(p.strip().strip('"'), {})
            continue
        m = re.match(r'^("?[A-Za-z0-9_.-]+"?)\s*=\s*(.*)$', line)
        if not m:
            continue
        key, value = m.group(1).strip('"'), m.group(2)
        if value.startswith("[") and "]" not in value:
            pending_key, pending = key, value
            continue
        cur[key] = _toml_value(value)
    return data


# ---------------------------------------------------------------- manifests


def manifest_paths(files: list[str]) -> list[str]:
    """Paths of the manifest files detect.py reads, in sorted order."""
    out = []
    for f in files:
        if f in ROOT_MANIFESTS or re.fullmatch(r"[^/]+\.gemspec", f) or re.fullmatch(
                r"[^/]+\.xcodeproj/project\.pbxproj", f):
            out.append(f)
    return sorted(out)


def _json(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _req_name(spec: str) -> str | None:
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec or "")
    return m.group(1).lower() if m else None


def _package_json(text: str) -> dict:
    d = _json(text)
    b = d.get("bin")
    bins = sorted(b) if isinstance(b, dict) else ([d.get("name")] if isinstance(b, str) else [])
    deps: set = set()
    for k in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(d.get(k), dict):
            deps |= set(d[k])
    scripts = d.get("scripts") if isinstance(d.get("scripts"), dict) else {}
    return {"name": d.get("name"), "version": d.get("version"), "bin": bins,
            "main": d.get("main"), "module": d.get("module"), "exports": "exports" in d,
            "scripts": sorted(scripts), "dependencies": sorted(deps),
            "keywords": [k for k in d.get("keywords") or [] if isinstance(k, str)]}


def _pyproject(text: str) -> dict:
    t = toml_load(text)
    project = t.get("project") if isinstance(t.get("project"), dict) else {}
    poetry = (t.get("tool") or {}).get("poetry") or {}
    scripts = set(project.get("scripts") or {}) | set(poetry.get("scripts") or {})
    deps = {_req_name(s) for s in project.get("dependencies") or [] if isinstance(s, str)}
    deps |= {k.lower() for k in (poetry.get("dependencies") or {}) if k != "python"}
    return {"name": project.get("name") or poetry.get("name"),
            "version": project.get("version") or poetry.get("version"),
            "requires_python": project.get("requires-python"),
            "scripts": sorted(scripts), "dependencies": sorted(d for d in deps if d),
            "keywords": list(project.get("keywords") or poetry.get("keywords") or [])}


def reduce_manifest(path: str, text: str) -> dict:
    """Keep only the manifest keys that detect.py and fix.py need."""
    name = path.rsplit("/", 1)[-1]
    if name == "package.json":
        return _package_json(text)
    if name == "pyproject.toml":
        return _pyproject(text)
    if name in ("setup.py", "setup.cfg"):
        m = re.search(r"""\bname\s*[=:]\s*['"]?([A-Za-z0-9._-]+)""", text)
        return {"name": m.group(1) if m else None,
                "console_scripts": "console_scripts" in text}
    if name == "Cargo.toml":
        t = toml_load(text)
        pkg = t.get("package") or {}
        return {"name": pkg.get("name"), "version": pkg.get("version"),
                "bin": bool(t.get("bin")), "lib": "lib" in t}
    if name == "go.mod":
        m = re.search(r"(?m)^module\s+(\S+)", text)
        return {"module": m.group(1) if m else None}
    if name.endswith(".gemspec"):
        m = re.search(r"""\.name\s*=\s*['"]([^'"]+)""", text)
        return {"name": m.group(1) if m else None, "executables": "executables" in text}
    if name == "composer.json":
        return {"name": _json(text).get("name")}
    if name == "Package.swift":
        return {"macos_app": ".macOS" in text and (".executableTarget" in text
                                                   or ".executable(" in text)}
    if name == "project.pbxproj":
        return {"macos": "SDKROOT = macosx" in text}
    if name == "pubspec.yaml":
        m = re.search(r"(?m)^name:\s*(\S+)", text)
        return {"name": m.group(1) if m else None,
                "flutter": re.search(r"(?m)^\s+sdk:\s*flutter\b", text) is not None}
    if name in ("hugo.toml", "config.toml"):
        return {"base_url": re.search(r"(?mi)^\s*baseURL\s*=", text) is not None}
    if name.startswith("wrangler."):
        return {"pages_build_output_dir": "pages_build_output_dir" in text,
                "assets": re.search(r'(?m)^\s*\[assets\]|"assets"\s*:', text) is not None}
    return {}


# ---------------------------------------------------------------- helpers


def find_readme(files: list[str]) -> str | None:
    """README path in GitHub's order of precedence: .github/, root, docs/."""
    for prefix in (".github/", "", "docs/"):
        for f in files:
            if f.startswith(prefix) and "/" not in f[len(prefix):] and _README_RE.match(f):
                return f
    return None


def _cap(text: str) -> str:
    raw = text.encode("utf-8")
    return raw[:README_CAP].decode("utf-8", errors="ignore") if len(raw) > README_CAP else text


def _ext_bytes(sizes: dict) -> dict:
    out: dict = {}
    for f, size in sizes.items():
        ext = Path(f).suffix.lower()
        if ext:
            out[ext] = out.get(ext, 0) + int(size or 0)
    return dict(sorted(out.items()))


def _ci_base(files: list[str]) -> dict:
    wf = [f for f in files if re.fullmatch(r"\.github/workflows/[^/]+\.ya?ml", f)]
    other = [f for f in files if f in OTHER_CI or f.startswith(".buildkite/")]
    return {"workflow_files": wf, "workflows_with_runs": [], "other_ci_files": other,
            "latest_default_branch_run": None}


def _b64(body) -> str:
    try:
        return base64.b64decode(body.get("content") or "").decode("utf-8", errors="replace")
    except (ValueError, AttributeError):
        return ""


def _optional(facts: dict, section: str, fn):
    """Run fn; a 403 marks the section as unavailable instead of failing."""
    try:
        return fn()
    except common.RLError as e:
        if e.code != "E_FORBIDDEN":
            raise
        facts["unavailable"][section] = "forbidden"
        return None


# ---------------------------------------------------------------- local


def _local_files(path: str):
    out = common.git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], path)
    if out is not None:
        files = {f for f in out.split("\0") if f and os.path.isfile(os.path.join(path, f))}
        return sorted(files), True
    files = []
    # ponytail: outside git there is no .gitignore parsing, only a fixed skip list
    for root, dirs, names in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in WALK_SKIP)
        for n in names:
            files.append(Path(os.path.relpath(os.path.join(root, n), path)).as_posix())
    return sorted(files), False


def _read(path: str, rel: str) -> str:
    with open(os.path.join(path, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def _collect_local(path: str, facts: dict) -> None:
    files, is_git = _local_files(path)
    facts["files"] = files
    facts["ext_bytes"] = _ext_bytes({f: os.path.getsize(os.path.join(path, f)) for f in files})
    readme = find_readme(files)
    if readme:
        facts["readme"] = {"path": readme, "text": _cap(_read(path, readme))}
    facts["manifests"] = {p: reduce_manifest(p, _read(path, p)) for p in manifest_paths(files)}
    if is_git:
        last = (common.git(["log", "-1", "--format=%cI"], path) or "").strip()
        facts["last_commit_at"] = common.fmt_ts(common.parse_ts(last)) if last else None
        if not last:
            facts["unavailable"]["last_commit_at"] = "no_commits"
        tags = common.git(["tag", "--list", "--sort=-creatordate"], path) or ""
        facts["tags"] = [t for t in tags.splitlines() if t.strip()]
    else:
        facts["unavailable"]["last_commit_at"] = "no_git"
        facts["unavailable"]["tags"] = "no_git"


# ---------------------------------------------------------------- remote


def _release(r: dict) -> dict:
    return {"tag": r.get("tag_name"), "published_at": r.get("published_at"),
            "draft": bool(r.get("draft")), "prerelease": bool(r.get("prerelease")),
            "body_chars": len(r.get("body") or ""),
            "assets": [{"name": a.get("name"), "download_count": a.get("download_count", 0)}
                       for a in r.get("assets") or []]}


def _remote_tree(slug: str, qb: str, facts: dict) -> None:
    api = common.gh_api
    tree = api(f"repos/{slug}/git/trees/{qb}?recursive=1", allow_404=True) or {}
    blobs = {e["path"]: e.get("size", 0) for e in tree.get("tree") or []
             if e.get("type") == "blob"}
    facts["files"] = sorted(blobs)
    facts["files_truncated"] = bool(tree.get("truncated"))
    facts["ext_bytes"] = _ext_bytes(blobs)
    readme = api(f"repos/{slug}/readme", allow_404=True)
    if readme:
        facts["readme"] = {"path": readme.get("path", "README.md"), "text": _cap(_b64(readme))}
    manifests = {}
    for p in manifest_paths(facts["files"]):
        body = api(f"repos/{slug}/contents/{quote(p)}?ref={qb}", allow_404=True)
        if body:
            manifests[p] = reduce_manifest(p, _b64(body))
    facts["manifests"] = manifests
    commits = api(f"repos/{slug}/commits?per_page=1", allow_404=True) or []
    if commits:
        date = ((commits[0].get("commit") or {}).get("committer") or {}).get("date")
        facts["last_commit_at"] = common.fmt_ts(common.parse_ts(date)) if date else None


def _remote_issues(slug: str, facts: dict) -> None:
    api = common.gh_api
    raw = api(f"repos/{slug}/issues?state=open&per_page=100") or []
    labels = [x.get("name", "") for x in api(f"repos/{slug}/labels?per_page=100") or []]
    gfi = sorted(lb for lb in labels if lb.lower() in GFI_LABELS)
    gfi_open = 0
    if gfi:
        q = f"repo:{slug} is:issue is:open label:" + ",".join(f'"{lb}"' for lb in gfi)
        found = api(f"search/issues?q={quote(q, safe='')}&per_page=1") or {}
        gfi_open = found.get("total_count", 0)
    facts["issues"] = {
        "open_sample": [{"number": i.get("number"), "created_at": i.get("created_at"),
                         "comments": i.get("comments", 0)}
                        for i in raw if "pull_request" not in i],
        "open_sample_complete": len(raw) < 100, "gfi_labels": gfi, "gfi_open": gfi_open}


def _collect_remote(slug: str, facts: dict, local: bool) -> None:
    api = common.gh_api
    r = api(f"repos/{slug}")
    owner, name = slug.split("/")
    data = common.gh_graphql(OG_QUERY, {"owner": owner, "name": name}) or {}
    g = data.get("repository") or {}
    branch = (g.get("defaultBranchRef") or {}).get("name") or r.get("default_branch") or "main"
    qb = quote(branch, safe="")
    spdx = (g.get("licenseInfo") or {}).get("spdxId") or (r.get("license") or {}).get("spdx_id")
    facts["repo"] = {
        "private": bool(r.get("private")), "archived": bool(r.get("archived")),
        "fork": bool(r.get("fork")), "has_issues": bool(r.get("has_issues")),
        "description": r.get("description") or "", "homepage": r.get("homepage") or "",
        "topics": list(r.get("topics") or []), "default_branch": branch,
        "license_spdx": spdx, "uses_custom_og_image": g.get("usesCustomOpenGraphImage"),
        "stars": r.get("stargazers_count", 0), "forks": r.get("forks_count", 0),
        "subscribers": r.get("subscribers_count", 0), "primary_language": r.get("language"),
    }
    if not local:
        _remote_tree(slug, qb, facts)
    facts["releases"] = [_release(x) for x in api(f"repos/{slug}/releases?per_page=10") or []]
    if not facts.get("tags"):
        facts["tags"] = [t.get("name") for t in api(f"repos/{slug}/tags?per_page=30") or []]
        facts["unavailable"].pop("tags", None)
    ci = _ci_base(facts["files"])
    if ci["workflow_files"]:
        runs = _optional(facts, "ci", lambda: api(
            f"repos/{slug}/actions/runs?branch={qb}&status=completed&per_page=1")) or {}
        run = (runs.get("workflow_runs") or [None])[0]
        if run:
            ci["latest_default_branch_run"] = {"conclusion": run.get("conclusion"),
                                               "created_at": run.get("created_at")}
            ci["workflows_with_runs"] = [run["path"]] if run.get("path") else []
    facts["ci"] = ci
    if facts["repo"]["has_issues"]:
        _remote_issues(slug, facts)
    else:
        facts["unavailable"]["issues"] = "disabled"
    profile = _optional(facts, "community",
                        lambda: api(f"repos/{slug}/community/profile", allow_404=True))
    if profile:
        pf = profile.get("files") or {}
        facts["community"] = {k: pf.get(k) is not None for k in (
            "code_of_conduct", "contributing", "issue_template", "pull_request_template")}
    elif "community" not in facts["unavailable"]:
        facts["unavailable"]["community"] = "not_found"


# ---------------------------------------------------------------- entry points


def collect_facts(target, offline: bool = False, as_of: str | None = None) -> dict:
    """Collect RepoFacts for a resolved target dict (or a raw TARGET string)."""
    if isinstance(target, str):
        target = common.resolve_target(target)
    if target["kind"] == "remote" and offline:
        raise common.RLError("E_BAD_ARGS", detail="--offline needs a local path; a remote "
                                                  "owner/repo can only be read through gh")
    facts = {"schema": 1, "as_of": as_of or common.utc_now().date().isoformat(),
             "target": target, "repo": None, "files": [], "files_truncated": False,
             "readme": None, "manifests": {}, "ext_bytes": {}, "last_commit_at": None,
             "releases": None, "tags": None, "ci": None, "issues": None, "community": None,
             "unavailable": {}}
    if target["kind"] == "local":
        _collect_local(target["path"], facts)
    slug = target["slug"]
    reason = "offline" if slug else "no_remote"
    if slug and not offline:
        try:
            _collect_remote(slug, facts, local=target["kind"] == "local")
            reason = None
        except common.RLError as e:
            # A local checkout can still be scored from its files; a remote target cannot.
            if e.code != "E_NETWORK" or target["kind"] != "local":
                raise
            reason = "no_network"
            for section in ("repo", "releases", "issues", "community"):  # drop partial data
                facts[section] = None
                facts["unavailable"].pop(section, None)
    if reason:
        for section in ("repo", "releases", "issues", "community"):
            facts["unavailable"].setdefault(section, reason)
        facts["ci"] = _ci_base(facts["files"])
    facts["unavailable"] = dict(sorted(facts["unavailable"].items()))
    return facts


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="facts.py", description="Collect RepoFacts as JSON (read-only).",
        epilog="Example: python3 scripts/facts.py cli/cli --as-of 2026-11-25")
    ap.add_argument("target", nargs="?", default=".", help="path, GitHub URL or owner/repo")
    ap.add_argument("--offline", action="store_true", help="no gh calls (local path only)")
    ap.add_argument("--as-of", help="reference date YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--out", help="write the JSON to this new file instead of stdout")
    args = ap.parse_args(argv)
    as_of = common.check_date(args.as_of, "--as-of") if args.as_of else None
    facts = collect_facts(args.target, offline=args.offline, as_of=as_of)
    if args.out:
        common.write_json(args.out, facts)
        common.err(f"Facts written to {args.out}")
    else:
        common.out(common.dumps(facts))
    return 0


def main(argv: list[str] | None = None) -> int:
    return common.run_main(_main, argv)


if __name__ == "__main__":
    sys.exit(main())
