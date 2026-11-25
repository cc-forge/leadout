"""Repository statistics: collect snapshots into a local history and report on them.

Read-only on the repository: the only file it writes is its own history
(default ./.leadout/stats.jsonl, in a folder that ignores itself in git).

Usage:
    python3 scripts/stats.py [TARGET] [report options]       collect, then report
    python3 scripts/stats.py collect [TARGET] [--history FILE] [--no-save] [--json]
                                     [--max-star-pages 10] [--source local|actions]
    python3 scripts/stats.py report  [TARGET] [--history FILE ...] [--launch-date YYYY-MM-DD]
                                     [--window 7] [--days 30] [--json]
    python3 scripts/stats.py install-workflow [TARGET] [--write] [--overwrite]

Examples:
    python3 scripts/stats.py collect cli/cli
    python3 scripts/stats.py report cli/cli --launch-date 2026-11-20
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import common

D = Decimal
STAR_ACCEPT = "application/vnd.github.star+json"
DEFAULT_HISTORY = os.path.join(".leadout", "stats.jsonl")
SUBCOMMANDS = ("collect", "report", "install-workflow", "run")
LAST_PAGE = re.compile(r'<[^>]*[?&]page=(\d+)[^>]*>\s*;\s*rel="last"')
WORKFLOW_FILES = (
    (".github/workflows/leadout-stats.yml",
     common.PLUGIN_ROOT / "templates" / "workflows" / "leadout-stats.yml"),
    (".github/leadout/stats.py", Path(__file__).resolve()),
    (".github/leadout/common.py", Path(common.__file__).resolve()),
)


# ---------------------------------------------------------------- collection


def _days(block: dict, key: str) -> list[dict]:
    return [{"date": d.get("timestamp", "")[:10], "count": d.get("count", 0),
             "uniques": d.get("uniques", 0)} for d in block.get(key) or []]


def _traffic(slug: str, warnings: list) -> dict:
    try:
        views = common.gh_api(f"repos/{slug}/traffic/views") or {}
    except common.RLError as e:
        if e.code != "E_FORBIDDEN":
            raise
        warnings.append(common.warn("W_NO_PUSH", slug=slug))
        return {"available": False, "error": "no_push_access", "views": None, "clones": None,
                "referrers": None, "paths": None}
    clones = common.gh_api(f"repos/{slug}/traffic/clones") or {}
    refs = common.gh_api(f"repos/{slug}/traffic/popular/referrers") or []
    paths = common.gh_api(f"repos/{slug}/traffic/popular/paths") or []
    return {
        "available": True, "error": None,
        "views": {"count": views.get("count", 0), "uniques": views.get("uniques", 0),
                  "days": _days(views, "views")},
        "clones": {"count": clones.get("count", 0), "uniques": clones.get("uniques", 0),
                   "days": _days(clones, "clones")},
        "referrers": [{"referrer": r.get("referrer"), "count": r.get("count", 0),
                       "uniques": r.get("uniques", 0)} for r in refs],
        "paths": [{"path": p.get("path"), "title": p.get("title"), "count": p.get("count", 0),
                   "uniques": p.get("uniques", 0)} for p in paths],
    }


def _releases(slug: str) -> list[dict]:
    out = []
    for page in (1, 2, 3):
        batch = common.gh_api(f"repos/{slug}/releases?per_page=100&page={page}") or []
        out += [{"tag": r.get("tag_name"), "published_at": r.get("published_at"),
                 "assets": [{"name": a.get("name"), "download_count": a.get("download_count", 0)}
                            for a in r.get("assets") or []]}
                for r in batch if not r.get("draft")]
        if len(batch) < 100:
            break
    return out


def _stargazers(slug: str, stars: int, max_pages: int, warnings: list) -> dict:
    if stars == 0:
        return {"available": True, "error": None, "complete": True, "events": []}
    first = common.gh_api(f"repos/{slug}/stargazers?per_page=100", accept=STAR_ACCEPT,
                          include_headers=True, allow_404=True)
    if first is None or not first.get("body"):
        warnings.append(common.warn("W_STARS_HIDDEN", slug=slug))
        return {"available": False, "error": "hidden", "complete": False, "events": []}
    link = next((v for k, v in (first.get("headers") or {}).items() if k.lower() == "link"), "")
    m = LAST_PAGE.search(link or "")
    last = int(m.group(1)) if m else 1
    pages = {1: first["body"]}
    for n in range(last, max(1, last - max_pages), -1):  # newest pages first
        pages[n] = common.gh_api(f"repos/{slug}/stargazers?per_page=100&page={n}",
                                 accept=STAR_ACCEPT) or []
    complete = last - max_pages <= 1
    if not complete:
        pages.pop(1)  # page 1 holds the oldest stars; keep the fetched range contiguous
    events = sorted(s["starred_at"] for p in pages.values() for s in p
                    if isinstance(s, dict) and s.get("starred_at"))
    if not events:
        warnings.append(common.warn("W_STARS_HIDDEN", slug=slug))
        return {"available": False, "error": "hidden", "complete": False, "events": []}
    return {"available": True, "error": None, "complete": complete, "events": events}


def collect(slug: str, max_star_pages: int = 10, source: str = "local", now=None):
    """Read one snapshot from GitHub. Returns (snapshot, warnings)."""
    warnings: list = []
    repo = common.gh_api(f"repos/{slug}")
    public = {"stars": repo.get("stargazers_count", 0), "forks": repo.get("forks_count", 0),
              "watchers": repo.get("subscribers_count", 0),
              "open_issues": repo.get("open_issues_count", 0)}
    snap = {
        "schema": 1, "collected_at": common.fmt_ts(now or common.utc_now()), "repo": slug,
        "source": source, "public": public,
        "stargazers": _stargazers(slug, public["stars"], max_star_pages, warnings),
        "traffic": _traffic(slug, warnings),
        "releases": _releases(slug),
    }
    return snap, warnings


def append_history(path, snap: dict) -> Path:
    """Append one JSON line; on first use create .leadout/.gitignore with `*`."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ignore = p.parent / ".gitignore"
    if p.parent.name == ".leadout" and not ignore.exists():
        common.write_new_file(ignore, "*\n")
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(snap, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        f.write("\n")
    return p


def load_history(paths: list[str], slug: str):
    """Snapshots of slug from every history file, deduped by collected_at, oldest first."""
    seen: dict = {}
    warnings: list = []
    for path in paths:
        try:
            f = open(path, encoding="utf-8")
        except FileNotFoundError:
            raise common.RLError("E_BAD_ARGS", detail=f"no history at {path}; run: stats.py "
                                                      f"collect {slug}") from None
        with f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    snap = json.loads(line)
                except ValueError:
                    snap = None
                if not isinstance(snap, dict) or not isinstance(snap.get("collected_at"), str):
                    warnings.append(common.warn("E_HISTORY_CORRUPT", path=path, n=n))
                    continue
                if str(snap.get("repo", "")).lower() == slug.lower():
                    seen.setdefault(snap["collected_at"], snap)
    return [seen[k] for k in sorted(seen)], warnings


# ---------------------------------------------------------------- report (pure)


def _day(ts: str) -> str:
    return ts[:10]


def _shift(day: str, n: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=n)).isoformat()


def _range(a: str, b: str) -> list[str]:
    out, d = [], a
    while d <= b:
        out.append(d)
        d = _shift(d, 1)
    return out


def _traffic_maps(snaps: list[dict]):
    """date -> (count, uniques) for views and clones; the latest snapshot of a day wins."""
    views, clones = {}, {}
    for s in snaps:
        t = s.get("traffic") or {}
        if not t.get("available"):
            continue
        for key, target in (("views", views), ("clones", clones)):
            for d in (t.get(key) or {}).get("days") or []:
                target[d["date"]] = (d.get("count", 0), d.get("uniques", 0))
    return views, clones


def _star_series(snaps: list[dict]):
    """(value(day) -> int|None, source, events_since)."""
    visible = [s for s in snaps if (s.get("stargazers") or {}).get("available")]
    events = sorted({e for s in visible for e in s["stargazers"].get("events") or []})
    complete = any(s["stargazers"].get("complete") for s in visible)
    if visible and (events or complete):
        counts = Counter(_day(e) for e in events)
        since = None if complete or not events else _day(events[0])
        until = max(_day(s["collected_at"]) for s in visible)

        def value(day):
            if day > until or (since and day < since):
                return None
            return counts.get(day, 0)
        return value, "events", since
    deltas: dict = {}
    for prev, cur in zip(snaps, snaps[1:]):
        day = _day(cur["collected_at"])
        deltas[day] = deltas.get(day, 0) + cur["public"]["stars"] - prev["public"]["stars"]
    return deltas.get, "snapshots", None


def _mean(days: list[str], fn, today: str):
    known = [v for v in (fn(d) for d in days if d != today) if v is not None]
    if not days or len(known) * 2 < len(days):
        return None
    return D(sum(known)) / D(len(known))


def _r(x, places=2):
    return None if x is None else common.round_half_up(x, places)


def _referrers(lt, traffic_snaps, star):
    cutoff = common.parse_ts(lt["collected_at"]) - dt.timedelta(days=7)
    older = next((s for s in reversed(traffic_snaps)
                  if common.parse_ts(s["collected_at"]) <= cutoff), None)
    old = {r["referrer"]: r["count"]
           for r in ((older or {}).get("traffic") or {}).get("referrers") or []}
    total = (lt["traffic"].get("views") or {}).get("count") or 0
    end = _day(lt["collected_at"])
    period = [star(d) for d in _range(_shift(end, -13), end)]
    rows = sorted(lt["traffic"].get("referrers") or [],
                  key=lambda r: (-r["count"], r["referrer"] or ""))
    return {
        "as_of": end, "compared_with": _day(older["collected_at"]) if older else None,
        "period_stars": sum(period) if all(v is not None for v in period) else None,
        "rows": [{"referrer": r["referrer"], "count": r["count"], "uniques": r["uniques"],
                  "share": _r(D(100 * r["count"]) / D(total), 1) if total else None,
                  "change": (r["count"] - old.get(r["referrer"], 0)) if older else None,
                  "new": bool(older) and r["referrer"] not in old} for r in rows]}


def _launch(launch_date, window, today, star, views, clones):
    before = _range(_shift(launch_date, -window), _shift(launch_date, -1))
    after = _range(launch_date, _shift(launch_date, window - 1))
    series = (("stars/day", star), ("views/day", lambda d: views.get(d, (None,))[0]),
              ("unique visitors/day", lambda d: views.get(d, (None, None))[1]),
              ("clones/day", lambda d: clones.get(d, (None,))[0]))
    metrics = []
    for name, fn in series:
        b, a = _mean(before, fn, today), _mean(after, fn, today)
        metrics.append({"metric": name, "before": _r(b), "after": _r(a),
                        "change": _r(a - b) if a is not None and b is not None else None,
                        "ratio": _r(a / b) if a is not None and b else None})
    return {"date": launch_date, "window": window, "metrics": metrics}


def report(snaps: list[dict], slug: str, now, launch_date: str | None = None,
           window: int = 7, days: int = 30, warnings: list | None = None) -> dict:
    """Build every report section from snapshots (oldest first). Pure."""
    if not snaps:
        raise common.RLError("E_BAD_ARGS", detail=f"the history has no snapshot of {slug}; "
                                                  f"run: stats.py collect {slug}")
    today = now.date().isoformat()
    views, clones = _traffic_maps(snaps)
    star, star_source, since = _star_series(snaps)
    last, prev = snaps[-1], (snaps[-2] if len(snaps) > 1 else None)
    traffic_snaps = [s for s in snaps if (s.get("traffic") or {}).get("available")]
    lt = traffic_snaps[-1] if traffic_snaps else None

    def change(key):
        return last["public"][key] - prev["public"][key] if prev else None

    def tget(kind, field):
        return (lt["traffic"].get(kind) or {}).get(field) if lt else None

    prev_dl = {(r["tag"], a["name"]): a["download_count"]
               for r in (prev or {}).get("releases") or [] for a in r.get("assets") or []}
    releases = [{"tag": r["tag"], "asset": a["name"], "downloads": a["download_count"],
                 "change": (a["download_count"] - prev_dl[(r["tag"], a["name"])]
                            if (r["tag"], a["name"]) in prev_dl else None)}
                for r in last.get("releases") or [] for a in r.get("assets") or []]
    totals = {"stars": last["public"]["stars"], "stars_change": change("stars"),
              "forks": last["public"]["forks"], "forks_change": change("forks"),
              "watchers": last["public"]["watchers"], "watchers_change": change("watchers"),
              "views_14d": tget("views", "count"), "uniques_14d": tget("views", "uniques"),
              "clones_14d": tget("clones", "count"),
              "clone_uniques_14d": tget("clones", "uniques"),
              "traffic_as_of": _day(lt["collected_at"]) if lt else None,
              "downloads_total": sum(r["downloads"] for r in releases)}

    firsts = [*views, *clones, *(_day(s["collected_at"]) for s in snaps)]
    if since:
        firsts.append(since)
    start = max(_shift(today, -(days - 1)), min(firsts + [today]))
    daily = [{"date": d, "stars": star(d), "views": views.get(d, (None,))[0],
              "uniques": views.get(d, (None, None))[1], "clones": clones.get(d, (None,))[0],
              "clone_uniques": clones.get(d, (None, None))[1], "partial": d == today}
             for d in _range(start, today)]
    paths = sorted((lt["traffic"].get("paths") or []) if lt else [],
                   key=lambda p: (-p["count"], p["path"] or ""))[:10]
    return {
        "schema": 1, "repo": slug, "generated_at": common.fmt_ts(now),
        "header": {"snapshots": len(snaps), "first": snaps[0]["collected_at"],
                   "last": last["collected_at"], "traffic_available": lt is not None,
                   "traffic_as_of": totals["traffic_as_of"], "stars_source": star_source,
                   "stars_since": since},
        "totals": totals, "daily": daily,
        "referrers": _referrers(lt, traffic_snaps, star) if lt else None, "paths": paths,
        "launch": (_launch(launch_date, window, today, star, views, clones)
                   if launch_date else None),
        "releases": releases, "warnings": list(warnings or []),
    }


def _n(v, signed=False) -> str:
    if v is None:
        return "–"
    s = f"{v:.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    return ("+" + s) if signed and v > 0 else s


def _md_referrers(ref) -> list[str]:
    if not ref:
        return ["Not available (traffic needs push access)."]
    out = [f"14-day totals from the reading of {ref['as_of']}.", "",
           f"| Referrer | Views | Unique visitors | Share of views | "
           f"Change vs {ref['compared_with'] or 'a week earlier'} |", "|---|---|---|---|---|"]
    for row in ref["rows"]:
        ch = "new" if row["new"] else _n(row["change"], True)
        share = "–" if row["share"] is None else f"{row['share']:.1f}%"
        out.append(f"| {row['referrer']} | {row['count']} | {row['uniques']} | {share} | {ch} |")
    out += ["", "GitHub reports referrers as 14-day totals and does not tie stars to "
                f"referrers. Stars in the same 14 days: {_n(ref['period_stars'], True)} "
                "(context, not attribution)."]
    return out


def _md_launch(L) -> list[str]:
    out = ["", f"## Before/after launch ({L['date']}, {L['window']} days each side)", "",
           "| Metric | Before (per day) | After (per day) | Change |", "|---|---|---|---|"]
    for m in L["metrics"]:
        if m["before"] is None or m["after"] is None:
            b = "insufficient data" if m["before"] is None else _n(m["before"])
            a = "insufficient data" if m["after"] is None else _n(m["after"])
            out.append(f"| {m['metric']} | {b} | {a} | – |")
            continue
        ratio = f" (×{_n(m['ratio'])})" if m["ratio"] is not None else ""
        out.append(f"| {m['metric']} | {_n(m['before'])} | {_n(m['after'])} | "
                   f"{_n(m['change'], True)}{ratio} |")
    return out


def render_markdown(r: dict) -> str:
    h, t = r["header"], r["totals"]
    out = [f"# Repository stats: {r['repo']}", "",
           f"Snapshots: {h['snapshots']} · collected {h['first']} → {h['last']}"]
    if h["traffic_available"]:
        out.append(f"Traffic: from the reading of {h['traffic_as_of']} "
                   "(GitHub keeps 14 days; older days come from earlier snapshots).")
    else:
        out.append("Traffic: not available (it needs push access); public data only.")
    if h["stars_source"] == "events":
        since = f" since {h['stars_since']}" if h["stars_since"] else " (complete)"
        out.append(f"Stars per day: from stargazer events{since}.")
    else:
        out.append("Stars per day: from snapshots (changes between readings); GitHub does "
                   "not list stargazer dates to this account.")
    out += ["", "## Totals now", "", "| Metric | Value | Change since previous snapshot |",
            "|---|---|---|",
            f"| Stars | {t['stars']} | {_n(t['stars_change'], True)} |",
            f"| Forks | {t['forks']} | {_n(t['forks_change'], True)} |",
            f"| Watchers | {t['watchers']} | {_n(t['watchers_change'], True)} |",
            f"| Views (14 d) | {_n(t['views_14d'])} | |",
            f"| Unique visitors (14 d) | {_n(t['uniques_14d'])} | |",
            f"| Clones (14 d) | {_n(t['clones_14d'])} | |",
            f"| Release downloads (all time) | {t['downloads_total']} | |",
            "", f"## Daily growth (last {len(r['daily'])} days)", "",
            "| Date | +Stars | Views | Unique visitors | Clones | Unique cloners |",
            "|---|---|---|---|---|---|"]
    for d in reversed(r["daily"]):
        label = d["date"] + (" (partial)" if d["partial"] else "")
        out.append(f"| {label} | {_n(d['stars'], True)} | {_n(d['views'])} | "
                   f"{_n(d['uniques'])} | {_n(d['clones'])} | {_n(d['clone_uniques'])} |")
    out += ["", "## Referrers", ""] + _md_referrers(r["referrers"])
    out += ["", "## Popular pages", ""]
    if r["paths"]:
        out += ["| Path | Views | Unique visitors |", "|---|---|---|"]
        out += [f"| {p['path']} | {p['count']} | {p['uniques']} |" for p in r["paths"]]
    else:
        out.append("Not available.")
    if r["launch"]:
        out += _md_launch(r["launch"])
    out += ["", "## Release downloads", ""]
    if r["releases"]:
        out += ["| Release | Asset | Downloads | Change since previous snapshot |",
                "|---|---|---|---|"]
        out += [f"| {x['tag']} | {x['asset']} | {x['downloads']} | {_n(x['change'], True)} |"
                for x in r["releases"]]
    else:
        out.append("No release assets.")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- CLI


def _slug(arg: str) -> str:
    t = common.resolve_target(arg)
    if not t["slug"]:
        raise common.RLError("E_BAD_TARGET", message=f'"{arg}" has no GitHub remote (origin).',
                             hint="Pass owner/repo, for example: stats.py collect cli/cli")
    return t["slug"]


def _summary(snap: dict) -> str:
    p, t, s = snap["public"], snap["traffic"], snap["stargazers"]
    lines = [f"{snap['repo']}: {p['stars']} stars, {p['forks']} forks, "
             f"{p['watchers']} watchers, {p['open_issues']} open issues and PRs."]
    if t["available"]:
        lines.append(f"Traffic (14 d): {t['views']['count']} views, {t['views']['uniques']} "
                     f"unique visitors, {t['clones']['count']} clones.")
    else:
        lines.append("Traffic: needs push access; public data only.")
    if s["available"]:
        lines.append(f"Star events: {len(s['events'])}"
                     f"{' (complete)' if s['complete'] else ' (newest pages only)'}.")
    else:
        lines.append("Star events: hidden by GitHub; the timeline uses snapshots.")
    return "\n".join(lines)


def _install_workflow(arg: str, write: bool, overwrite: bool) -> int:
    t = common.resolve_target(arg)
    if t["kind"] != "local":
        raise common.RLError("E_BAD_ARGS", detail="install-workflow needs a local checkout path")
    root = Path(t["path"])
    if not write:
        common.out("Would create these files (run again with --write after review):")
        for rel, _ in WORKFLOW_FILES:
            common.out(f"  {rel}{' (exists)' if (root / rel).exists() else ''}")
        common.out("")
        common.out(f"==> {WORKFLOW_FILES[0][0]} <==")
        common.out(WORKFLOW_FILES[0][1].read_text(encoding="utf-8").rstrip("\n"))
        return 0
    if common.git(["rev-parse", "--is-inside-work-tree"], root) is None:
        raise common.RLError("E_NOT_GIT", path=str(root))
    if common.is_default_branch(root):
        branch = (common.git(["symbolic-ref", "--quiet", "--short", "HEAD"], root)
                  or "detached HEAD").strip()
        raise common.RLError("E_DEFAULT_BRANCH", branch=branch,
                             date=common.utc_now().date().isoformat())
    if not overwrite:
        for rel, _ in WORKFLOW_FILES:
            if (root / rel).exists():
                raise common.RLError("E_FILE_EXISTS", path=rel)
    for rel, src in WORKFLOW_FILES:
        common.write_new_file(root / rel, src.read_text(encoding="utf-8"), overwrite=overwrite)
        common.out(f"Wrote {rel}")
    common.out("Nothing is staged or committed. Review with: git status && git diff")
    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="stats.py", description="GitHub repository stats with a local history "
                                     "(read-only on the repository).",
        epilog="Example: python3 scripts/stats.py report cli/cli --launch-date 2026-11-20")
    sub = ap.add_subparsers(dest="cmd")

    def base(p, many_histories):
        p.add_argument("target", nargs="?", default=".", help="owner/repo, URL or a checkout")
        p.add_argument("--json", action="store_true", help="machine output on stdout")
        p.add_argument("--history", action="append" if many_histories else "store",
                       help=f"history JSONL (default: {DEFAULT_HISTORY})")

    def report_opts(p):
        p.add_argument("--launch-date", help="launch day YYYY-MM-DD for before/after")
        p.add_argument("--window", type=int, default=7, help="days on each side (default 7)")
        p.add_argument("--days", type=int, default=30, help="daily table length (default 30)")

    def collect_opts(p):
        p.add_argument("--max-star-pages", type=int, default=10,
                       help="stargazer pages to read, newest first (default 10)")
        p.add_argument("--source", choices=("local", "actions"), default="local")

    c = sub.add_parser("collect", help="read one snapshot and append it to the history")
    base(c, False)
    collect_opts(c)
    c.add_argument("--no-save", action="store_true", help="print only, do not save")
    r = sub.add_parser("report", help="report on the saved history")
    base(r, True)
    report_opts(r)
    run = sub.add_parser("run", help="collect, then report (the default)")
    base(run, True)
    collect_opts(run)
    report_opts(run)
    w = sub.add_parser("install-workflow", help="print (or --write) the daily Actions workflow")
    w.add_argument("target", nargs="?", default=".", help="local checkout")
    w.add_argument("--write", action="store_true", help="write the files (new branch only)")
    w.add_argument("--overwrite", action="store_true", help="replace existing files")
    return ap


def _main(argv: list[str]) -> int:
    if not argv or argv[0] not in SUBCOMMANDS + ("-h", "--help"):
        argv = ["run", *argv]
    args = _parser().parse_args(argv)
    if args.cmd == "install-workflow":
        return _install_workflow(args.target, args.write, args.overwrite)
    for flag in ("window", "days", "max_star_pages"):
        if getattr(args, flag, 1) < 1:
            raise common.RLError("E_BAD_ARGS", detail=f"--{flag.replace('_', '-')} must be >= 1")
    slug = _slug(args.target)
    if args.cmd in ("collect", "run"):
        snap, _ = collect(slug, args.max_star_pages, args.source)
        history = args.history if isinstance(args.history, str) else (
            (args.history or [DEFAULT_HISTORY])[0])
        if args.cmd == "collect" and args.no_save:
            common.err("Not saved (--no-save).")
        else:
            append_history(history, snap)
            common.err(f"Snapshot appended to {history}")
        if args.cmd == "collect":
            common.out(common.dumps(snap) if args.json else _summary(snap))
            return 0
    launch = common.check_date(args.launch_date, "--launch-date") if args.launch_date else None
    snaps, warnings = load_history(args.history or [DEFAULT_HISTORY], slug)
    result = report(snaps, slug, common.utc_now(), launch, args.window, args.days, warnings)
    common.out(common.dumps(result) if args.json else render_markdown(result).rstrip("\n"))
    return 0


def main(argv: list[str] | None = None) -> int:
    return common.run_main(_main, argv)


if __name__ == "__main__":
    sys.exit(main())
