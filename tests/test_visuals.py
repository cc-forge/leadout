"""Tests for scripts/visuals.py: plan, compress ladder, SVG generation, SVG to PNG."""
import re
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import helpers
from helpers import deep, load_facts, run_cli

import common
import visuals

CLI = {"type": "cli", "language": "Rust", "evidence": [], "frameworks": []}


def fake_which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def gif_bytes(w, h, size):
    return (b"GIF89a" + struct.pack("<HH", w, h)).ljust(size, b"\0")


def png_bytes(w, h):
    return b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", w, h) + b"\0" * 8


class PlanTests(unittest.TestCase):
    def test_storyboard_per_type(self):
        for ptype, steps in visuals.STORYBOARDS.items():
            text = visuals.plan_text(ptype, which=fake_which())
            self.assertIn(f"type: {ptype}", text)
            self.assertIn(steps[0], text)

    def test_only_present_tools_get_commands(self):
        text = visuals.plan_text("cli", which=fake_which("ffmpeg", "screencapture"))
        self.assertIn("screencapture -v demo.mov", text)
        self.assertIn("visuals.py compress", text)
        self.assertNotIn("vhs demo.tape", text)
        self.assertIn("gifski: brew install gifski", text)
        self.assertIn("vhs: brew install vhs", text)
        self.assertNotIn("ffmpeg: brew", text)

    def test_vhs_tape_when_present(self):
        text = visuals.plan_text("cli", which=fake_which("vhs", "ffmpeg", "gifski"))
        self.assertIn("Output demo.gif", text)
        self.assertIn("gifski found", text)

    def test_no_recorder(self):
        self.assertIn("No recorder found", visuals.plan_text("cli", which=fake_which()))
        self.assertIn("ffmpeg is needed", visuals.plan_text("cli", which=fake_which()))


class CompressTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.src = self.tmp / "in.mov"
        self.src.write_bytes(b"x")
        self.calls = []

    def tearDown(self):
        self._t.cleanup()

    def runner(self, sizes):
        """Fake ffmpeg/gifski: each GIF-producing call writes the next size in `sizes`."""
        sizes = list(sizes)

        def run(cmd, timeout=600):
            self.calls.append(cmd)
            if cmd[0] == "ffmpeg" and cmd[-1].endswith("%05d.png"):
                Path(cmd[-1].replace("%05d", "00001")).write_bytes(b"png")
            elif cmd[0] in ("ffmpeg", "gifski"):
                out = Path(cmd[cmd.index("-o") + 1] if cmd[0] == "gifski" else cmd[-1])
                m = re.search(r"scale=(\d+)", " ".join(cmd))
                w = int(m.group(1)) if m else int(cmd[cmd.index("--width") + 1])
                out.write_bytes(gif_bytes(w, w // 2, sizes.pop(0)))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return run

    def test_first_attempt_fits(self):
        r = visuals.compress(self.src, self.tmp / "o.gif", which=fake_which("ffmpeg"),
                             run=self.runner([1000]))
        self.assertTrue(r["within_limit"])
        self.assertEqual((r["width"], r["fps"], r["method"]), (960, 12, "ffmpeg"))
        self.assertEqual(visuals.gif_info(self.tmp / "o.gif"), (960, 480))
        self.assertEqual(len(self.calls), 1)
        self.assertIn("palettegen", self.calls[0][5])

    def test_ladder_steps_down(self):
        big = 6 * 1024 * 1024
        r = visuals.compress(self.src, self.tmp / "o.gif", which=fake_which("ffmpeg"),
                             run=self.runner([big, big, 1000]))
        self.assertEqual((r["width"], r["fps"]), (640, 10))
        self.assertEqual(len(self.calls), 3)  # 960x12, 800x10, 640x10

    def test_never_fits_keeps_smallest(self):
        big = 6 * 1024 * 1024
        sizes = [big + i for i in range(4, 0, -1)]
        r = visuals.compress(self.src, self.tmp / "o.gif", which=fake_which("ffmpeg"),
                             run=self.runner(sizes))
        self.assertFalse(r["within_limit"])
        self.assertEqual(r["bytes"], big + 1)
        self.assertEqual(len(self.calls), 4)

    def test_gifski_is_tried_first(self):
        r = visuals.compress(self.src, self.tmp / "o.gif", which=fake_which("ffmpeg", "gifski"),
                             run=self.runner([1000]))
        self.assertEqual(r["method"], "gifski")
        self.assertEqual([c[0] for c in self.calls], ["ffmpeg", "gifski"])

    def test_gifski_too_big_falls_back_to_ffmpeg_palette(self):
        big = 6 * 1024 * 1024
        r = visuals.compress(self.src, self.tmp / "o.gif", which=fake_which("ffmpeg", "gifski"),
                             run=self.runner([big, 1000]))
        self.assertEqual(r["method"], "ffmpeg")

    def test_errors(self):
        out = self.tmp / "o.gif"
        with self.assertRaises(common.RLError) as e:
            visuals.compress(self.src, out, which=fake_which())
        self.assertEqual(e.exception.code, "E_TOOL_MISSING")
        with self.assertRaises(common.RLError) as e:
            visuals.compress(self.tmp / "missing.mov", out, which=fake_which("ffmpeg"))
        self.assertEqual(e.exception.code, "E_BAD_ARGS")
        out.write_bytes(b"old")
        with self.assertRaises(common.RLError) as e:
            visuals.compress(self.src, out, which=fake_which("ffmpeg"), run=self.runner([1]))
        self.assertEqual(e.exception.code, "E_FILE_EXISTS")
        self.assertEqual(out.read_bytes(), b"old")

    def test_ffmpeg_failure_is_reported(self):
        def run(cmd, timeout=600):
            return SimpleNamespace(returncode=1, stdout="", stderr="bad input\n")
        with self.assertRaises(common.RLError) as e:
            visuals.compress(self.src, self.tmp / "o.gif", which=fake_which("ffmpeg"), run=run)
        self.assertIn("could not convert", e.exception.message)


class SvgTests(unittest.TestCase):
    def setUp(self):
        self.facts = deep(load_facts("base"))

    def test_social_preview_is_valid_xml_with_facts(self):
        svg, todos = visuals.social_preview_svg(self.facts, CLI)
        root = ET.fromstring(svg)
        self.assertEqual((root.get("width"), root.get("height")), ("1280", "640"))
        self.assertIn("widget", svg)
        self.assertIn("acme/widget", svg)
        self.assertIn("Command-line tool · Rust", svg)
        self.assertNotIn("[[", svg)
        self.assertEqual(todos, [])

    def test_text_is_escaped(self):
        svg, _ = visuals.social_preview_svg(self.facts, CLI, title='A&B <x> "q"',
                                            tagline="1 < 2 & 3 > 2")
        ET.fromstring(svg)
        self.assertIn("A&amp;B &lt;x&gt;", svg)
        self.assertIn("1 &lt; 2 &amp; 3 &gt; 2", svg)

    def test_tagline_wraps_to_three_lines_and_truncates(self):
        svg, _ = visuals.social_preview_svg(self.facts, CLI, tagline="word " * 100)
        self.assertEqual(svg.count("<tspan"), 3)
        self.assertIn("…", svg)

    def test_missing_tagline_is_a_visible_todo(self):
        facts = deep(self.facts)
        facts["repo"]["description"] = ""
        svg, todos = visuals.social_preview_svg(facts, CLI)
        self.assertIn("TODO(tagline)", svg)
        self.assertEqual(todos, ["tagline"])

    def test_long_title_shrinks_and_dark_theme(self):
        svg, _ = visuals.social_preview_svg(self.facts, CLI, title="x" * 60, theme="dark")
        size = int(re.search(r'font-size="(\d+)" font-weight="700"', svg).group(1))
        self.assertEqual(size, 40)
        self.assertIn("…", svg)
        self.assertIn("#0d1117", svg)

    def test_safe_margin(self):
        svg, _ = visuals.social_preview_svg(self.facts, CLI)
        for x in re.findall(r'<(?:text|tspan) x="(\d+)"', svg):
            self.assertGreaterEqual(int(x), 40)

    def test_banner_and_snippet(self):
        for theme, bg in (("light", "#ffffff"), ("dark", "#0d1117")):
            svg = visuals.banner_svg(self.facts, None, None, theme, "#2f81f7")
            root = ET.fromstring(svg)
            self.assertEqual((root.get("width"), root.get("height")), ("1280", "320"))
            self.assertIn(bg, svg)
        snip = visuals.picture_snippet("assets", 'widget: "fast" charts')
        self.assertIn('srcset="assets/banner-dark.svg"', snip)
        self.assertIn('alt="widget: &quot;fast&quot; charts"', snip)

    def test_cli_writes_new_files_only(self):
        with tempfile.TemporaryDirectory() as t:
            repo = helpers.make_repo(Path(t) / "r", {"Cargo.toml": "[package]\nname=\"d\"\n",
                                                     "src/main.rs": "fn main(){}\n"})
            out = Path(t) / "sp.svg"
            args = ["social-preview", str(repo), "--offline", "--out", str(out),
                    "--tagline", "Short tagline"]
            code, stdout, _e = run_cli(visuals.main, args)
            self.assertEqual(code, 0)
            self.assertIn("no API", stdout)
            code, _o, err = run_cli(visuals.main, args)
            self.assertEqual(code, 4)
            self.assertIn("already exists", err)
            self.assertEqual(run_cli(visuals.main, args + ["--overwrite"])[0], 0)
            code, _o, err = run_cli(visuals.main, args + ["--accent", "red", "--overwrite"])
            self.assertEqual(code, 2)
            code, stdout, _e = run_cli(visuals.main, ["banner", str(repo), "--offline",
                                                      "--out-dir", str(Path(t) / "b"),
                                                      "--tagline", "Hi"])
            self.assertEqual(code, 0)
            self.assertTrue((Path(t) / "b" / "banner-dark.svg").exists())
            self.assertIn("<picture>", stdout)


class ToPngTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.svg = self.tmp / "a.svg"
        self.svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" '
                            'viewBox="0 0 1280 640"></svg>')
        self.no_chrome = mock.patch.object(visuals, "CHROME_MAC", ())
        self.no_chrome.start()

    def tearDown(self):
        self.no_chrome.stop()
        self._t.cleanup()

    def converter(self, w, h, code=0, calls=None):
        def run(cmd, timeout=600):
            if calls is not None:
                calls.append(cmd[0])
            out = next((c.split("=", 1)[1] for c in cmd
                        if c.startswith(("--screenshot=", "--export-filename="))), None)
            if out is None:
                out = cmd[cmd.index("-o") + 1] if "-o" in cmd else cmd[-1]
            if code == 0:
                Path(out).write_bytes(png_bytes(w, h))
            return SimpleNamespace(returncode=code, stdout="", stderr="boom\n")
        return run

    def test_svg_size_from_attrs_and_viewbox(self):
        self.assertEqual(visuals.svg_size(self.svg.read_text()), (1280, 640))
        self.assertEqual(visuals.svg_size('<svg viewBox="0 0 800 200">'), (800, 200))
        self.assertEqual(visuals.svg_size("nothing"), (1280, 640))

    def test_converts_and_verifies_size(self):
        r = visuals.to_png(self.svg, self.tmp / "o.png", which=fake_which("rsvg-convert"),
                           run=self.converter(1280, 640))
        self.assertEqual((r["width"], r["height"], r["converter"]), (1280, 640, "rsvg-convert"))
        self.assertEqual(visuals.png_size(self.tmp / "o.png"), (1280, 640))

    def test_wrong_size_tries_next_converter(self):
        calls = []
        sizes = {"rsvg-convert": (10, 10)}

        def run(cmd, timeout=600):
            calls.append(cmd[0])
            w, h = sizes.get(cmd[0], (1280, 640))
            return self.converter(w, h)(cmd)
        r = visuals.to_png(self.svg, self.tmp / "o.png",
                           which=fake_which("rsvg-convert", "inkscape"), run=run)
        self.assertEqual(calls, ["rsvg-convert", "inkscape"])
        self.assertEqual(r["converter"], "inkscape")

    def test_all_fail(self):
        with self.assertRaises(common.RLError) as e:
            visuals.to_png(self.svg, self.tmp / "o.png", which=fake_which("rsvg-convert"),
                           run=self.converter(1, 1, code=1))
        self.assertIn("no converter produced a valid PNG", e.exception.message)
        self.assertFalse((self.tmp / "o.png").exists())

    def test_no_converter(self):
        with self.assertRaises(common.RLError) as e:
            visuals.to_png(self.svg, self.tmp / "o.png", which=fake_which())
        self.assertEqual(e.exception.code, "E_TOOL_MISSING")
        self.assertIn("rsvg-convert", e.exception.hint)

    def test_refuses_existing_output(self):
        (self.tmp / "o.png").write_bytes(b"old")
        with self.assertRaises(common.RLError) as e:
            visuals.to_png(self.svg, self.tmp / "o.png", which=fake_which("rsvg-convert"),
                           run=self.converter(1280, 640))
        self.assertEqual(e.exception.code, "E_FILE_EXISTS")

    def test_custom_size_and_cli_pairing(self):
        r = visuals.to_png(self.svg, self.tmp / "o.png", 640, 320,
                           which=fake_which("rsvg-convert"), run=self.converter(640, 320))
        self.assertEqual((r["width"], r["height"]), (640, 320))
        code, _o, err = run_cli(visuals.main, ["to-png", str(self.svg), "--out",
                                               str(self.tmp / "x.png"), "--width", "10"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
