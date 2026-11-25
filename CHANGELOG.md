# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `docs/audits/2026-11-25/`: audits of ten well-known public repositories of different
  types, published in the README with date and rubric version.
- `docs/commands.md` (flags and outputs of every command) and `docs/rules.md` (project
  rules and privacy).

### Changed
- README: first screen with name, one sentence, demo and install only; short sections
  that link to `docs/`. The case study waits for real numbers.
- `SPEC.md` moved to `docs/SPEC.md`.

### Fixed
- `fix.py topics` proposed fewer topics than the audit asks for: it now reads keywords
  from `.claude-plugin/plugin.json` and `Cargo.toml` and fills up to the rubric minimum
  with words from the description.
- Project type: `skills/*/SKILL.md` alone no longer turns a CLI or an app into a Claude
  Code plugin (`cli/cli`), and a Python package whose README imports it is a library
  even when it also installs a command (`pallets/flask`).

## [0.1.0] - 2026-11-25

### Added
- `/leadout:all`: audit, fix, visuals and launch kit in order, with a confirmation
  before each phase.
- Plugin README with a demo of a real audit run, icon `.claude-plugin/icon.png`,
  CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, issue forms, pull request template and a CI
  workflow (Python 3.9 and 3.13, `unittest`).
- Recorded audit fixtures and expected snapshots for `psf/requests` (library) and
  `localsend/localsend` (desktop app), next to `sharkdp/hyperfine` (CLI).
- Plugin skeleton: plugin manifest, marketplace manifest, `skills/`, `commands/`,
  `scripts/`, `templates/`, `tests/` folders.
- `SPEC.md`: the full specification that every implementation session follows.
- MIT license.
- `/leadout:audit` (skill `repo-audit`): read-only launch-readiness audit with a
  deterministic 0-100 score, coverage, grade and fixes ranked by impact per effort;
  works on a local path (also offline) or a remote `owner/repo`. Editable rubric in
  `scripts/rubric.json` (24 checks), overridable per repository in
  `.leadout/rubric.json`.
- `/leadout:stats` (skill `repo-stats`): stars, forks, watchers, 14-day views and
  clones, referrers, popular pages and release downloads, saved to
  `.leadout/stats.jsonl`; daily growth, referrer changes and a before/after launch
  comparison. Optional daily GitHub Actions workflow that keeps the history on an orphan
  `leadout-stats` branch.
- Scripts `common.py`, `facts.py`, `detect.py`, `audit.py`, `stats.py` (Python 3.9+,
  standard library only; all GitHub access through `gh`).
- `/leadout:fix` (skill `repo-fix`): turns the audit JSON into a plan of actions; renders
  README skeletons per project type, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, issue forms, PR
  template, CI workflows (Python, Node, Go, Rust) and a Keep a Changelog file from real facts,
  with `TODO(...)` for the rest; verified badges, topic proposals and release notes from
  Conventional Commits. Writes only on a non-default branch and never overwrites without a flag.
- `/leadout:visuals` (skill `repo-visuals`): GIF storyboard and recording commands for the
  tools present, GIF compression under a size limit (ffmpeg, gifski), 1280x640 social preview
  and light/dark banner SVGs, SVG to PNG with the first converter found.
- `/leadout:launch` (skill `launch-kit`): `launch/` kit with Show HN, Reddit, X, dev.to,
  awesome-list, calendar, checklist and objections files; awesome-list search, timezone-aware
  calendar and a lint for the platform rules. It writes text only and publishes nothing.
- Scripts `fix.py`, `visuals.py`, `launch.py`; `slug_of` and `repo_name` in `common.py`.
- Test suite (`python3 -m unittest discover -s tests`) with recorded `gh` fixtures; no
  network access during tests.

### Fixed
- Each command pre-approves its own skill (`allowed-tools: Skill(leadout:<skill>)`):
  in a live run the Skill call itself needed approval before any script ran.
- Flutter desktop apps whose app lives in a subfolder (pub workspaces, such as
  `localsend/localsend`) are detected as `desktop-app` instead of `other`.
- Recorded fixtures drop commit signature payloads and redact `Name <email>` trailers,
  which carried author emails.

### Known gaps
- `templates/launch/subreddits.json` is empty: Reddit refused automated reads of the rules
  pages, and rules are never guessed.

[Unreleased]: https://github.com/cc-forge/leadout/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cc-forge/leadout/releases/tag/v0.1.0
