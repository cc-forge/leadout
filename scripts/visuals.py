"""Plan and produce a repository's visuals (SPEC sections 7.6 and 11).

Subcommands:
    plan            GIF storyboard for the project type and the commands for tools you have
    compress        shrink a recording to a GIF under a size limit (ffmpeg, gifski)
    social-preview  1280x640 SVG for GitHub's social preview
    banner          light and dark README banners (1280x320 SVG) and the <picture> snippet
    to-png          convert an SVG to PNG with the first converter found

Usage:
    python3 scripts/visuals.py plan [TARGET] [--type TYPE] [--offline]
    python3 scripts/visuals.py compress INPUT --out FILE.gif [--max-mb 5] [--width 960] [--fps 12]
    python3 scripts/visuals.py social-preview [TARGET] --out FILE.svg [--title T] [--tagline T]
    python3 scripts/visuals.py banner [TARGET] --out-dir DIR [--title T] [--tagline T]
    python3 scripts/visuals.py to-png INPUT.svg --out FILE.png [--width W --height H]

Every writing subcommand refuses an existing file unless --overwrite is passed.

Example:
    python3 scripts/visuals.py social-preview . --out assets/social-preview.svg
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

import common
import detect as detect_mod
import facts as facts_mod

TEMPLATES = common.PLUGIN_ROOT / "templates" / "visuals"
FFMPEG_TIMEOUT = 600
TOOLS = ("ffmpeg", "gifski", "vhs", "asciinema", "agg", "screencapture", "wf-recorder")
INSTALL_HINTS = {
    "ffmpeg": "brew install ffmpeg (macOS) or apt install ffmpeg (Debian, Ubuntu)",
    "gifski": "brew install gifski (macOS) or cargo install gifski",
    "vhs": "brew install vhs (macOS) or see https://github.com/charmbracelet/vhs",
}
STORYBOARDS = {
    "cli": ["Terminal ready, large font, empty prompt",
            "Type the one install or run command",
            "The output that shows the main result stays on screen"],
    "library": ["A code snippet of at most 8 lines, already on screen",
                "Run it",
                "The result appears"],
    "desktop-app": ["The app window is already open",
                    "Do the main action",
                    "The visible result"],
    "website": ["The landing page, above the fold",
                "The key interaction",
                "The result state"],
    "claude-plugin": ["A Claude Code prompt",
                      "Type the slash command",
                      "The result"],
    "other": ["The project in its working state",
              "The main action",
              "The result"],
}
TYPE_LABELS = {"cli": "Command-line tool", "library": "Library", "desktop-app": "Desktop app",
               "website": "Website", "claude-plugin": "Claude Code plugin", "other": "Project"}
THEMES = {
    "light": {"bg": "#ffffff", "fg": "#1f2328", "muted": "#59636e"},
    "dark": {"bg": "#0d1117", "fg": "#f0f6fc", "muted": "#9198a1"},
}
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                "microsoft-edge", "chrome")
CHROME_MAC = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------------------------------------------------------------- process helpers


def _run(cmd: list, timeout: int = FFMPEG_TIMEOUT):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise common.RLError("E_TOOL_MISSING", tool=cmd[0],
                             install_hint=INSTALL_HINTS.get(cmd[0], f"Install {cmd[0]}.")
                             ) from None
    except subprocess.TimeoutExpired:
        raise common.RLError("E_BAD_ARGS", detail=f"{cmd[0]} took longer than {timeout} s"
                             ) from None


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else "no output"


def _check_out(out, overwrite: bool) -> Path:
    p = Path(out)
    if p.exists() and not overwrite:
        raise common.RLError("E_FILE_EXISTS", path=str(p))
    return p


def _context(args):
    facts = facts_mod.collect_facts(args.target, offline=args.offline)
    detection = detect_mod.detect(facts)
    if getattr(args, "type", None):
        if args.type not in detect_mod.TYPES:
            raise common.RLError("E_BAD_ARGS", detail="--type must be one of "
                                                      + ", ".join(detect_mod.TYPES))
        detection = dict(detection, type=args.type)
    return facts, detection


def _accent(value: str) -> str:
    if not _HEX.match(value):
        raise common.RLError("E_BAD_ARGS", detail=f"--accent must look like #2f81f7, got {value!r}")
    return value


# ---------------------------------------------------------------- plan


def plan_text(ptype: str, which=shutil.which) -> str:
    have = {t: bool(which(t)) for t in TOOLS}
    out = [f"# Visuals plan (type: {ptype})", "",
           "## GIF storyboard (first 5 seconds)", "",
           f"- 0 s: {STORYBOARDS[ptype][0]}",
           f"- 1-3 s: {STORYBOARDS[ptype][1]}",
           f"- 3-5 s: {STORYBOARDS[ptype][2]}",
           "- Optional rest, 20 s in total at most.", "",
           "Rules: a 1280x720 or 960x540 window, no personal data or tokens on screen, a "
           "high-contrast theme, no idle frame over 1 s, a clean loop.", "",
           "## Record", ""]
    rec = []
    if have["vhs"]:
        rec.append("- vhs (scripted terminal). Save this as demo.tape, then run `vhs demo.tape`:"
                   "\n\n  ```\n  Output demo.gif\n  Set FontSize 28\n  Set Width 1280\n"
                   "  Set Height 720\n  Type \"TODO(the command to show)\"\n  Enter\n"
                   "  Sleep 3s\n  ```")
    if have["asciinema"]:
        rec.append("- asciinema: `asciinema rec demo.cast`"
                   + (", then `agg demo.cast demo.gif`." if have["agg"] else "."))
    if have["screencapture"]:
        rec.append("- macOS: `screencapture -v demo.mov` (stop with Ctrl-C).")
    if have["wf-recorder"]:
        rec.append("- Wayland: `wf-recorder -g \"$(slurp)\" -f demo.mp4`.")
    out += rec or ["- No recorder found on this machine. Use your OS screen recorder."]
    out += ["", "## Compress to under 5 MB", ""]
    if have["ffmpeg"]:
        out.append("`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/visuals.py compress demo.mov "
                   "--out demo.gif`" + (" (gifski found: used first)." if have["gifski"]
                                         else "."))
    else:
        out.append("ffmpeg is needed for compress.")
    missing = [f"- {t}: {INSTALL_HINTS[t]}" for t in ("ffmpeg", "gifski", "vhs")
               if not have[t]]
    if missing:
        out += ["", "## Missing tools", "", *missing]
    return "\n".join(out)


# ---------------------------------------------------------------- compress


def gif_info(path) -> tuple:
    """(width, height) read from a GIF header."""
    with open(path, "rb") as f:
        head = f.read(10)
    if head[:3] != b"GIF":
        raise common.RLError("E_BAD_ARGS", detail=f"{path} is not a GIF")
    return struct.unpack("<HH", head[6:10])


def _ladder(width: int, fps: int) -> list:
    out = []
    for step in ((width, fps), (960, 12), (800, 10), (640, 10), (640, 8)):
        if step not in out:
            out.append(step)
    return out


def _encode(method: str, inp: str, cand: Path, w: int, f: int, tmp: Path, run) -> None:
    scale = f"fps={f},scale={w}:-1:flags=lanczos"
    if method == "gifski":
        frames = tmp / f"frames-{w}-{f}"
        frames.mkdir()
        p = run(["ffmpeg", "-y", "-i", inp, "-vf", scale, str(frames / "%05d.png")])
        if p.returncode != 0:
            raise common.RLError("E_BAD_ARGS", detail=f"ffmpeg failed: {_last_line(p.stderr)}")
        pngs = sorted(str(x) for x in frames.glob("*.png"))
        p = run(["gifski", "--fps", str(f), "--width", str(w), "--quality", "80",
                 "-o", str(cand), *pngs])
    else:
        vf = (f"{scale},split[a][b];[a]palettegen=stats_mode=diff[p];"
              f"[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
        p = run(["ffmpeg", "-y", "-i", inp, "-vf", vf, str(cand)])
    if p.returncode != 0:
        raise common.RLError("E_BAD_ARGS", detail=f"{method} failed: {_last_line(p.stderr)}")


def compress(inp, out, max_mb: float = 5, width: int = 960, fps: int = 12,
             overwrite: bool = False, which=shutil.which, run=_run) -> dict:
    """Shrink `inp` to a GIF of at most max_mb. Returns the result record."""
    if not Path(inp).is_file():
        raise common.RLError("E_BAD_ARGS", detail=f"{inp} does not exist")
    dest = _check_out(out, overwrite)
    if not which("ffmpeg"):
        raise common.RLError("E_TOOL_MISSING", tool="ffmpeg", install_hint=INSTALL_HINTS["ffmpeg"])
    methods = (["gifski"] if which("gifski") else []) + ["ffmpeg"]
    limit = int(max_mb * 1024 * 1024)
    best = None
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for w, f in _ladder(width, fps):
            for method in methods:
                cand = tmp / f"{method}-{w}-{f}.gif"
                try:
                    _encode(method, str(inp), cand, w, f, tmp, run)
                except common.RLError as e:
                    common.err(f"leadout: warning: {e.message}")
                    continue
                size = cand.stat().st_size
                if best is None or size < best["bytes"]:
                    best = {"bytes": size, "path": cand, "width": w, "fps": f, "method": method}
                if size <= limit:
                    break
            if best and best["bytes"] <= limit:
                break
        if best is None:
            raise common.RLError("E_BAD_ARGS", detail=f"could not convert {inp} to a GIF")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(best["path"], dest)
    w, h = gif_info(dest)
    return {"path": str(dest), "bytes": best["bytes"], "width": w, "height": h,
            "fps": best["fps"], "method": best["method"], "within_limit": best["bytes"] <= limit}


# ---------------------------------------------------------------- SVG


def _tpl(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _fit_title(title: str, avail: int, top: int, floor: int) -> tuple:
    """(title, font size) so the title fits `avail` px, using a 0.6 em average glyph width."""
    # ponytail: width estimate, no font metrics
    size = max(floor, min(top, int(avail / (0.6 * max(1, len(title))))))
    max_chars = int(avail / (0.6 * size))
    return (title if len(title) <= max_chars else title[:max_chars - 1] + "…"), size


def social_preview_svg(facts: dict, detection: dict, title: str | None = None,
                       tagline: str | None = None, theme: str = "light",
                       accent: str = "#2f81f7"):
    """Return (svg text, todos) for the 1280x640 social preview."""
    repo = facts.get("repo") or {}
    title = title or common.repo_name(facts) or ""
    tagline = tagline if tagline is not None else (repo.get("description") or "")
    todos = [n for n, v in (("title", title), ("tagline", tagline)) if not v]
    title_fit, size = _fit_title(title or "TODO(title)", 1100, 104, 40)
    # ponytail: width estimate, no font metrics; 40 px text is about 22 px per character
    lines = textwrap.wrap(tagline or "TODO(tagline)", width=45)
    if len(lines) > 3:
        lines = lines[:3]
        lines[2] = lines[2][:44].rstrip() + "…"
    tspans = "".join(f'<tspan x="80" y="{320 + 56 * i}">{escape(ln)}</tspan>'
                     for i, ln in enumerate(lines))
    meta = " · ".join(x for x in (TYPE_LABELS[detection["type"]], detection.get("language")) if x)
    values = {**THEMES[theme], "accent": accent, "title": escape(title_fit, {'"': "&quot;"}),
              "title_size": size, "title_y": 220, "tagline_lines": tspans,
              "slug": escape(common.slug_of(facts) or common.repo_name(facts) or "TODO(slug)"),
              "meta_line": escape(meta)}
    return common.render(_tpl("social-preview.svg"), values)[0], todos


def banner_svg(facts: dict, title: str | None, tagline: str | None, theme: str,
               accent: str) -> str:
    repo = facts.get("repo") or {}
    title = title or common.repo_name(facts) or "TODO(title)"
    tagline = tagline if tagline is not None else (repo.get("description") or "TODO(tagline)")
    title_fit, size = _fit_title(title, 1100, 72, 36)
    # ponytail: width estimate, no font metrics; 32 px text is about 18 px per character
    if len(tagline) > 62:
        tagline = tagline[:61].rstrip() + "…"
    values = {**THEMES[theme], "accent": accent, "title": escape(title_fit, {'"': "&quot;"}),
              "title_size": size, "tagline": escape(tagline),
              "slug": escape(common.slug_of(facts) or common.repo_name(facts) or "TODO(slug)")}
    return common.render(_tpl("banner.svg"), values)[0]


def picture_snippet(directory: str, alt: str) -> str:
    alt = escape(alt, {'"': "&quot;"})
    dark = f"{directory}/banner-dark.svg"
    light = f"{directory}/banner-light.svg"
    return ("<picture>\n"
            f'  <source media="(prefers-color-scheme: dark)" srcset="{dark}">\n'
            f'  <img alt="{alt}" src="{light}" width="100%">\n'
            "</picture>")


# ---------------------------------------------------------------- SVG to PNG


def svg_size(text: str) -> tuple:
    head = re.search(r"<svg\b[^>]*>", text)
    tag = head.group(0) if head else ""
    w = re.search(r'\bwidth="(\d+(?:\.\d+)?)"', tag)
    h = re.search(r'\bheight="(\d+(?:\.\d+)?)"', tag)
    if w and h:
        return int(float(w.group(1))), int(float(h.group(1)))
    vb = re.search(r'viewBox="[\d.\s-]*?([\d.]+)\s+([\d.]+)"', tag)
    return (int(float(vb.group(1))), int(float(vb.group(2)))) if vb else (1280, 640)


def png_size(path) -> tuple:
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise common.RLError("E_BAD_ARGS", detail=f"{path} is not a PNG")
    return struct.unpack(">II", head[16:24])


def _chrome(which) -> str | None:
    for n in CHROME_NAMES:
        if which(n):
            return which(n)
    return next((p for p in CHROME_MAC if os.path.exists(p)), None)


def to_png(inp, out, width: int | None = None, height: int | None = None,
           overwrite: bool = False, which=shutil.which, run=_run) -> dict:
    src = Path(inp).resolve()
    if not src.is_file():
        raise common.RLError("E_BAD_ARGS", detail=f"{inp} does not exist")
    dest = _check_out(out, overwrite)
    sw, sh = svg_size(src.read_text(encoding="utf-8"))
    w, h = width or sw, height or sh
    converters = []
    if which("rsvg-convert"):
        converters.append(("rsvg-convert", lambda o: ["rsvg-convert", "-w", str(w), "-h", str(h),
                                                       "-o", o, str(src)]))
    if which("inkscape"):
        converters.append(("inkscape", lambda o: ["inkscape", str(src), "--export-type=png",
                                                   f"--export-filename={o}", "-w", str(w),
                                                   "-h", str(h)]))
    for tool in ("magick", "convert"):
        if which(tool):
            converters.append((tool, lambda o, tool=tool: [tool, "-background", "none",
                                                           "-density", "192", str(src),
                                                           "-resize", f"{w}x{h}!", o]))
            break
    chrome = _chrome(which)
    if chrome:
        converters.append(("chrome", lambda o: [chrome, "--headless", "--disable-gpu",
                                                "--hide-scrollbars", f"--screenshot={o}",
                                                f"--window-size={w},{h}", src.as_uri()]))
    if not converters:
        common.err("Manual options: open the SVG in a browser and export a "
                   f"{w}x{h} PNG, or use any design tool.")
        raise common.RLError("E_TOOL_MISSING", tool="An SVG converter",
                             install_hint="Install one of: rsvg-convert (brew install librsvg), "
                                          "inkscape, ImageMagick, or Google Chrome.")
    problems = []
    with tempfile.TemporaryDirectory() as t:
        tmp_png = str(Path(t) / "out.png")
        for name, build in converters:
            try:
                p = run(build(tmp_png))
            except common.RLError as e:
                problems.append(f"{name}: {e.message}")
                continue
            if p.returncode != 0 or not os.path.exists(tmp_png):
                problems.append(f"{name} failed: {_last_line(p.stderr)}")
                continue
            if png_size(tmp_png) != (w, h):
                problems.append(f"{name} produced {png_size(tmp_png)}, not {w}x{h}")
                os.remove(tmp_png)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tmp_png, dest)
            return {"path": str(dest), "width": w, "height": h, "converter": name}
    raise common.RLError("E_BAD_ARGS", detail="no converter produced a valid PNG ("
                                              + "; ".join(problems) + ")")


# ---------------------------------------------------------------- commands


def cmd_plan(args) -> int:
    _f, detection = _context(args)
    common.out(plan_text(detection["type"]))
    return 0


def cmd_compress(args) -> int:
    r = compress(args.input, args.out, args.max_mb, args.width, args.fps, args.overwrite)
    mb = r["bytes"] / (1024 * 1024)
    common.out(f"{r['path']}: {mb:.2f} MB, {r['width']}x{r['height']}, {r['fps']} fps "
               f"({r['method']})")
    if not r["within_limit"]:
        common.err(f"leadout: warning: still above {args.max_mb} MB. Trim the recording "
                   "to the first 5-10 seconds and run compress again.")
    return 0


def cmd_social(args) -> int:
    facts, detection = _context(args)
    dest = _check_out(args.out, args.overwrite)
    svg, todos = social_preview_svg(facts, detection, args.title, args.tagline, args.theme,
                                    _accent(args.accent))
    common.write_new_file(dest, svg, overwrite=args.overwrite)
    common.out(f"wrote {dest} (1280x640). Next: visuals.py to-png {dest} --out "
               f"{dest.with_suffix('.png')}, then upload the PNG at Settings > Social preview "
               f"(there is no API for it).")
    if todos:
        common.out("todos: " + ", ".join(todos) + " (pass --title or --tagline)")
    return 0


def cmd_banner(args) -> int:
    facts, _d = _context(args)
    out_dir = Path(args.out_dir)
    paths = {t: out_dir / f"banner-{t}.svg" for t in ("light", "dark")}
    for p in paths.values():
        _check_out(p, args.overwrite)
    accent = _accent(args.accent)
    for theme, p in paths.items():
        common.write_new_file(p, banner_svg(facts, args.title, args.tagline, theme, accent),
                              overwrite=args.overwrite)
        common.out(f"wrote {p} (1280x320)")
    repo = facts.get("repo") or {}
    name = args.title or common.repo_name(facts)
    alt = f"{name}: {args.tagline or repo.get('description') or ''}".rstrip(": ")
    common.out("\nREADME snippet (insert it through repo-fix, with a diff and confirmation):\n")
    common.out(picture_snippet(out_dir.as_posix(), alt))
    return 0


def cmd_to_png(args) -> int:
    if bool(args.width) != bool(args.height):
        raise common.RLError("E_BAD_ARGS", detail="pass --width and --height together")
    r = to_png(args.input, args.out, args.width, args.height, args.overwrite)
    common.out(f"wrote {r['path']} ({r['width']}x{r['height']}, {r['converter']})")
    return 0


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="visuals.py", description="Plan and produce repository visuals.",
        epilog="Example: python3 scripts/visuals.py social-preview . --out social.svg")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def target_args(p):
        p.add_argument("target", nargs="?", default=".", help="path, GitHub URL or owner/repo")
        p.add_argument("--type", help="override the detected project type")
        p.add_argument("--offline", action="store_true", help="no gh calls")
        return p

    def overwrite(p):
        p.add_argument("--overwrite", action="store_true", help="replace existing files")
        return p

    target_args(sub.add_parser("plan", help="storyboard and commands"))
    p = overwrite(sub.add_parser("compress", help="recording to a small GIF"))
    p.add_argument("input")
    p.add_argument("--out", required=True)
    p.add_argument("--max-mb", type=float, default=5)
    p.add_argument("--width", type=int, default=960)
    p.add_argument("--fps", type=int, default=12)
    p = overwrite(target_args(sub.add_parser("social-preview", help="1280x640 SVG")))
    p.add_argument("--out", required=True)
    p.add_argument("--theme", choices=sorted(THEMES), default="light")
    p = overwrite(target_args(sub.add_parser("banner", help="light and dark banners")))
    p.add_argument("--out-dir", required=True)
    for p in (sub.choices["social-preview"], sub.choices["banner"]):
        p.add_argument("--title")
        p.add_argument("--tagline")
        p.add_argument("--accent", default="#2f81f7")
    p = overwrite(sub.add_parser("to-png", help="SVG to PNG"))
    p.add_argument("input")
    p.add_argument("--out", required=True)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    args = ap.parse_args(argv)
    return {"plan": cmd_plan, "compress": cmd_compress, "social-preview": cmd_social,
            "banner": cmd_banner, "to-png": cmd_to_png}[args.cmd](args)


def main(argv: list[str] | None = None) -> int:
    return common.run_main(_main, argv)


if __name__ == "__main__":
    sys.exit(main())
