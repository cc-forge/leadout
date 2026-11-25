"""Shared helpers for the leadout scripts (library, not a command).

Errors and their exact messages, the gh wrapper with fixture/record modes, target
resolution, redaction, JSON output, file writing, time helpers.

Usage from a sibling script:

    import common
    def main(argv=None):
        return common.run_main(_main, argv)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import traceback
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
GH_TIMEOUT = 60
GIT_TIMEOUT = 30

# code -> (exit code, message template, hint template)
ERRORS = {
    "E_GH_MISSING": (3, "GitHub CLI (gh) is not installed.",
                     "Install it from https://cli.github.com, then run: gh auth login"),
    "E_GH_AUTH": (3, "gh is not authenticated.", "Run: gh auth login"),
    "E_REPO_NOT_FOUND": (3, "Repository {slug} was not found, or it is private and your "
                            "account cannot see it.",
                         "Check the owner/name spelling, or run: gh auth status"),
    "E_RATE_LIMIT": (3, "GitHub API rate limit reached. It resets at {reset_local} "
                        "(in {minutes} min).",
                     "Try again after the reset, or use --offline for a local-only audit."),
    "E_SECONDARY_RATE_LIMIT": (3, "GitHub asked to slow down (secondary rate limit).",
                               "Wait a minute and try again."),
    "E_FORBIDDEN": (3, "GitHub refused access to {endpoint} (HTTP 403).",
                    "Your account may lack permission for this data."),
    "E_NETWORK": (3, "Cannot reach GitHub: {detail}.",
                  "Check your connection, or use --offline for a local-only audit."),
    "E_GH_FAILED": (3, "gh failed: {detail}.",
                    "Run the same command with LEADOUT_DEBUG=1 for details."),
    "E_BAD_TARGET": (2, '"{arg}" is not a directory, a GitHub URL or owner/repo.',
                     "Examples: . or cli/cli or https://github.com/cli/cli"),
    "E_BAD_ARGS": (2, "{detail}", "Run with --help for usage."),
    "E_NOT_GIT": (4, "{path} is not a git repository.", "Run the command inside a git checkout."),
    "E_DEFAULT_BRANCH": (4, "Refusing to write on the default branch ({branch}).",
                         "Create a branch first: git switch -c leadout/fix-{date}"),
    "E_DIRTY_TREE": (4, "The working tree has uncommitted changes.",
                     "Commit or stash them first, so the fix diff stays clean."),
    "E_FILE_EXISTS": (4, "{path} already exists; not overwriting.",
                      "Pass --overwrite after reviewing the diff."),
    "E_RUBRIC_INVALID": (2, "Rubric {path} is invalid: {detail}.",
                         "Compare it with scripts/rubric.json."),
    "E_TOOL_MISSING": (4, "{tool} is not installed.", "{install_hint}"),
    "E_HISTORY_CORRUPT": (4, "{path} line {n} is not valid JSON; skipped.", ""),
}

WARNINGS = {
    "W_NO_PUSH": "Traffic data needs push access to {slug}. Showing public data only.",
    "W_PRIVATE": "{slug} is private. A private repository cannot be launched publicly; "
                 "checks still run.",
    "W_ARCHIVED": "{slug} is archived.",
    "W_NO_NETWORK": "GitHub could not be reached, so {slug} was audited from its local files "
                    "only; checks that need GitHub have no data (no_network).",
    "W_TREE_TRUNCATED": "The file list of {slug} is truncated by GitHub; file checks may "
                        "miss files.",
    "W_STARS_HIDDEN": "GitHub does not list stargazers of {slug} to this account; the star "
                      "timeline uses stored snapshots only.",
}

NETWORK_PATTERNS = ("could not resolve host", "dial tcp", "connection refused", "timeout",
                    "error connecting to", "no such host", "network is unreachable")
_TOKEN_RE = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")
_FIELD_RE = re.compile(r"\{(\w+)\}")
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SLUG_PATTERNS = (
    re.compile(r"^https?://(?:www\.)?github\.com/(?P<o>[^/\s]+)/(?P<r>[^/\s#?]+)(?:[/#?].*)?$"),
    re.compile(r"^(?:ssh://)?git@github\.com[:/](?P<o>[^/\s]+)/(?P<r>[^/\s]+?)/?$"),
    re.compile(r"^(?P<o>[A-Za-z0-9-]+)/(?P<r>[A-Za-z0-9._-]+)$"),
)
_auth_checked = False


def _fill(template: str, fields: dict) -> str:
    return _FIELD_RE.sub(lambda m: str(fields.get(m.group(1), "?")), template)


class RLError(Exception):
    """Expected failure with a standard code, message, hint and exit code."""

    def __init__(self, code: str, message: str | None = None, hint: str | None = None,
                 **fields):
        exit_code, msg_t, hint_t = ERRORS.get(code, (5, "{detail}", ""))
        self.code = code
        self.exit_code = exit_code
        self.message = message if message is not None else _fill(msg_t, fields)
        self.hint = hint if hint is not None else _fill(hint_t, fields)
        super().__init__(self.message)


# ---------------------------------------------------------------- output


def redact(text: str) -> str:
    """Replace anything that looks like a GitHub token with [REDACTED]."""
    return _TOKEN_RE.sub("[REDACTED]", text or "")


def dumps(data) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)


def out(text: str) -> None:
    print(redact(text))


def err(text: str) -> None:
    print(redact(text), file=sys.stderr)


def warning(code: str, **fields) -> dict:
    """Build a warning record without printing it (for pure functions)."""
    template = WARNINGS.get(code) or ERRORS.get(code, (0, code, ""))[1]
    return {"code": code, "message": _fill(template, fields)}


def warn(code: str, **fields) -> dict:
    """Print `leadout: warning: ...` to stderr and return the warning record."""
    w = warning(code, **fields)
    err(f"leadout: warning: {w['message']}")
    return w


def run_main(fn, argv: list[str] | None = None) -> int:
    """Run fn(argv) and turn every failure into a one-line message and an exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    try:
        return int(fn(argv) or 0)
    except RLError as e:
        err(f"leadout: error: {e.message}")
        if e.hint:
            err(f"hint: {e.hint}")
        if as_json:
            out(dumps({"error": {"code": e.code, "message": e.message, "hint": e.hint}}))
        return e.exit_code
    except KeyboardInterrupt:
        err("leadout: interrupted")
        return 130
    except SystemExit as e:  # argparse: --help (0) or usage error (2)
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 2)
    except Exception as e:  # noqa: BLE001 - last-resort guard, by design
        if os.environ.get("LEADOUT_DEBUG") == "1":
            err(traceback.format_exc())
        err(f"leadout: internal error: {type(e).__name__}: {e}")
        err("hint: rerun with LEADOUT_DEBUG=1 for a traceback")
        return 5


# ---------------------------------------------------------------- numbers and time


def round_half_up(x, places: int = 0):
    """Round half away from zero on the decimal representation; int when places == 0."""
    q = Decimal(1) if places == 0 else Decimal(1).scaleb(-places)
    v = (x if isinstance(x, Decimal) else Decimal(str(x))).quantize(q, ROUND_HALF_UP)
    return int(v) if places == 0 else float(v)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def parse_ts(s: str) -> dt.datetime:
    """Parse `YYYY-MM-DD` or ISO 8601 (with Z or offset) into an aware UTC datetime."""
    if len(s) == 10:
        d = dt.datetime.strptime(s, "%Y-%m-%d")
    else:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def fmt_ts(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_between(a: str, b: str) -> int:
    """Whole UTC calendar days from a to b (negative when b is earlier)."""
    return (parse_ts(b).date() - parse_ts(a).date()).days


def check_date(value: str, flag: str) -> str:
    """Validate a `YYYY-MM-DD` argument or raise E_BAD_ARGS."""
    try:
        dt.datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise RLError("E_BAD_ARGS", detail=f"{flag} must be a date like 2026-11-25, "
                                           f"got {value!r}") from None
    return value


# ---------------------------------------------------------------- files


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise RLError("E_BAD_ARGS", detail=f"{path} does not exist") from None
    except (OSError, ValueError) as e:
        raise RLError("E_BAD_ARGS", detail=f"{path} is not readable JSON ({e})") from None


def write_new_file(path, text: str, overwrite: bool = False) -> Path:
    """Write UTF-8 text with a trailing newline; refuse an existing file unless overwrite."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    try:
        with open(p, "w" if overwrite else "x", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except FileExistsError:
        raise RLError("E_FILE_EXISTS", path=str(p)) from None
    return p


def write_json(path, data, overwrite: bool = False) -> Path:
    return write_new_file(path, dumps(data), overwrite=overwrite)


_PLACEHOLDER = re.compile(r"\[\[([a-z_][a-z0-9_]*)\]\]")


def render(text: str, values: dict):
    """Fill `[[name]]` placeholders. Missing or empty values become `TODO(name)`.

    Returns (rendered, todos) where todos lists the missing names in order of appearance.
    """
    todos: list[str] = []

    def sub(m):
        name = m.group(1)
        v = values.get(name)
        if v is None or str(v) == "":
            if name not in todos:
                todos.append(name)
            return f"TODO({name})"
        return str(v)

    return _PLACEHOLDER.sub(sub, text), todos


# ---------------------------------------------------------------- git and targets


def git(args: list[str], cwd) -> str | None:
    """Run git; return stdout on success, None on a non-zero exit."""
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                           timeout=GIT_TIMEOUT)
    except FileNotFoundError:
        raise RLError("E_TOOL_MISSING", tool="git",
                      install_hint="Install git from https://git-scm.com") from None
    except subprocess.TimeoutExpired:
        return None
    return p.stdout if p.returncode == 0 else None


def is_default_branch(path) -> bool:
    """True when HEAD is the default branch or detached (both unsafe for writing)."""
    cur = git(["symbolic-ref", "--quiet", "--short", "HEAD"], path)
    if cur is None:
        return True
    default = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], path)
    if default and default.strip():
        return cur.strip() == default.strip().split("/", 1)[-1]
    # ponytail: no origin/HEAD known, so main/master are assumed to be the default
    return cur.strip() in ("main", "master")


def working_tree_clean(path) -> bool:
    status = git(["status", "--porcelain"], path)
    if status is None:
        raise RLError("E_NOT_GIT", path=str(path))
    return status.strip() == ""


def parse_slug(text: str) -> str | None:
    """owner/repo from a GitHub https/ssh URL or a bare owner/repo, validated; else None."""
    text = (text or "").strip()
    for pattern in _SLUG_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        owner, name = m.group("o"), m.group("r")
        if name.endswith(".git"):
            name = name[:-4]
        if _OWNER_RE.match(owner) and _NAME_RE.match(name) and name not in (".", ".."):
            return f"{owner}/{name}"
        return None
    return None


def resolve_target(arg: str | None) -> dict:
    """Classify a CLI target as a local directory or a remote owner/repo (SPEC 6.3)."""
    arg = "." if arg in (None, "") else arg
    p = Path(arg).expanduser()
    if p.is_dir():
        path = p.resolve()
        slug = None
        try:
            origin = git(["remote", "get-url", "origin"], path)
            slug = parse_slug(origin) if origin else None
        except RLError:
            slug = None
        return {"kind": "local", "path": str(path), "slug": slug}
    slug = parse_slug(arg)
    if slug:
        return {"kind": "remote", "path": None, "slug": slug}
    raise RLError("E_BAD_TARGET", arg=arg)


# ---------------------------------------------------------------- gh wrapper


def fixture_key(endpoint: str, accept: str | None = None) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "_", endpoint).strip("_").lower()
    if accept:
        key += "__accept_" + re.sub(r"[^A-Za-z0-9]+", "_", accept).strip("_").lower()
    return key


def _gh_env() -> dict:
    env = dict(os.environ)
    env.pop("GH_DEBUG", None)
    return env


def _run_gh(args: list[str]):
    try:
        return subprocess.run(["gh", *args], capture_output=True, text=True,
                              timeout=GH_TIMEOUT, env=_gh_env())
    except FileNotFoundError:
        raise RLError("E_GH_MISSING") from None
    except subprocess.TimeoutExpired:
        raise RLError("E_NETWORK", detail=f"no answer within {GH_TIMEOUT} s") from None


def gh_available() -> bool:
    """True when gh is installed and authenticated (always true in fixture mode)."""
    if os.environ.get("LEADOUT_FIXTURES"):
        return True
    try:
        return _run_gh(["auth", "status", "--hostname", "github.com"]).returncode == 0
    except RLError:
        return False


def _ensure_auth() -> None:
    global _auth_checked
    if _auth_checked:
        return
    if _run_gh(["auth", "status", "--hostname", "github.com"]).returncode != 0:
        raise RLError("E_GH_AUTH")
    _auth_checked = True


def _load_fixture(key: str) -> dict:
    path = Path(os.environ["LEADOUT_FIXTURES"]) / f"{key}.json"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise RLError("E_GH_FAILED", detail=f"missing fixture {key}") from None


def _sanitize(obj):
    """Keep only `login` in user-like objects, drop emails, redact strings.

    `verification` goes too: a signed commit's payload embeds author and committer emails.
    """
    if isinstance(obj, dict):
        if isinstance(obj.get("login"), str):
            return {"login": obj["login"]}
        return {k: _sanitize(v) for k, v in obj.items() if k not in ("email", "verification")}
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    if isinstance(obj, str):
        return redact(_BARE_EMAIL.sub("<email>", _TRAILER_EMAIL.sub("<email>", obj)))
    return obj


_TRAILER_EMAIL = re.compile(r"<[^<>@\s]+@[^<>\s]+>")  # "Name <a@b>" in commit messages
# Emails in free text (issue bodies, shell prompts). The domain must start with a letter so
# file names like Icon@2x.png survive; git@ is the SSH clone user, not a person.
_BARE_EMAIL = re.compile(r"(?<![\w.+-])(?!git@)[\w.+-]+@[A-Za-z][\w-]*(?:\.[\w-]+)+")


def _record(key: str, status: int, body, stderr: str, headers: dict) -> None:
    folder = os.environ.get("LEADOUT_RECORD")
    if not folder:
        return
    link = {k: v for k, v in headers.items() if k.lower() == "link"}
    rec = {"status": status, "body": _sanitize(body), "stderr": redact(stderr),
           "headers": link}
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / f"{key}.json").write_text(dumps(rec) + "\n", encoding="utf-8")


def _parse_body(raw: str, paginate: bool = False):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        if not paginate:
            return None
    # gh --paginate prints one JSON document per page back to back
    pages, pos, dec = [], 0, json.JSONDecoder()
    while pos < len(raw):
        page, pos = dec.raw_decode(raw, pos)
        pages.append(page)
        while pos < len(raw) and raw[pos].isspace():
            pos += 1
    if all(isinstance(p, list) for p in pages):
        return [item for p in pages for item in p]
    return pages


def _split_headers(raw: str):
    parts = re.split(r"\r?\n\r?\n", raw or "", maxsplit=1)
    head = parts[0].splitlines()
    status = None
    if head and head[0].startswith("HTTP/"):
        m = re.match(r"HTTP/\S+\s+(\d{3})", head[0])
        status = int(m.group(1)) if m else None
    headers = {}
    for line in head[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return status, headers, parts[1] if len(parts) > 1 else ""


def _status_from(stderr: str, returncode: int) -> int:
    m = re.search(r"\(HTTP (\d{3})\)", stderr or "")
    if m:
        return int(m.group(1))
    return 200 if returncode == 0 else 0


def _repo_endpoint_slug(endpoint: str) -> str | None:
    m = re.fullmatch(r"repos/([^/?]+)/([^/?]+)", endpoint)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _rate_limit_fields() -> dict:
    try:
        reset = int(gh_api("rate_limit")["resources"]["core"]["reset"])
    except (RLError, KeyError, TypeError, ValueError):
        return {"reset_local": "an unknown time", "minutes": "?"}
    local = dt.datetime.fromtimestamp(reset).astimezone().strftime("%H:%M")
    minutes = max(0, math.ceil((reset - utc_now().timestamp()) / 60))
    return {"reset_local": local, "minutes": minutes}


def map_error(status: int, stderr: str, body, endpoint: str) -> RLError:
    """Translate a failed gh call into the matching standard RLError."""
    message = body.get("message", "") if isinstance(body, dict) else ""
    text = f"{stderr or ''} {message}"
    low = text.lower()
    first = next((ln.strip() for ln in (stderr or "").splitlines() if ln.strip()), "")
    first = re.sub(r"^gh:\s*", "", first).rstrip(".") or f"HTTP {status}"
    if status == 0 and any(p in low for p in NETWORK_PATTERNS):
        return RLError("E_NETWORK", detail=first)
    if status == 401 or "bad credentials" in low or "gh auth login" in low:
        return RLError("E_GH_AUTH")
    if status == 404:
        slug = _repo_endpoint_slug(endpoint)
        if slug:
            return RLError("E_REPO_NOT_FOUND", slug=slug)
        return RLError("E_GH_FAILED", detail=f"{endpoint} returned HTTP 404")
    if status in (403, 429):
        if "secondary rate limit" in low:
            return RLError("E_SECONDARY_RATE_LIMIT")
        if "rate limit" in low:
            fields = {} if endpoint == "rate_limit" else _rate_limit_fields()
            return RLError("E_RATE_LIMIT", **fields)
        return RLError("E_FORBIDDEN", endpoint=endpoint)
    if any(p in low for p in NETWORK_PATTERNS):
        return RLError("E_NETWORK", detail=first)
    return RLError("E_GH_FAILED", detail=first)


def gh_api(endpoint: str, *, method: str = "GET", accept: str | None = None,
           fields: dict | None = None, paginate: bool = False,
           include_headers: bool = False, allow_404: bool = False):
    """Call `gh api` and return the parsed JSON body.

    include_headers=True returns {"status", "headers", "body"} instead.
    allow_404=True returns None for a 404 on anything but the repository itself.
    """
    endpoint = endpoint.lstrip("/")
    key = fixture_key(endpoint, accept)
    if os.environ.get("LEADOUT_FIXTURES"):
        rec = _load_fixture(key)
        status = int(rec.get("status", 200))
        body, stderr, headers = rec.get("body"), rec.get("stderr", ""), rec.get("headers") or {}
    else:
        _ensure_auth()
        args = ["api", endpoint, "--method", method]
        if accept:
            args += ["-H", f"Accept: {accept}"]
        for k, v in sorted((fields or {}).items()):
            args += ["-f", f"{k}={v}"]
        if paginate:
            args.append("--paginate")
        if include_headers:
            args.append("-i")
        proc = _run_gh(args)
        stderr, raw, headers = proc.stderr, proc.stdout, {}
        status = _status_from(stderr, proc.returncode)
        if include_headers:
            line_status, headers, raw = _split_headers(raw)
            if line_status:
                status = line_status
        body = _parse_body(raw, paginate)
        _record(key, status, body, stderr, headers)
    if status >= 400 or status == 0:
        if status == 404 and allow_404 and not _repo_endpoint_slug(endpoint):
            return None
        raise map_error(status, stderr, body, endpoint)
    return {"status": status, "headers": headers, "body": body} if include_headers else body


def gh_graphql(query: str, variables: dict):
    """Run a GraphQL query through `gh api graphql` and return `data`."""
    payload = query + json.dumps(variables, sort_keys=True)
    key = "graphql_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    if os.environ.get("LEADOUT_FIXTURES"):
        rec = _load_fixture(key)
        status, body, stderr = int(rec.get("status", 200)), rec.get("body"), rec.get("stderr", "")
    else:
        _ensure_auth()
        args = ["api", "graphql", "-f", f"query={query}"]
        for k, v in sorted(variables.items()):
            if isinstance(v, (bool, int)):
                args += ["-F", f"{k}={json.dumps(v)}"]
            else:
                args += ["-f", f"{k}={v}"]
        proc = _run_gh(args)
        stderr, body = proc.stderr, _parse_body(proc.stdout)
        status = _status_from(stderr, proc.returncode)
        _record(key, status, body, stderr, {})
    errors = body.get("errors") if isinstance(body, dict) else None
    if errors:
        types = [e.get("type") for e in errors if isinstance(e, dict)]
        if "NOT_FOUND" in types:
            slug = f"{variables.get('owner', '?')}/{variables.get('name', '?')}"
            raise RLError("E_REPO_NOT_FOUND", slug=slug)
        if "RATE_LIMITED" in types:
            raise RLError("E_RATE_LIMIT", **_rate_limit_fields())
        raise RLError("E_GH_FAILED", detail=str(errors[0].get("message", "GraphQL error")))
    if status >= 400 or status == 0 or not isinstance(body, dict):
        raise map_error(status, stderr, body, "graphql")
    return body.get("data")


# ---------------------------------------------------------------- facts helpers


def slug_of(facts: dict) -> str | None:
    """owner/repo of a RepoFacts dict, or None for a checkout without a GitHub remote."""
    return (facts.get("target") or {}).get("slug")


def repo_name(facts: dict) -> str:
    """Repository name: from the slug, else a manifest name, else the folder name."""
    slug = slug_of(facts)
    if slug:
        return slug.split("/", 1)[1]
    for k in ("package.json", "pyproject.toml", "Cargo.toml"):
        n = ((facts.get("manifests") or {}).get(k) or {}).get("name")
        if n:
            return str(n)
    path = (facts.get("target") or {}).get("path")
    return Path(path).name if path else ""
