"""Rubric loading and validation, scoring, coverage, grades, rounding, fix ordering."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

import helpers
import audit
import common

BASE = helpers.load_facts("base")


def chk(cid, kind, weight, effort="low", **extra):
    return {"id": cid, "category": "x", "type": kind, "title": cid, "weight": weight,
            "effort": effort, "phase": "fix", "fix": f"Fix {cid}.", **extra}


def mini(*checks):
    r = {"schema": 1, "name": "test", "version": "0.0.1",
         "effort_levels": {"low": 1, "medium": 2, "high": 3},
         "grades": [[90, "A"], [75, "B"], [60, "C"], [40, "D"], [0, "F"]],
         "categories": [{"id": "x", "title": "X"}], "checks": list(checks)}
    audit.validate_rubric(r)
    return r


def facts_with(**changes):
    f = helpers.deep(BASE)
    for key, value in changes.items():
        if "__" in key:
            a, b = key.split("__")
            f[a][b] = value
        else:
            f[key] = value
    return f


class DefaultRubricTests(unittest.TestCase):
    def setUp(self):
        self.rubric = audit.load_rubric(helpers.BUNDLED_RUBRIC)

    def test_weights_and_categories(self):
        checks = self.rubric["checks"]
        self.assertEqual(sum(c["weight"] for c in checks), 100)
        totals = {}
        for c in checks:
            totals[c["category"]] = totals.get(c["category"], 0) + c["weight"]
        self.assertEqual(totals, {"readme": 35, "metadata": 20, "distribution": 15,
                                  "community": 15, "health": 15})
        self.assertEqual(len(checks), 24)
        self.assertEqual(len(self.rubric["_sha256"]), 64)

    def test_base_facts_score(self):
        r = audit.evaluate(BASE, self.rubric)
        self.assertEqual(r["project_type"], "cli")
        self.assertEqual(r["coverage"], 100)
        self.assertEqual(r["score"], 95)  # only readme.length (5 points) fails
        self.assertEqual([f["id"] for f in r["fixes"]], ["readme.length"])
        self.assertAlmostEqual(sum(c["points"] for c in r["categories"]), 95.0, places=1)

    def test_json_is_byte_identical(self):
        a = common.dumps(audit.evaluate(helpers.deep(BASE), self.rubric))
        b = common.dumps(audit.evaluate(helpers.deep(BASE), audit.load_rubric(
            helpers.BUNDLED_RUBRIC)))
        self.assertEqual(a, b)

    def test_type_override_skips(self):
        r = audit.evaluate(BASE, self.rubric, "website")
        skipped = {c["id"] for c in r["checks"] if c["status"] == "skip"}
        self.assertEqual(skipped, {"readme.install", "dist.release", "dist.semver"})
        self.assertTrue(r["detection"]["overridden"])
        self.assertIn("set with --type", audit.render_markdown(r))


class ValidationTests(unittest.TestCase):
    def assertInvalid(self, rubric, needle):
        with self.assertRaises(common.RLError) as cm:
            audit.validate_rubric(rubric, "r.json")
        self.assertEqual(cm.exception.code, "E_RUBRIC_INVALID")
        self.assertIn(needle, cm.exception.message)
        self.assertEqual(cm.exception.exit_code, 2)

    def base(self):
        return mini(chk("x.one", "meta_homepage", 1), chk("x.two", "readme_exists", 1))

    def test_invalid_rubrics(self):
        cases = [
            (lambda r: r["checks"].append(dict(r["checks"][0])), "duplicate check id"),
            (lambda r: r["checks"][0].update(type="nope"), "unknown type"),
            (lambda r: r["checks"][0].update(weight=0), "weight"),
            (lambda r: r["checks"][0].update(weight=-2), "weight"),
            (lambda r: r["checks"][0].update(weight=True), "weight"),
            (lambda r: r["checks"][0].update(category="zzz"), "unknown category"),
            (lambda r: r["checks"][0].update(params={"colour": 1}), "unknown param"),
            (lambda r: r["checks"][0].update(effort="huge"), "effort"),
            (lambda r: r["checks"][0].update(phase="later"), "phase"),
            (lambda r: r["checks"][0].update(id="Bad Id"), "check id"),
            (lambda r: r["checks"][0].update(skip_types=["mobile"]), "skip_types"),
            (lambda r: r.update(schema=2), "schema"),
            (lambda r: r.update(checks=[]), "checks"),
        ]
        for mutate, needle in cases:
            r = self.base()
            mutate(r)
            with self.subTest(needle=needle):
                self.assertInvalid(r, needle)

    def test_param_types_and_comm_file(self):
        r = mini(chk("x.tag", "readme_tagline", 1))
        r["checks"][0]["params"] = {"max_lines": "15"}
        self.assertInvalid(r, "wrong type")
        r = mini(chk("x.f", "comm_file", 1))
        r["checks"][0]["params"] = {"file": "funding"}
        self.assertInvalid(r, "file must be one of")

    def test_invalid_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "r.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{not json")
            with self.assertRaises(common.RLError) as cm:
                audit.load_rubric(p)
            self.assertEqual(cm.exception.code, "E_RUBRIC_INVALID")
            with self.assertRaises(common.RLError):
                audit.load_rubric(os.path.join(tmp, "missing.json"))


class ScoringTests(unittest.TestCase):
    def test_skip_is_excluded(self):
        r = audit.evaluate(facts_with(repo__description=""), mini(
            chk("x.home", "meta_homepage", 2), chk("x.desc", "meta_description", 2),
            chk("x.inst", "readme_install", 6, skip_types=["cli"])))
        self.assertEqual((r["score"], r["coverage"]), (50, 100))
        self.assertEqual(r["checks"][2]["status"], "skip")
        self.assertEqual(r["checks"][2]["points"], 0.0)

    def test_unknown_lowers_coverage_not_score(self):
        f = facts_with(repo=None, unavailable={"repo": "offline"})
        r = audit.evaluate(f, mini(chk("x.exists", "readme_exists", 1),
                                   chk("x.home", "meta_homepage", 3)))
        self.assertEqual((r["score"], r["coverage"]), (100, 25))
        md = audit.render_markdown(r)
        self.assertIn("unknown: offline", md)
        self.assertIn("Coverage is below 100%", md)
        none = audit.evaluate(f, mini(chk("x.home", "meta_homepage", 3)))
        self.assertEqual((none["score"], none["coverage"], none["grade"]), (0, 0, "F"))

    def test_grade_thresholds(self):
        f = facts_with(repo__homepage="")
        for pass_w, fail_w, score, grade in ((90, 10, 90, "A"), (89, 11, 89, "B"),
                                             (60, 40, 60, "C"), (39, 61, 39, "F")):
            r = audit.evaluate(f, mini(chk("x.exists", "readme_exists", pass_w),
                                       chk("x.home", "meta_homepage", fail_w)))
            self.assertEqual((r["score"], r["grade"]), (score, grade))

    def test_round_half_up(self):
        self.assertEqual(common.round_half_up(2.5), 3)
        self.assertEqual(common.round_half_up(72.5), 73)
        self.assertEqual(common.round_half_up(0.125, 2), 0.13)
        self.assertEqual(common.round_half_up(0.665, 2), 0.67)
        f = facts_with(ci__latest_default_branch_run={"conclusion": "failure",
                                                      "created_at": "2026-11-20T00:00:00Z"})
        r = audit.evaluate(f, mini(chk("x.exists", "readme_exists", 1),
                                   chk("x.ci", "health_ci", 1)))
        self.assertEqual(r["score"], 63)  # 100 * 1.25 / 2 = 62.5

    def test_fix_order_and_tie_breaks(self):
        f = facts_with(files=["README.md"], community=None)
        r = audit.evaluate(f, mini(
            chk("comm.b", "comm_file", 2, params={"file": "contributing"}),
            chk("comm.a", "comm_file", 2, params={"file": "security"}),
            chk("health.ci", "health_ci", 4, effort="high"),
            chk("readme.exists", "readme_exists", 4, effort="medium")))
        self.assertEqual([x["id"] for x in r["fixes"]], ["comm.a", "comm.b", "health.ci"])
        self.assertEqual([x["gain"] for x in r["fixes"]], [16.7, 16.7, 33.3])
        self.assertEqual(r["fixes"][0]["text"], "Fix comm.a. No SECURITY.md found.")
        self.assertEqual(r["score"], 33)

    def test_disabled_checks_are_dropped(self):
        r = audit.evaluate(BASE, mini(chk("x.exists", "readme_exists", 1),
                                      chk("x.home", "meta_homepage", 1, enabled=False)))
        self.assertEqual([c["id"] for c in r["checks"]], ["x.exists"])

    def test_markdown_report_shape(self):
        md = audit.render_markdown(audit.evaluate(BASE, audit.load_rubric(
            helpers.BUNDLED_RUBRIC)))
        self.assertTrue(md.startswith("# Launch readiness: acme/widget: 95/100 (A)\n"))
        self.assertIn("type: cli (detected: Cargo binary)", md)
        self.assertIn("| README | 30.0 / 35.0 |", md)
        self.assertIn("| 1 | Bring the README", md)
        self.assertIn("| ❌ | README length", md)

    def test_warnings(self):
        f = facts_with(repo__private=True, files_truncated=True)
        codes = [w["code"] for w in audit.evaluate(f, mini(chk("x.e", "readme_exists", 1)))[
            "warnings"]]
        self.assertEqual(codes, ["W_PRIVATE", "W_TREE_TRUNCATED"])


if __name__ == "__main__":
    unittest.main()
