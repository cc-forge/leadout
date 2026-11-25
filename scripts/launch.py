"""Build and check a launch kit (SPEC sections 7.7 and 12). Writes text files only: it
never publishes anything on any platform.

Subcommands:
    init        create launch/ from the templates, prefilled from repository facts
    subreddits  list verified subreddits that fit the project type and language
    awesome     search GitHub for relevant awesome lists
    calendar    dated launch schedule in your timezone
    lint        check the kit against the platform rules (exit 1 on errors)

Usage:
    python3 scripts/launch.py init [TARGET] [--out launch] [--type TYPE] [--launch-date D]
                                   [--timezone TZ] [--overwrite] [--offline]
    python3 scripts/launch.py subreddits [--type TYPE] [--language LANG] [--json]
    python3 scripts/launch.py awesome [TARGET] [--limit 10] [--json]
    python3 scripts/launch.py calendar --launch-date YYYY-MM-DD [--timezone TZ] [--json]
    python3 scripts/launch.py lint [--dir launch]

Example:
    python3 scripts/launch.py calendar --launch-date 2026-12-02 --timezone Europe/Rome
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import os
import re
import sys
import urllib.parse
from pathlib import Path

import common
import detect as detect_mod
import facts as facts_mod

try:  # zoneinfo ships with Python 3.9+
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None

LAUNCH_TEMPLATES = common.PLUGIN_ROOT / "templates" / "launch"
KIT_FILES = ("show-hn.md", "reddit.md", "x-thread.md", "devto.md", "awesome-lists.md",
             "calendar.md", "checklist.md", "objections.md")
ALLOW = "<!-- lint-allow -->"
VOTE_PATTERNS = [re.compile(p, re.I) for p in (
    r"\bupvote", r"\bvote (for|up)\b", r"\b(please|pls) (star|vote)",
    r"\bgive (it|us|me) a star\b", r"\bstar (it|this|the repo)\b", r"star[- ]for[- ]star",
    r"\bstar exchange\b", r"\bsupport (us|me) by (starring|voting)\b")]
SUPERLATIVES = re.compile(r"\b(best|fastest|revolutionary|ultimate|world's first)\b", re.I)
URL_RE = re.compile(r"https?://\S+")
STALE_DAYS = 180
TWEET_MAX, URL_LEN, SIMILAR = 280, 23, 0.6


def _tpl(name: str) -> str:
    return (LAUNCH_TEMPLATES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- calendar


def _system_tz():
    try:
        m = re.search(r"zoneinfo/(.+)$", os.path.realpath("/etc/localtime"))
        if m and ZoneInfo:
            return ZoneInfo(m.group(1))
    except Exception:  # noqa: BLE001 - fall through to the fixed offset
        pass
    return dt.datetime.now().astimezone().tzinfo  # ponytail: fixed offset, no DST rule


def get_tz(name: str | None):
    if not name:
        return _system_tz()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise common.RLError("E_BAD_ARGS", detail=f"unknown timezone {name!r}; use a name like "
                                                  "Europe/Rome") from None


def calendar_rows(launch_date: str | None, tz_name: str | None = None) -> dict:
    """{"rows": [{"when", "what"}], "notes": [str]} from calendar.json."""
    spec = common.read_json(LAUNCH_TEMPLATES / "calendar.json")
    ref = get_tz(spec["reference_zone"])
    rows, notes = [], [spec["note"]]
    day = dt.date.fromisoformat(launch_date) if launch_date else None
    tz = get_tz(tz_name) if launch_date else None
    for s in spec["slots"]:
        off = s["offset_days"]
        label = "D" + (f"{off:+d}" if off else "")
        if day is None:
            when = label + (f" {s['time']} {spec['reference_zone']}" if s["time"] else "")
        else:
            d = day + dt.timedelta(days=off)
            if s["time"]:
                hh, mm = (int(x) for x in s["time"].split(":"))
                local = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=ref).astimezone(tz)
                when = local.strftime("%Y-%m-%d %H:%M %Z (%a)")
            else:
                when = d.strftime("%Y-%m-%d (%a)")
        rows.append({"when": when, "what": s["what"]})
    if day and day.weekday() not in (1, 2, 3):
        notes.append(f"Launch day falls on a {day.strftime('%A')}; Tuesday to Thursday is the "
                     "usual preference for Show HN.")
    return {"rows": rows, "notes": notes}


def calendar_markdown(cal: dict) -> str:
    lines = ["| When | What |", "|---|---|"] + [f"| {r['when']} | {r['what']} |"
                                              for r in cal["rows"]]
    return "\n".join(lines + [""] + [f"- {n}" for n in cal["notes"]])


# ---------------------------------------------------------------- subreddits


def subreddits(ptype: str | None = None, language: str | None = None,
               today: dt.date | None = None) -> dict:
    data = common.read_json(LAUNCH_TEMPLATES / "subreddits.json")
    today = today or dt.date.today()
    out, stale = [], []
    for s in data["subreddits"]:
        if ptype and s["fits_types"] and ptype not in s["fits_types"]:
            continue
        if language and s["fits_languages"] and language.lower() not in [
                x.lower() for x in s["fits_languages"]]:
            continue
        out.append(s)
        if (today - dt.date.fromisoformat(s["verified_at"])).days > STALE_DAYS:
            stale.append(s["name"])
    return {"subreddits": out, "stale": stale}


# ---------------------------------------------------------------- awesome


def awesome_queries(terms: list) -> list:
    qs: list = []
    for t in terms:
        for q in (f"awesome {t} in:name topic:awesome", f"awesome-{t} in:name"):
            if q not in qs:
                qs.append(q)
    return qs[:5]


def search_awesome(terms: list, limit: int = 10, today: dt.date | None = None) -> list:
    today = today or dt.date.today()
    found: dict = {}
    for q in awesome_queries(terms):
        res = common.gh_api("search/repositories?q=" + urllib.parse.quote(q, safe="")
                            + "&sort=stars&order=desc&per_page=30")
        for r in (res or {}).get("items") or []:
            pushed = (r.get("pushed_at") or "")[:10]
            if r.get("archived") or not pushed or (today - dt.date.fromisoformat(pushed)
                                                   ).days > 365:
                continue
            found.setdefault(r["full_name"], {
                "full_name": r["full_name"], "html_url": r["html_url"],
                "stars": r.get("stargazers_count", 0), "pushed_at": r.get("pushed_at")})
    top = sorted(found.values(), key=lambda r: (-r["stars"], r["full_name"]))[:limit]
    for r in top:
        listing = common.gh_api(f"repos/{r['full_name']}/contents", allow_404=True) or []
        r["contributing_url"] = next((f.get("html_url") for f in listing if isinstance(f, dict)
                                      and f.get("name", "").upper().startswith("CONTRIBUTING")),
                                     None)
    return top


def awesome_table(rows: list) -> str:
    if not rows:
        return "No active awesome lists found for these search terms."
    lines = ["| List | Stars | Last push | Contributing guide |", "|---|---|---|---|"]
    for r in rows:
        lines.append(f"| [{r['full_name']}]({r['html_url']}) | {r['stars']} | "
                     f"{(r['pushed_at'] or '')[:10]} | {r['contributing_url'] or '-'} |")
    return "\n".join(lines)


# ---------------------------------------------------------------- init


def _reddit_sections(subs: list) -> str:
    if not subs:
        return ("## r/TODO(subreddit)\n\nNo verified subreddit rules ship with leadout yet. "
                "Pick communities that fit, read each live rules page, and add one section "
                "per community in this format.\n\n**Title:** TODO(title)\n\n**Body:**\n\n"
                "TODO(post written for this community)")
    out = []
    for s in subs:
        out.append(f"## r/{s['name']}\n\nRules ({s['rules_url']}), verified {s['verified_at']}: "
                   f"{s['self_promotion']} Flair: {s['flair'] or 'none required'}.\n\n"
                   f"**Title:** TODO(title for r/{s['name']})\n\n**Body:**\n\n"
                   f"TODO(post for r/{s['name']}, a different angle from the other posts)")
    return "\n\n".join(out)


def kit_values(facts: dict, ptype: str, language: str | None) -> dict:
    repo = facts.get("repo") or {}
    slug = common.slug_of(facts)
    topics = [t for t in repo.get("topics") or []][:4]
    return {"name": common.repo_name(facts), "tagline": repo.get("description") or "",
            "repo_url": f"https://github.com/{slug}" if slug else "",
            "submit_url": repo.get("homepage") or (f"https://github.com/{slug}" if slug else ""),
            "license_name": repo.get("license_spdx") or "",
            "tags": "[" + ", ".join(topics) + "]" if topics else ""}


def init_kit(root: Path, out: str, facts: dict, detection: dict, launch_date: str | None,
             tz: str | None, overwrite: bool) -> dict:
    values = kit_values(facts, detection["type"], detection.get("language"))
    subs = subreddits(detection["type"], detection.get("language"))["subreddits"]
    values["sections"] = _reddit_sections(subs)
    values["schedule"] = calendar_markdown(calendar_rows(launch_date, tz))
    dest = Path(out) if Path(out).is_absolute() else root / out
    created, skipped = [], []
    for name in KIT_FILES:
        path = dest / name
        if path.exists() and not overwrite:
            skipped.append(str(path))
            continue
        text, _ = common.render(_tpl(name), values)
        common.write_new_file(path, text, overwrite=overwrite)
        created.append(str(path))
    return {"created": created, "skipped": skipped}


# ---------------------------------------------------------------- lint


def _line_of(text: str, needle: str) -> int:
    for i, ln in enumerate(text.splitlines(), 1):
        if needle in ln:
            return i
    return 1


def _tweet_len(text: str) -> int:
    return len(URL_RE.sub("x" * URL_LEN, text.strip()))


def _titles_md(text: str) -> list:
    """List items under a heading that contains 'title' (Show HN title options)."""
    items, on = [], False
    for ln in text.splitlines():
        if re.match(r"^#{1,6}\s", ln):
            on = "title" in ln.lower()
        elif on:
            m = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.+?)\s*$", ln)
            if m:
                items.append(m.group(1))
    return items


def lint_dir(directory) -> dict:
    d = Path(directory)
    if not d.is_dir():
        raise common.RLError("E_BAD_ARGS", detail=f"{d} is not a directory")
    errors, warnings = [], []
    texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.md"))}

    def err(f, line, msg):
        errors.append(f"{d / f}:{line}: error: {msg}")

    def warn(f, line, msg):
        warnings.append(f"{d / f}:{line}: warning: {msg}")

    for f, text in texts.items():
        for i, ln in enumerate(text.splitlines(), 1):
            if ALLOW not in ln and any(p.search(ln) for p in VOTE_PATTERNS):
                err(f, i, "asks for votes or stars; remove it, or mark a line that states the "
                          f"rule itself with {ALLOW}")
        n = text.count("TODO(")
        if n:
            warn(f, 1, f"{n} TODO placeholder(s) remaining")
    hn = texts.get("show-hn.md", "")
    for t in _titles_md(hn):
        if not t.startswith("Show HN: ") or len(t) > 80:
            err("show-hn.md", _line_of(hn, t), f"title must start with 'Show HN: ' and be at "
                                               f"most 80 characters ({len(t)}): {t}")
        if SUPERLATIVES.search(t):
            warn("show-hn.md", _line_of(hn, t), f"superlative in title: {t}")
    rd = texts.get("reddit.md", "")
    posts = []
    for m in re.finditer(r"^## r/(.+?)\n(.*?)(?=^## r/|\Z)", rd, re.M | re.S):
        title = re.search(r"^\*\*Title:\*\*\s*(.+)$", m.group(2), re.M)
        body = m.group(2).split("**Body:**", 1)[-1].strip() if "**Body:**" in m.group(2) else ""
        posts.append((m.group(1), (title.group(1).strip() if title else ""), body))
        if title and SUPERLATIVES.search(title.group(1)):
            warn("reddit.md", _line_of(rd, title.group(1)), f"superlative in title: "
                                                            f"{title.group(1)}")
    for i in range(len(posts)):
        for j in range(i + 1, len(posts)):
            (na, ta, ba), (nb, tb, bb) = posts[i], posts[j]
            if ta and ta.lower() == tb.lower():
                err("reddit.md", _line_of(rd, tb), f"r/{na} and r/{nb} have the same title")
            ca, cb = (re.sub(r"TODO\([^)]*\)", "", b).strip() for b in (ba, bb))
            if min(len(ca), len(cb)) >= 40 and difflib.SequenceMatcher(
                    None, ca, cb).ratio() >= SIMILAR:
                err("reddit.md", _line_of(rd, nb), f"the posts for r/{na} and r/{nb} are too "
                                                   "similar; write each for its community")
    xt = texts.get("x-thread.md", "")
    for m in re.finditer(r"^### Post \d+\n(.*?)(?=^### |\Z)", xt, re.M | re.S):
        n = _tweet_len(m.group(1))
        if n > TWEET_MAX:
            err("x-thread.md", _line_of(xt, m.group(1).strip().splitlines()[0]),
                f"post is {n} characters (max {TWEET_MAX}, a URL counts as {URL_LEN})")
    if len(re.findall(r"(?<!\w)#\w+", xt)) > 2:
        warn("x-thread.md", 1, "more than 2 hashtags in the thread")
    dv = texts.get("devto.md", "")
    fm = re.match(r"^---\n(.*?)\n---", dv, re.S)
    if fm:
        head = fm.group(1)
        if re.search(r"^published:\s*true\b", head, re.M):
            err("devto.md", _line_of(dv, "published:"), "published must stay false; "
                                                        "publish by hand")
        tags = re.search(r"^tags:\s*(.*)$", head, re.M)
        if tags and len([t for t in tags.group(1).strip("[] ").split(",") if t.strip()]) > 4:
            err("devto.md", _line_of(dv, "tags:"), "dev.to allows at most 4 tags")
        title = re.search(r"^title:\s*(.+)$", head, re.M)
        if title and SUPERLATIVES.search(title.group(1)):
            warn("devto.md", _line_of(dv, "title:"), f"superlative in title: {title.group(1)}")
    return {"errors": errors, "warnings": warnings}


# ---------------------------------------------------------------- commands


def _facts_ctx(args, offline: bool):
    facts = facts_mod.collect_facts(args.target, offline=offline)
    detection = detect_mod.detect(facts)
    if getattr(args, "type", None):
        if args.type not in detect_mod.TYPES:
            raise common.RLError("E_BAD_ARGS", detail="--type must be one of "
                                                      + ", ".join(detect_mod.TYPES))
        detection = dict(detection, type=args.type)
    return facts, detection


def cmd_init(args) -> int:
    target = common.resolve_target(args.target)
    if target["kind"] != "local":
        raise common.RLError("E_NOT_GIT", path=args.target,
                             hint="Clone the repository first, then run the command inside it.")
    if args.launch_date:
        common.check_date(args.launch_date, "--launch-date")
    facts, detection = _facts_ctx(args, args.offline)
    r = init_kit(Path(target["path"]), args.out, facts, detection, args.launch_date,
                 args.timezone, args.overwrite)
    for p in r["created"]:
        common.out(f"created {p}")
    for p in r["skipped"]:
        common.out(f"kept {p} (exists; pass --overwrite after confirming)")
    common.out("Nothing was published. Next: fill the TODO(...) slots from facts, then run "
               "launch.py lint.")
    return 0


def cmd_subreddits(args) -> int:
    r = subreddits(args.type, args.language)
    if args.json:
        common.out(common.dumps(r))
        return 0
    if not r["subreddits"]:
        common.out("No verified subreddit entries match. Read each community's live rules "
                   "page before posting.")
    for s in r["subreddits"]:
        common.out(f"r/{s['name']}: {s['self_promotion']} Rules: {s['rules_url']} "
                   f"(verified {s['verified_at']})")
    for n in r["stale"]:
        common.err(f"leadout: warning: the rules of r/{n} were verified over "
                   f"{STALE_DAYS} days ago; re-check them.")
    return 0


def cmd_awesome(args) -> int:
    facts, detection = _facts_ctx(args, False)
    repo = facts.get("repo") or {}
    terms: list = []
    for t in [detection.get("language") or "", *(repo.get("topics") or []),
              "" if detection["type"] == "other" else detection["type"]]:
        t = re.sub(r"[^a-z0-9-]", "", str(t).lower().replace(" ", "-"))
        if t and t not in terms:
            terms.append(t)
    rows = search_awesome(terms, args.limit)
    common.out(common.dumps(rows) if args.json else awesome_table(rows))
    return 0


def cmd_calendar(args) -> int:
    common.check_date(args.launch_date, "--launch-date")
    cal = calendar_rows(args.launch_date, args.timezone)
    common.out(common.dumps(cal) if args.json else calendar_markdown(cal))
    return 0


def cmd_lint(args) -> int:
    r = lint_dir(args.dir)
    for line in r["errors"] + r["warnings"]:
        common.out(line)
    common.out(f"{len(r['errors'])} error(s), {len(r['warnings'])} warning(s)")
    return 1 if r["errors"] else 0


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="launch.py", description="Build and check a launch kit (text only).",
        epilog="Example: python3 scripts/launch.py lint --dir launch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create launch/ from templates")
    p.add_argument("target", nargs="?", default=".")
    p.add_argument("--out", default="launch")
    p.add_argument("--type")
    p.add_argument("--launch-date")
    p.add_argument("--timezone")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--offline", action="store_true", help="no gh calls")
    p = sub.add_parser("subreddits", help="verified subreddits that fit")
    p.add_argument("--type")
    p.add_argument("--language")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("awesome", help="search awesome lists")
    p.add_argument("target", nargs="?", default=".")
    p.add_argument("--type")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("calendar", help="dated schedule")
    p.add_argument("--launch-date", required=True)
    p.add_argument("--timezone")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("lint", help="check the kit against the platform rules")
    p.add_argument("--dir", default="launch")
    args = ap.parse_args(argv)
    return {"init": cmd_init, "subreddits": cmd_subreddits, "awesome": cmd_awesome,
            "calendar": cmd_calendar, "lint": cmd_lint}[args.cmd](args)


def main(argv: list[str] | None = None) -> int:
    return common.run_main(_main, argv)


if __name__ == "__main__":
    sys.exit(main())
