"""Detect a repository's project type and primary language (SPEC section 7.3).

Types, first match wins: claude-plugin, desktop-app, website, cli, library, other.

Usage:
    python3 scripts/detect.py [TARGET] [--offline] [--json]

Example:
    python3 scripts/detect.py . --offline --json
"""
from __future__ import annotations

import argparse
import re
import sys

import common
import facts as facts_mod

TYPES = ("claude-plugin", "desktop-app", "website", "cli", "library", "other")

LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go",
    ".rs": "Rust", ".rb": "Ruby", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".swift": "Swift", ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".cs": "C#", ".php": "PHP", ".dart": "Dart", ".lua": "Lua", ".sh": "Shell",
    ".zig": "Zig", ".ex": "Elixir", ".exs": "Elixir", ".scala": "Scala", ".vue": "Vue",
    ".svelte": "Svelte", ".html": "HTML", ".css": "CSS", ".m": "Objective-C",
}

FRAMEWORK_DEPS = {
    "react": "react", "next": "nextjs", "vue": "vue", "nuxt": "nuxt", "svelte": "svelte",
    "@sveltejs/kit": "sveltekit", "astro": "astro", "electron": "electron",
    "express": "express", "fastify": "fastify", "@angular/core": "angular",
    "gatsby": "gatsby", "@docusaurus/core": "docusaurus", "django": "django",
    "flask": "flask", "fastapi": "fastapi", "streamlit": "streamlit", "typer": "typer",
    "click": "click", "@tauri-apps/api": "tauri",
}

_SITE_CONFIG = re.compile(r"(?:astro|next|nuxt|svelte)\.config\.[a-z]+|gatsby-config\.[a-z]+"
                          r"|docusaurus\.config\.[a-z]+")


# Files `flutter create` generates for desktop targets, at the root or one folder down
# (pub workspaces such as localsend keep the app in app/).
_FLUTTER_RUNNER = re.compile(r"(?:([^/]+)/)?(?:linux/my_application\.cc|windows/runner/main\.cpp"
                             r"|macos/Runner\.xcodeproj/project\.pbxproj)")


def _flutter_desktop(fs) -> bool:
    for f in fs:
        hit = _FLUTTER_RUNNER.fullmatch(f)
        if hit and (f"{hit.group(1)}/pubspec.yaml" if hit.group(1) else "pubspec.yaml") in fs:
            return True
    return False


def _first(rules):
    for evidence, ok in rules:
        if ok:
            return evidence
    return None


def detect(facts: dict) -> dict:
    """Return {"type", "language", "evidence", "frameworks"} for RepoFacts."""
    files = facts.get("files") or []
    fs = set(files)
    m = facts.get("manifests") or {}
    pkg = m.get("package.json") or {}
    py = m.get("pyproject.toml") or {}
    cargo = m.get("Cargo.toml") or {}
    pub = m.get("pubspec.yaml") or {}
    gem = next((v for k, v in sorted(m.items()) if k.endswith(".gemspec")), None)
    deps = set(pkg.get("dependencies") or []) | set(py.get("dependencies") or [])
    setup = (m.get("setup.py") or {}).get("console_scripts") or (
        m.get("setup.cfg") or {}).get("console_scripts")
    wrangler = [v for k, v in m.items() if k.startswith("wrangler.")]

    checks = [
        ("claude-plugin", [
            (".claude-plugin/plugin.json", ".claude-plugin/plugin.json" in fs),
            (".claude-plugin/marketplace.json", ".claude-plugin/marketplace.json" in fs),
            ("SKILL.md at root", "SKILL.md" in fs),
            ("skills/*/SKILL.md", any(re.fullmatch(r"skills/[^/]+/SKILL\.md", f)
                                      for f in files)),
        ]),
        ("desktop-app", [
            ("src-tauri/", any(f.startswith("src-tauri/") for f in files)),
            ("tauri.conf.json", "tauri.conf.json" in fs),
            ("electron in package.json", bool({"electron", "electron-builder"} & deps) or any(
                d.startswith("@electron-forge/") for d in deps)),
            ("wails.json", "wails.json" in fs),
            ("Xcode macOS project", any(v.get("macos") for k, v in m.items()
                                        if k.endswith("project.pbxproj"))),
            ("Package.swift macOS app", bool((m.get("Package.swift") or {}).get("macos_app"))),
            ("Flutter desktop", bool(pub.get("flutter")) and any(
                f.startswith(("macos/", "windows/", "linux/")) for f in files)),
            ("Flutter desktop runner", _flutter_desktop(fs)),
        ]),
        ("website", [] if pkg.get("bin") else [
            ("index.html", "index.html" in fs),
            ("site framework config", any("/" not in f and _SITE_CONFIG.fullmatch(f)
                                          for f in files)),
            ("Hugo baseURL", any((m.get(k) or {}).get("base_url")
                                 for k in ("hugo.toml", "config.toml"))),
            ("Jekyll _config.yml", "_config.yml" in fs),
            ("wrangler static assets", any(w.get("pages_build_output_dir") or w.get("assets")
                                           for w in wrangler)),
        ]),
        ("cli", [
            # evidence order: compiled-language manifests first, they are rarely incidental
            ("Cargo binary", bool(cargo.get("bin")) or (
                "src/main.rs" in fs and "src/lib.rs" not in fs)),
            ("Go package main", "main.go" in fs or any(
                re.fullmatch(r"cmd/[^/]+/main\.go", f) for f in files)),
            ("package.json bin", bool(pkg.get("bin"))),
            ("pyproject scripts", bool(py.get("scripts")) or bool(setup)),
            # ponytail: any file in bin/ counts; executable bits are not in RepoFacts
            ("bin/ scripts", any(re.fullmatch(r"bin/[^/]+", f) and not f.endswith(".md")
                                 for f in files)),
            ("gemspec executables", bool(gem and gem.get("executables"))),
        ]),
        ("library", [
            ("package.json main/exports", bool(pkg.get("main") or pkg.get("exports")
                                               or pkg.get("module"))),
            ("Python package", "pyproject.toml" in m or "setup.py" in m),
            ("Cargo library", bool(cargo.get("lib")) or "src/lib.rs" in fs),
            ("go.mod", "go.mod" in fs),
            ("gemspec", gem is not None),
            ("composer.json", "composer.json" in fs),
            ("Java build file", any(f in fs for f in ("pom.xml", "build.gradle",
                                                     "build.gradle.kts"))),
        ]),
    ]
    ptype, evidence = "other", []
    for name, rules in checks:
        hit = _first(rules)
        if hit:
            ptype, evidence = name, [hit]
            break

    frameworks = {FRAMEWORK_DEPS[d] for d in deps if d in FRAMEWORK_DEPS}
    if any(f.startswith("src-tauri/") for f in files):
        frameworks.add("tauri")
    if pub.get("flutter"):
        frameworks.add("flutter")
    if any((m.get(k) or {}).get("base_url") for k in ("hugo.toml", "config.toml")):
        frameworks.add("hugo")
    return {"type": ptype, "language": _language(facts), "evidence": evidence,
            "frameworks": sorted(frameworks)}


def _language(facts: dict) -> str | None:
    repo = facts.get("repo") or {}
    if repo.get("primary_language"):
        return repo["primary_language"]
    totals: dict = {}
    for ext, size in (facts.get("ext_bytes") or {}).items():
        lang = LANG_BY_EXT.get(ext)
        if lang:
            totals[lang] = totals.get(lang, 0) + size
    if not totals:
        return None
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="detect.py", description="Detect project type and primary language.",
        epilog="Example: python3 scripts/detect.py . --offline --json")
    ap.add_argument("target", nargs="?", default=".", help="path, GitHub URL or owner/repo")
    ap.add_argument("--offline", action="store_true", help="no gh calls (local path only)")
    ap.add_argument("--json", action="store_true", help="machine output")
    args = ap.parse_args(argv)
    result = detect(facts_mod.collect_facts(args.target, offline=args.offline))
    if args.json:
        common.out(common.dumps(result))
    else:
        why = f" (detected: {', '.join(result['evidence'])})" if result["evidence"] else ""
        common.out(f"type: {result['type']}{why} · language: {result['language'] or 'unknown'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return common.run_main(_main, argv)


if __name__ == "__main__":
    sys.exit(main())
