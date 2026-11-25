# leadout — Specification

leadout is a Claude Code plugin that takes a GitHub repository from "code finished" to
"launched and measured". It merges into one flow what usually lives in separate skills
(README generators, audits, visuals, repo init) and adds the two missing parts: the launch
itself and post-launch statistics.

This document is the single source of truth for every implementation session. A session
that reads only this file and the repository must be able to do its part without the
conversation that produced it. When this spec and the official Claude Code documentation
disagree on plugin structure, the documentation wins: update this spec in the same change.

Spec version: 3 · Written: 2026-11-25 · Updated: 2026-11-25 (S3+S4) · Plugin version targeted: 0.1.0

---

## 0. Status and session plan

| Session | Scope | Status |
|---|---|---|
| S1 | Skeleton, manifests, LICENSE, CHANGELOG, this SPEC | done |
| S2 | The two read-only phases. Audit: `common.py`, `facts.py`, `detect.py`, `rubric.json`, `audit.py`, skill `repo-audit`, command `audit`. Stats (scope moved here from S5): `stats.py`, `templates/workflows/leadout-stats.yml`, skill `repo-stats`, command `stats`. Tests `test_common`, `test_rubric`, `test_checks`, `test_audit_e2e` (recorded `sharkdp/hyperfine` fixtures and expected snapshot), `test_stats` | done |
| S3 | `fix.py`, `templates/readme/*`, `templates/community/*`, `templates/workflows/ci-*.yml`, `templates/release-notes.md`, `templates/changelog.md`, skill `repo-fix`, command `fix`, `test_fix.py` | done (built together with S4) |
| S4 | `visuals.py`, `templates/visuals/*`, `launch.py`, `templates/launch/*`, skills `repo-visuals` and `launch-kit`, commands `visuals` and `launch`, `test_visuals.py`, `test_launch.py`, `test_launch_lint.py`. `subreddits.json` ships **empty**: Reddit refused every automated read on 2026-11-25 (HTTP 403), and an unread rule is never guessed (D11, D21) | done |
| S5 | Merged into S2 (D15). Left for later: record traffic fixtures from a repository with push access, if the owner agrees to commit them | merged |
| S6 | Command `all`, real audits on three public repos plus fixes, expected-score snapshots, plugin README (self-audit ≥ 90), demo asset, icon, release 0.1.0 prep. Live run with `claude -p --plugin-dir`: commands needed `Skill(...)` in `allowed-tools` (D23). Real audits: hyperfine 79, requests 75, localsend 83 after the Flutter-workspace detection fix. Self-audit: 87 on the uncommitted tree (no tag, no remote), 95 with a commit and a `v0.1.0` tag (checked in a throwaway copy) | done |

Each session ends with: `python3 -m unittest discover -s tests -v` green,
`claude plugin validate .` passing, CHANGELOG `[Unreleased]` updated, this table updated.
A session never leaves stubs: every file it creates is complete and working.

---

## 1. General rules (non-negotiable)

1. **Language.** Everything in the repository is English: README, SKILL.md, command
   files, script output, comments, templates, tests. Claude talks to the user in the
   user's language (the author speaks Italian); generated repo content stays English
   unless the user asks otherwise for their own repo.
2. **Stack.** Scripts are Python 3.9+ using only the standard library. Nothing to install.
   (3.9 is the macOS system Python; do not use `match`, `tomllib` without fallback,
   runtime `X | Y` unions, or other 3.10+ features.)
3. **GitHub access.** Every GitHub call goes through `gh` or `gh api`. Scripts never call
   `gh auth token`, never read, print, store or pass a token, and never write one to a
   file or log. All printed text passes through `redact()` (section 6.6).
4. **Confirmation before writing.** Explicit user confirmation is required before each
   of: writing to an existing file, creating a commit, pushing, changing repo metadata
   (description, homepage, topics, labels, settings), creating a release or tag.
   Creating a new file on a new branch is allowed after the user approved the phase
   plan that lists it. The protocol is in section 6.7.
5. **Read-only phases.** `audit` and `stats` never write to the analyzed repository: no
   file in its tracked tree, no commit, no metadata change. `stats` writes only its own
   history under `.leadout/` (section 13.3).
6. **Platform rules.** leadout never produces or encourages: requests for votes,
   upvotes or stars; star exchanges ("star for star"); multiple accounts or sock puppets;
   identical posts across subreddits; posting on the user's behalf. The plugin writes
   texts only and never publishes anything on any platform. The plugin README states
   this as a project choice.
7. **No invention.** Never invent features, numbers, benchmarks, user counts,
   testimonials, logos of users, or press. When a fact is missing, leave a visible
   placeholder `TODO(<what is needed>)` and list it in the phase summary.
8. **Errors.** Every expected failure produces a clear one-line message and a hint,
   never a stack trace: nonexistent repo, private/inaccessible repo, rate limit,
   `gh` missing, `gh` not authenticated, no push access (traffic), network down,
   invalid arguments, invalid rubric, file already exists, wrong branch. See section 6.5.

---

## 2. Verified platform facts

Checked on 2026-11-25 against the docs and live API. Re-verify anything marked
"verify" when implementing.

### 2.1 Claude Code plugins (code.claude.com/docs/en/plugins-reference, plugin-marketplaces, skills, slash-commands)

- Manifest: `.claude-plugin/plugin.json`; only `name` is required (kebab-case).
  Optional fields used here: `displayName`, `version`, `description`, `author`
  (`name`, `url`), `homepage`, `repository`, `license`, `keywords`. Unknown keys are
  stripped with a warning.
- Component folders live at the plugin root, not inside `.claude-plugin/`:
  `skills/<name>/SKILL.md` and `commands/*.md` are auto-discovered.
- Marketplace: `.claude-plugin/marketplace.json` with `name`, `owner.name`, `plugins[]`
  (`name`, `source`). The plugin sits at the marketplace root with `"source": "./"`;
  `claude plugin validate .` passes with this layout (checked in S1).
- Skills are invocable as `/<plugin>:<skill>`. A skill and a command with the same
  name: the skill wins. The docs call `commands/` the legacy format and prefer skills.
- SKILL.md frontmatter used here: `name`, `description`, `when_to_use`,
  `argument-hint`, `user-invocable`, `disable-model-invocation`, `allowed-tools`.
  `description` + `when_to_use` are truncated at 1,536 characters in the listing.
- Command frontmatter used here: `description`, `argument-hint`, `allowed-tools`.
  `$ARGUMENTS` holds the full argument string.
- `${CLAUDE_PLUGIN_ROOT}` expands in plugin SKILL.md bodies and in `allowed-tools`
  Bash rules (observed live on 2026-11-25, Claude Code 2.1.330, `claude -p --plugin-dir`:
  the Bash call arrived as `python3 /abs/path/scripts/audit.py sharkdp/hyperfine` and ran
  without a prompt). It is **not** set in the Bash tool environment. Commands still never
  reference script paths (D4).
- A command that invokes a plugin skill through the Skill tool needs
  `allowed-tools: Skill(<plugin>:<skill>)` in its own frontmatter; without it the Skill
  call needs approval (observed live: denied in `-p` mode, D23).
- `allowed-tools` Bash matching, observed live with `--allowedTools`: `Bash(cmd)` matches
  only `cmd` exactly (`cmd --write` prompts); `Bash(cmd *)` matches `cmd` alone and `cmd`
  followed by any arguments, `--overwrite` included, but not `cmdx`; compound commands
  (`a && b`) are split and every part must match; a quoted path (`python3 "x.py"`) does
  not match an unquoted rule. Read-only commands such as `echo` are approved anyway.
- Validation: `claude plugin validate <path>`; `--profile directory` also fails on
  directory-submission problems (missing README, missing icon warning).
- Local testing: `claude --plugin-dir <path>`; install flow:
  `claude plugin marketplace add <path>` then `claude plugin install leadout@leadout`.

### 2.2 GitHub API behaviour (probed live)

| Endpoint | Observed |
|---|---|
| `GET repos/{o}/{r}/traffic/views` without push access | HTTP 403, message `Must have push access to repository` |
| `GET repos/{o}/{r}/traffic/views` with push access | keys `count`, `uniques`, `views[]` |
| `GET repos/{o}/{r}/stargazers` (REST, with or without `Accept: application/vnd.github.star+json`) on repos the viewer does not own | HTTP 404 `Not Found` even for public repos with thousands of stars |
| GraphQL `stargazers(last:N){edges{starredAt}}` on a repo the viewer does not own | empty `edges` while `stargazerCount` is non-zero |
| GraphQL `repository{usesCustomOpenGraphImage openGraphImageUrl licenseInfo{spdxId} defaultBranchRef{name}}` | works on public repos |
| `GET repos/{o}/{r}/community/profile` | `health_percentage`, `files.{code_of_conduct,contributing,issue_template,pull_request_template,license,readme}`; accounts for org-level `.github` defaults |
| `GET repos/{o}/{r}/actions/runs?branch=<default>&per_page=1` | `workflow_runs[0].{status,conclusion,created_at}` |
| `GET repos/{o}/{r}/releases` | `assets[].download_count` |
| Nonexistent or invisible private repo | HTTP 404 (indistinguishable) |

Consequences: star timestamps are a best-effort source available mainly for the
viewer's own repos. The star timeline in `stats` therefore relies on stored snapshots
of `stargazers_count`, and uses `starred_at` events only when the API returns them.
`watchers_count` in the REST repo object is an alias of stars; real watchers are
`subscribers_count`.

There is no REST API for uploading a repository social preview image (verified on
2026-11-25 in docs.github.com: `PATCH /repos/{owner}/{repo}` has no such parameter, and
"Customizing your repository's social media preview" documents only the web UI). The
plugin generates the image and tells the user where to upload it
(`https://github.com/<owner>/<repo>/settings` → Social preview → Edit). GitHub accepts
PNG, JPG or GIF under 1 MB, at least 640×320, 1280×640 recommended.

---

## 3. File tree (final for v0.1.0)

```
leadout/
├── .claude-plugin/
│   ├── plugin.json                 plugin manifest
│   ├── marketplace.json            marketplace manifest (plugin at "./")
│   └── icon.png                    S6: square PNG 512–2048 px, < 2 MB
├── skills/
│   ├── repo-audit/SKILL.md
│   ├── repo-fix/SKILL.md
│   ├── repo-visuals/SKILL.md
│   ├── launch-kit/SKILL.md
│   └── repo-stats/SKILL.md
├── commands/
│   ├── audit.md
│   ├── fix.md
│   ├── visuals.md
│   ├── launch.md
│   ├── stats.md
│   └── all.md
├── scripts/
│   ├── common.py                   errors, gh wrapper, targets, redaction, fixtures, output
│   ├── facts.py                    collect RepoFacts (local or remote) → JSON
│   ├── detect.py                   project type + primary language
│   ├── rubric.json                 default audit rubric (editable)
│   ├── audit.py                    evaluate rubric, score, report
│   ├── fix.py                      plan / render / write / badges / topics / release-notes
│   ├── visuals.py                  plan / compress / social-preview / banner / to-png
│   ├── launch.py                   init / subreddits / awesome / calendar / lint
│   └── stats.py                    collect / report / install-workflow
├── templates/
│   ├── readme/
│   │   ├── cli.md
│   │   ├── library.md
│   │   ├── desktop-app.md
│   │   ├── website.md
│   │   ├── claude-plugin.md
│   │   └── other.md
│   ├── community/
│   │   ├── CONTRIBUTING.md
│   │   ├── CODE_OF_CONDUCT.md
│   │   ├── SECURITY.md
│   │   ├── PULL_REQUEST_TEMPLATE.md
│   │   └── ISSUE_TEMPLATE/
│   │       ├── bug_report.yml
│   │       ├── feature_request.yml
│   │       └── config.yml
│   ├── workflows/
│   │   ├── ci-python.yml
│   │   ├── ci-node.yml
│   │   ├── ci-go.yml
│   │   ├── ci-rust.yml
│   │   └── leadout-stats.yml
│   ├── release-notes.md
│   ├── changelog.md                template for the `changelog` action
│   ├── visuals/
│   │   ├── social-preview.svg
│   │   └── banner.svg
│   └── launch/
│       ├── show-hn.md
│       ├── reddit.md
│       ├── x-thread.md
│       ├── devto.md
│       ├── awesome-lists.md
│       ├── calendar.md
│       ├── checklist.md
│       ├── objections.md
│       ├── calendar.json           suggested slots, relative to launch day
│       └── subreddits.json         subreddit data with verified rule summaries
├── tests/
│   ├── fixtures/
│   │   ├── gh/                     recorded `gh api` responses (section 15.2)
│   │   ├── facts/                  RepoFacts JSON for check tests
│   │   ├── stats/                  history JSONL samples
│   │   └── expected/               expected audit JSON for recorded repos (S6)
│   ├── helpers.py                  fixture loading, temp git repo helper
│   ├── test_common.py
│   ├── test_rubric.py
│   ├── test_checks.py
│   ├── test_audit_e2e.py
│   ├── test_fix.py
│   ├── test_launch.py              init, calendar, subreddits, awesome
│   ├── test_launch_lint.py
│   ├── test_visuals.py
│   └── test_stats.py
├── README.md                       S6
├── docs/                         SPEC.md, commands.md, rules.md, audits/, cover, demo, social preview
├── CHANGELOG.md
├── LICENSE                         MIT
└── .gitignore
```

`.gitkeep` files created in S1 are removed once a folder has real content.

---

## 4. Manifests

`plugin.json` and `marketplace.json` exist (S1). Rules:

- `version` in `plugin.json` and in the marketplace entry move together (SemVer).
- The marketplace entry `name` equals the plugin `name` (`leadout`), so the install
  id is `leadout@leadout`.
- `homepage`/`repository` point to `https://github.com/cc-forge/leadout`, the
  authenticated account at S1. If the repo is published elsewhere, update both files.
- S6 adds `.claude-plugin/icon.png`; no `icon` field is needed at that path.

---

## 5. Skills and commands

### 5.1 Design

The logic and the instructions live in **skills**. **Commands** are thin, user-facing
entry points. The docs say a skill shadows a command with the same name, so the names
differ:

| Command (user types) | Skill it runs | Phase |
|---|---|---|
| `/leadout:audit` | `repo-audit` | 1 audit (read-only) |
| `/leadout:fix` | `repo-fix` | 2 fix |
| `/leadout:visuals` | `repo-visuals` | 3 visuals |
| `/leadout:launch` | `launch-kit` | 4 launch kit |
| `/leadout:stats` | `repo-stats` | 5 stats (read-only on the repo) |
| `/leadout:all` | runs the four skills above in order, stats excluded | pipeline |

Skills set `user-invocable: false`, so the slash menu shows only the six commands.
The model can still pick a skill on its own when the user asks in plain words
("audit this repo").

Safety by design: `allowed-tools` pre-approves only read-only or new-file-only script
calls. Commands that write to existing files, commit, push, edit metadata or create
releases (`fix.py write`, `git commit`, `git push`, `gh repo edit`, `gh release create`,
`gh label create`, `gh issue edit`, `stats.py install-workflow --write`) are never
pre-approved. The harness permission prompt is a second gate on top of the
conversational confirmation.

### 5.2 Command files

Each command body tells Claude to invoke the matching skill with the Skill tool,
passing `$ARGUMENTS` through. Command bodies never contain script paths. Template:

```markdown
---
description: <one line, ≤ 100 chars>
argument-hint: "<hint>"
allowed-tools: Skill(leadout:<skill>)
---

Invoke the Skill tool with skill `leadout:<skill>` and pass these arguments
unchanged: $ARGUMENTS

If no target is given, the target is the current working directory.
Talk to the user in their language; everything written to the repository stays in English.
```

| File | description | argument-hint |
|---|---|---|
| `audit.md` | `Audit a GitHub repo for launch readiness: read-only, deterministic 0-100 score` | `"[owner/repo \| URL \| path] [--json] [--offline] [--rubric FILE]"` |
| `fix.md` | `Apply audit fixes on a new branch: README, badges, community files, CI, release notes` | `"[path] [--only ACTION[,ACTION]]"` |
| `visuals.md` | `Plan and produce the demo GIF, social preview (1280x640) and README banner` | `"[path]"` |
| `launch.md` | `Write a launch kit in launch/: Show HN, Reddit, X, dev.to, awesome lists, calendar` | `"[path] [--launch-date YYYY-MM-DD] [--timezone TZ]"` |
| `stats.md` | `Read stars, traffic, referrers and downloads; keep a local history; compare before/after launch` | `"[owner/repo \| path] [--launch-date YYYY-MM-DD] [--install-workflow]"` |
| `all.md` | `Run audit, fix, visuals and launch in order, asking for confirmation at each phase` | `"[path]"` |

`all.md` body differs: it states the pipeline (section 5.4) instead of a single skill.

### 5.3 Skill frontmatter

Script calls inside skill bodies use the literal form
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py ...`, unquoted, so the command text
matches the `allowed-tools` patterns below character for character (a quoted path would
not match them and would fall back to a permission prompt). The plugin cache path has no
spaces.

**skills/repo-audit/SKILL.md**
```yaml
---
name: repo-audit
description: Audit a GitHub repository's launch readiness (README, metadata, distribution, community, health) with a deterministic 0-100 score and fixes ranked by impact per effort. Read-only. Works on a local path or a remote owner/repo.
when_to_use: The user asks to audit, score, review or check a repository before launch, or asks why a repo gets no attention.
argument-hint: "[owner/repo | URL | path] [--json] [--offline] [--rubric FILE]"
user-invocable: false
allowed-tools: Read Glob Grep Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/audit.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/facts.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect.py *)
---
```
Body: run `audit.py <target>` (markdown) and show the report as is. Explain the top
three fixes in plain language. Point to `/leadout:fix` for the fixes marked
`phase: fix` and to `/leadout:visuals` for `phase: visuals`. Never edit files.

**skills/repo-fix/SKILL.md**
```yaml
---
name: repo-fix
description: Apply the fixes found by the leadout audit on a new git branch. Detects the project type (desktop app, CLI, library, website, Claude Code plugin) and adapts the README; generates working badges, topics, issue/PR templates, CONTRIBUTING, a CI workflow for the detected language and release notes. Shows the diff and asks before every write, commit, push or metadata change.
when_to_use: The user wants to fix audit findings, rewrite or improve a README, add community files, badges, topics, CI or release notes.
argument-hint: "[path] [--only ACTION[,ACTION]]"
user-invocable: false
allowed-tools: Read Glob Grep Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/audit.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fix.py plan *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fix.py render *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fix.py badges *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fix.py topics *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fix.py release-notes *) Bash(git status *) Bash(git diff *) Bash(git log *) Bash(git branch --show-current) Bash(git branch --list *) Bash(git rev-parse *)
---
```
Body: the fix flow in section 10. (S3 deviation, D19: `git branch *` would also match
`git branch -D`, so only the read forms are pre-approved.)

**skills/repo-visuals/SKILL.md** (S4 deviation, D19: only `visuals.py plan` is pre-approved; `compress`,
`social-preview`, `banner` and `to-png` create files, and a `*` rule would also match `--overwrite`)
```yaml
---
name: repo-visuals
description: Plan and produce a repository's visuals - a demo GIF script for the first 5 seconds, recording and compression commands (under 5 MB with ffmpeg or gifski), a 1280x640 social preview SVG convertible to PNG, and a README header banner.
when_to_use: The user needs a demo GIF, screenshot, social preview or banner for a repository.
argument-hint: "[path]"
user-invocable: false
allowed-tools: Read Glob Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/visuals.py plan) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/visuals.py plan *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/facts.py *)
---
```
Body: the visuals flow in section 11.

**skills/launch-kit/SKILL.md** (S4 deviation, D19: `launch.py init` and `--overwrite` are not
pre-approved; only the read-only subcommands `subreddits`, `awesome`, `calendar`, `lint` are)
```yaml
---
name: launch-kit
description: Write a launch kit for a GitHub repository in launch/ - Show HN title and text, one tailored post per fitting subreddit with its self-promotion rules summarised and linked, an X thread, a dev.to post, relevant awesome lists with PR instructions, a release calendar, a launch-day checklist and prepared answers to likely objections. Writes text only and never publishes anything.
when_to_use: The user is preparing to launch or announce a project, or asks for launch posts, a launch plan or awesome lists.
argument-hint: "[path] [--launch-date YYYY-MM-DD] [--timezone TZ]"
user-invocable: false
allowed-tools: Read Glob Grep Write(launch/**) Edit(launch/**) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py subreddits) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py subreddits *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py awesome *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py calendar *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py lint) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py lint *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/facts.py *)
---
```
Body: the launch flow in section 12. `Write`/`Edit` are pre-approved only under
`launch/`; overwriting an existing file there still needs the conversational
confirmation.

**skills/repo-stats/SKILL.md**
```yaml
---
name: repo-stats
description: Read a GitHub repository's stars over time, forks, watchers, clones and views for the last 14 days, top referrers and pages, and release downloads via gh api; save each reading to a local history (.leadout/stats.jsonl) and report daily growth, referrers and a before/after comparison around a launch date. Read-only on the repository.
when_to_use: The user asks how a launch went, wants repository traffic, stars, referrers or download numbers, or wants to keep traffic history beyond GitHub's 14 days.
argument-hint: "[owner/repo | path] [--launch-date YYYY-MM-DD] [--install-workflow]"
user-invocable: false
allowed-tools: Read Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py collect *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py report *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py install-workflow)
---
```
`stats.py install-workflow` without `--write` only prints, so it is safe to pre-approve.
A trailing `*` would also match `--write`, so the rule lists only the bare form (no
arguments, target `.`): the skill previews from inside the checkout, and the `--write`
call always goes through the harness prompt (D17). `collect` is pre-approved because it
writes only the stats history.

### 5.4 Pipeline (`/leadout:all`)

1. Run the `repo-audit` skill and show the score.
2. Ask: continue to fix? If yes, run `repo-fix`; it keeps its own per-write confirmations.
   After the fix, re-run the audit on the fix branch and show the score delta.
3. Ask: continue to visuals? If yes, run `repo-visuals`.
4. Ask: continue to the launch kit? If yes, run `launch-kit`.
5. End: list remaining TODOs from all phases, say that `/leadout:stats` is for after
   the launch, and suggest `--launch-date` once the date is known.

A "no" at any step ends the pipeline. Everything already produced stays where it is,
and the summary says so. Nothing is pushed unless the user approved the push inside
`repo-fix`.

---

## 6. Shared conventions

### 6.1 Code

- One module per script in `scripts/`. Shared code only in `common.py`. Scripts import
  siblings with plain `import common`; the script directory is on `sys.path` when run
  as `python3 scripts/x.py`. Tests add `scripts/` to `sys.path` in `tests/helpers.py`.
- Every script: module docstring with usage, `def main(argv: list[str] | None = None) -> int`
  (with `from __future__ import annotations`), `if __name__ == "__main__": sys.exit(main())`.
- `argparse` with subcommands where listed. `--help` text in English with an example.
- Plain dicts and lists for data; no classes except `RLError` and its subclasses. Pure
  functions for evaluation and reporting; I/O (gh, files, clock) only at the edges, and
  injectable (`as_of`, `now`, fixture dir) for tests.
- Determinism: sorted file lists, sorted keys, stable tie-breaks, no `set` iteration in
  output, no current time in pure functions, `json.dumps(..., indent=2, sort_keys=True,
  ensure_ascii=False)` for files and `--json` output.
- Times: UTC ISO 8601 with `Z` (`2026-11-25T09:30:00Z`); dates `YYYY-MM-DD`.
- Line length ≤ 100. Type hints on public functions. No dependencies, no vendored libs.
- Subprocesses: list arguments only, never `shell=True`; timeouts (gh: 60 s; git: 30 s;
  ffmpeg: 600 s).
- Files written by scripts: UTF-8, `\n` newlines, trailing newline. New files only,
  unless an explicit `--overwrite` flag is passed (which skills pass only after user
  confirmation).
- Anything that cuts a corner with a known ceiling gets a `# ponytail:` comment naming
  the ceiling and the upgrade path.

### 6.2 CLI conventions

- The positional `TARGET` is optional and defaults to `.`.
- `--json`: machine output on stdout, nothing else on stdout. Human messages,
  warnings and progress go to stderr.
- `--offline`: no gh calls; remote-only data becomes `unknown`.
- Exit codes: `0` success; `1` completed with findings that a caller should treat as
  failure (only `launch.py lint`); `2` usage error; `3` GitHub or environment error;
  `4` local file or git error; `5` internal error.

### 6.3 Targets

`common.resolve_target(arg)` returns
`{"kind": "local"|"remote", "path": str|None, "slug": "owner/repo"|None}`:

1. If `arg` is an existing directory → local; `path` is its absolute path. `slug` comes
   from `git remote get-url origin` when it points to github.com (https or ssh form),
   else `None`.
2. Else if it matches `https://github.com/<o>/<r>[.git][/...]`, `git@github.com:<o>/<r>[.git]`
   or `^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$` → remote with that slug.
3. Else → `E_BAD_TARGET`.

Owner/repo names are validated against GitHub's character rules before use in an API
path. They are never interpolated into a shell string.

### 6.4 Templates

- Placeholder syntax `[[name]]`, with `name` matching `[a-z_][a-z0-9_]*`. Chosen to
  avoid clashing with GitHub Actions `${{ }}`, Jinja and shell syntax.
- `common.render(text, values)` replaces known names. A placeholder whose value is
  missing or empty becomes the visible marker `TODO(<name>)`. The function returns
  `(rendered, todos)` and callers report `todos`.
- Templates never contain claims about the project. They contain structure, factual
  placeholders and `TODO(...)` prompts only.

### 6.5 Errors

`common.py` defines `class RLError(Exception)` with `code`, `message`, `hint`,
`exit_code`, and a `run_main(fn)` wrapper used by every script:

- `RLError` → stderr `leadout: error: <message>` and, if any, `hint: <hint>`; with
  `--json`, stdout `{"error": {"code": "...", "message": "...", "hint": "..."}}`.
- Any other exception → `leadout: internal error: <ExceptionType>: <message>` plus
  `hint: rerun with LEADOUT_DEBUG=1 for a traceback`, exit 5. The traceback is
  printed only when `LEADOUT_DEBUG=1`.
- `KeyboardInterrupt` → `leadout: interrupted`, exit 130.

Standard messages (exact wording; `{x}` are fields):

| Code | Exit | Message | Hint |
|---|---|---|---|
| `E_GH_MISSING` | 3 | `GitHub CLI (gh) is not installed.` | `Install it from https://cli.github.com, then run: gh auth login` |
| `E_GH_AUTH` | 3 | `gh is not authenticated.` | `Run: gh auth login` |
| `E_REPO_NOT_FOUND` | 3 | `Repository {slug} was not found, or it is private and your account cannot see it.` | `Check the owner/name spelling, or run: gh auth status` |
| `E_RATE_LIMIT` | 3 | `GitHub API rate limit reached. It resets at {reset_local} (in {minutes} min).` | `Try again after the reset, or use --offline for a local-only audit.` |
| `E_SECONDARY_RATE_LIMIT` | 3 | `GitHub asked to slow down (secondary rate limit).` | `Wait a minute and try again.` |
| `E_FORBIDDEN` | 3 | `GitHub refused access to {endpoint} (HTTP 403).` | `Your account may lack permission for this data.` |
| `E_NETWORK` | 3 | `Cannot reach GitHub: {detail}.` | `Check your connection, or use --offline for a local-only audit.` |
| `E_GH_FAILED` | 3 | `gh failed: {detail}.` | `Run the same command with LEADOUT_DEBUG=1 for details.` |
| `E_BAD_TARGET` | 2 | `"{arg}" is not a directory, a GitHub URL or owner/repo.` | `Examples: . or cli/cli or https://github.com/cli/cli` |
| `E_BAD_ARGS` | 2 | `{detail}` | `Run with --help for usage.` |
| `E_NOT_GIT` | 4 | `{path} is not a git repository.` | `Run the command inside a git checkout.` |
| `E_DEFAULT_BRANCH` | 4 | `Refusing to write on the default branch ({branch}).` | `Create a branch first: git switch -c leadout/fix-{date}` |
| `E_DIRTY_TREE` | 4 | `The working tree has uncommitted changes.` | `Commit or stash them first, so the fix diff stays clean.` |
| `E_FILE_EXISTS` | 4 | `{path} already exists; not overwriting.` | `Pass --overwrite after reviewing the diff.` |
| `E_RUBRIC_INVALID` | 2 | `Rubric {path} is invalid: {detail}.` | `Compare it with scripts/rubric.json.` |
| `E_TOOL_MISSING` | 4 | `{tool} is not installed.` | `{install_hint}` |
| `E_HISTORY_CORRUPT` | 4 | `{path} line {n} is not valid JSON; skipped.` (warning, not fatal) | — |

Informational warnings use `leadout: warning: ...`. Standard warnings:
- `W_NO_PUSH`: `Traffic data needs push access to {slug}. Showing public data only.`
- `W_PRIVATE`: `{slug} is private. A private repository cannot be launched publicly; checks still run.`
- `W_ARCHIVED`: `{slug} is archived.`
- `W_TREE_TRUNCATED`: `The file list of {slug} is truncated by GitHub; file checks may miss files.`
- `W_STARS_HIDDEN`: `GitHub does not list stargazers of {slug} to this account; the star timeline uses stored snapshots only.`

### 6.6 gh wrapper and secrets

`common.gh_api(endpoint, *, method="GET", accept=None, fields=None, paginate=False,
include_headers=False, allow_404=False)` and `common.gh_graphql(query, variables)`:

- Runs `["gh", "api", ...]` with `capture_output=True, text=True, timeout=60`. Before
  the first call, `gh auth status` exit code ≠ 0 → `E_GH_AUTH`. `FileNotFoundError` →
  `E_GH_MISSING`.
- The pre-check is `gh auth status --hostname github.com`; HTTP 401 or `Bad credentials`
  on any call also maps to `E_GH_AUTH`. Network patterns also include
  `error connecting to` and `no such host`. A gh timeout (60 s) is `E_NETWORK`.
- Error mapping from stderr/stdout: `HTTP 404` → `E_REPO_NOT_FOUND` when the endpoint
  is the repo itself, otherwise returns `None` if `allow_404`, else `E_GH_FAILED`;
  `HTTP 403` with `rate limit` → `E_RATE_LIMIT` (reset time read from
  `gh api rate_limit`, converted to local time); `secondary rate limit` →
  `E_SECONDARY_RATE_LIMIT`; other 403 → `E_FORBIDDEN` (callers such as traffic catch
  it and degrade); `could not resolve host`, `dial tcp`, `connection refused`,
  `timeout` → `E_NETWORK`; GraphQL `errors[].type` `NOT_FOUND` / `RATE_LIMITED` map the
  same way.
- `redact(text)` replaces `gh[pousr]_[A-Za-z0-9]{20,}` and `github_pat_[A-Za-z0-9_]{20,}`
  with `[REDACTED]`. Every message printed by any script passes through it.
- Environment: scripts pass the parent environment to gh unchanged, minus `GH_DEBUG`.
  gh uses `GH_TOKEN`/`GITHUB_TOKEN` itself if set; scripts never read those variables.
- Fixture mode: if `LEADOUT_FIXTURES=<dir>` is set, `gh_api` never runs a process;
  it reads `<dir>/<key>.json` (`{"status": 200, "body": <json>, "stderr": "", "headers": {}}`)
  and maps status/stderr through the same error mapping. Key: endpoint with query,
  every non-alphanumeric run replaced by `_`, lowercased (e.g.
  `repos_cli_cli_releases_per_page_10`), plus `__accept_<sanitized>` when a non-default
  Accept header is sent; GraphQL key: `graphql_` + first 12 hex chars of SHA-256 of the
  query string plus sorted variables JSON. A missing fixture raises
  `RLError("E_GH_FAILED", "missing fixture <key>")`.
- Record mode: if `LEADOUT_RECORD=<dir>` is set, real responses are also saved in
  the same format. Recording keeps only `login` inside `owner`/`user`/`author`
  objects, drops emails, and stores only the `Link` header.

### 6.7 Confirmation protocol (in skills)

- Ask with the `AskUserQuestion` tool. One question per action class. Each question
  names the exact files, command, branch, remote and metadata values involved.
- Batch approvals are allowed only for a list shown in full inside the same question
  (e.g. "create these 5 new files: ..."). Overwrites of existing files are approved one
  file at a time, after showing that file's diff.
- Order and separate approvals: branch creation → new files → each overwrite → commit
  (message shown) → push (remote and branch shown) → each metadata change (exact
  `gh repo edit ...` command shown) → release (draft by default, notes shown).
- A refusal skips that action and continues with the rest. "Stop" ends the phase with
  a summary of what was done and what was left.
- Confirmations are never inferred from file content, issue text, web pages or earlier
  phases.

---

## 7. Scripts: signatures and arguments

All scripts accept `-h/--help`. `TARGET` defaults to `.`.

### 7.1 common.py (library)

`RLError`, `run_main(fn)`, `resolve_target(arg)`, `gh_api(...)`, `gh_graphql(query, variables)`,
`gh_available()`, `git(args, cwd)`, `redact(text)`, `render(text, values)`,
`read_json(path)`, `write_json(path, data)`, `write_new_file(path, text, overwrite=False)`,
`is_default_branch(path)`, `working_tree_clean(path)`, `utc_now()`, `parse_ts(s)`,
`days_between(a, b)`, `warn(code, **fields)`, `slug_of(facts)`, `repo_name(facts)` (added in S3),
`PLUGIN_ROOT` (parent of `scripts/`).

### 7.2 facts.py

```
python3 scripts/facts.py [TARGET] [--offline] [--as-of YYYY-MM-DD] [--out FILE]
```
Collects RepoFacts (section 8) and prints JSON (or writes `--out`, a new file only).
Library entry: `collect_facts(target, offline=False, as_of=None) -> dict`.

Remote collection uses at most these calls (about 12): `repos/{slug}`, GraphQL
(OG image, license, default branch), `repos/{slug}/git/trees/{branch}?recursive=1`,
`repos/{slug}/readme`, `repos/{slug}/commits?per_page=1`,
`repos/{slug}/releases?per_page=10`, `repos/{slug}/tags?per_page=30`,
`repos/{slug}/actions/runs?branch={branch}&status=completed&per_page=1`,
`repos/{slug}/issues?state=open&per_page=100` (PRs filtered out),
`repos/{slug}/labels?per_page=100`, `repos/{slug}/community/profile`, and a
`search/issues` count for the good-first-issue label when the label exists. Manifest
files needed by `detect.py` (package.json, pyproject.toml, Cargo.toml, go.mod...) are
read with one `contents` call each, only when present in the tree.
Local collection reads the working tree (respecting `.gitignore` through
`git ls-files --cached --others --exclude-standard`) and `git log -1 --format=%cI`,
then adds remote data when a slug exists and `--offline` is not set. Local files win
over the remote tree for file checks (they reflect what the user is about to push).
Implementation notes (S2):
- A directory that is not a git checkout is walked with a fixed skip list (`.git`,
  `node_modules`, `__pycache__`, `.venv`, `venv`); `last_commit_at` and `tags` are then
  `null` with reason `no_git`. A checkout without commits gives reason `no_commits`.
- Local tags come from `git tag --sort=-creatordate` (newest first); remote tags are read
  only when no local tag exists.
- The Actions runs call is made only when workflow files exist. `workflows_with_runs`
  holds the path of that latest run only.
- `--offline` with a remote `owner/repo` is `E_BAD_ARGS`: there is nothing local to read.

### 7.3 detect.py

```
python3 scripts/detect.py [TARGET] [--offline] [--json]
```
Library: `detect(facts) -> {"type": str, "language": str|None, "evidence": [str], "frameworks": [str]}`.

Types and rules, first match wins:

1. `claude-plugin`: `.claude-plugin/plugin.json` or `.claude-plugin/marketplace.json`,
   or `SKILL.md` at root, or `skills/*/SKILL.md`.
2. `desktop-app`: `src-tauri/` or `tauri.conf.json`; `electron` / `electron-builder` /
   `@electron-forge/*` in package.json dependencies; `wails.json`; `*.xcodeproj` or
   `Package.swift` with a macOS app target (`.app` product, `NSApplication` or
   `@main struct ...: App` in Sources); Flutter with `macos/`, `windows/` or `linux/`; a
   Flutter desktop runner (`linux/my_application.cc`, `windows/runner/main.cpp`,
   `macos/Runner.xcodeproj/project.pbxproj`) next to a `pubspec.yaml`, at the root or one
   folder down (pub workspaces, S6).
3. `website`: no `bin` entries and one of: `index.html` at root; `astro.config.*`,
   `next.config.*`, `nuxt.config.*`, `svelte.config.*`, `gatsby-config.*`,
   `docusaurus.config.*`, `hugo.toml`/`config.toml` with `baseURL`, Jekyll `_config.yml`;
   `wrangler.toml|json|jsonc` with `pages_build_output_dir` or `assets`.
4. `cli`: package.json `bin`; pyproject `[project.scripts]` or `console_scripts`;
   Cargo `[[bin]]` or `src/main.rs` without `src/lib.rs`; Go `package main` at root or
   under `cmd/`; a `bin/` dir with executables; gemspec `executables`.
5. `library`: package.json `main`/`exports`/`module`; pyproject or setup.py; Cargo with
   `src/lib.rs`; `go.mod` without `package main`; gemspec; `composer.json`; `pom.xml`
   or `build.gradle*`.
6. `other`.

Language: GitHub `language` when available, else the extension with the most bytes
among tracked source files (map in `detect.py`). TOML parsing uses `tomllib` when
present, else a small regex reader for the keys above
(`# ponytail: regex TOML reader, handles flat tables only`).
Every check, fix, template and launch choice that depends on type reads this result.
`--type` overrides are accepted by `audit.py`, `fix.py`, `visuals.py`, `launch.py`.

### 7.4 audit.py

```
python3 scripts/audit.py [TARGET] [--json] [--offline] [--rubric FILE]
                         [--type TYPE] [--as-of YYYY-MM-DD]
                         [--facts FILE] [--save-facts FILE]
```
- `--rubric`: defaults to `./.leadout/rubric.json` when it exists in the current
  directory, else `scripts/rubric.json`.
- `--facts`: evaluate saved facts without any network (used by tests and to re-score).
- `--save-facts`: also write the collected facts (new file only).
- `--as-of`: reference date for time-based checks (default: today, UTC).
- Library: `load_rubric(path)`, `evaluate(facts, rubric, project_type) -> result dict`,
  `render_markdown(result) -> str`.
- Never writes inside the target repository. `--save-facts` refuses a path inside the
  target's working tree.

### 7.5 fix.py

```
python3 scripts/fix.py plan [TARGET] [--audit FILE] [--type TYPE] [--json]
python3 scripts/fix.py render ACTION [TARGET] [--type TYPE]
python3 scripts/fix.py write ACTION [TARGET] [--type TYPE] [--overwrite] [--content-file FILE]
python3 scripts/fix.py badges [TARGET] [--no-verify] [--json]
python3 scripts/fix.py topics [TARGET] [--json]
python3 scripts/fix.py release-notes [TARGET] [--from TAG] [--to REF]
```
- `plan`: runs `audit.py --json` itself (or reads `--audit FILE`, `-` for stdin) and lists actions
  (section 10.2) as markdown or `--json`: `{"schema": 1, "project_type", "score", "actions": [{"id",
  "check_ids", "kind": "file"|"metadata"|"release"|"manual", "paths", "exists", "summary",
  "todos"}]}`. The audit JSON must have `schema` 1 and a `checks` list (section 9.6), else
  `E_BAD_ARGS`. An action appears when one of its checks has status `fail` or `partial`;
  `skip` and `unknown` never trigger one. `readme` is triggered by `readme.*` checks whose
  `phase` is `fix` (so `readme.visual_demo` is left to visuals) except `readme.contributing`.
  `badges` appears when the README's first 30 lines hold no badge. `ci` becomes `manual`,
  with the reason, when the language has no template.
- `render`: prints the generated content for a file action to stdout. Pure, no writes.
  Multi-file actions (`issue-templates`) print each file under a `==> path <==` header.
- `write`: writes a file action into TARGET. Refuses with `E_NOT_GIT`, `E_DEFAULT_BRANCH`
  (also on detached HEAD), `E_FILE_EXISTS` (without `--overwrite`). `--content-file`
  writes reviewed content (used for the README rewritten by Claude) instead of the raw
  render. Prints the paths and the TODO list. Never stages or commits. Extra guards (S3): `readme` over an
  existing README, and `badges` always, require `--content-file`, since the raw template would
  drop content. All subcommands take `--offline` and `--type`; `render` never verifies badges
  (`badges` does); `release-notes` takes `--title`.
- `badges`: markdown badge line built only from real data (section 10.4).
- `topics`: proposed topics plus the exact `gh repo edit <slug> --add-topic ...` command,
  which it prints and never runs.
- `release-notes`: markdown notes from `git log <from>..<to>` (defaults: latest semver
  tag → `HEAD`), grouped by Conventional Commit type, other commits under
  "Other changes". Unknown sections are omitted, never invented.

### 7.6 visuals.py

```
python3 scripts/visuals.py plan [TARGET] [--type TYPE]
python3 scripts/visuals.py compress INPUT --out FILE.gif [--max-mb 5] [--width 960] [--fps 12] [--overwrite]
python3 scripts/visuals.py social-preview [TARGET] --out FILE.svg [--title T] [--tagline T] [--theme light|dark] [--accent HEX] [--overwrite]
python3 scripts/visuals.py banner [TARGET] --out-dir DIR [--title T] [--tagline T] [--accent HEX] [--overwrite]
python3 scripts/visuals.py to-png INPUT.svg --out FILE.png [--width W --height H] [--overwrite]
```
`plan` and the two SVG generators also take `--offline`; `social-preview` takes `--theme`. Details in
section 11.

### 7.7 launch.py

```
python3 scripts/launch.py init [TARGET] [--out launch] [--type TYPE] [--launch-date YYYY-MM-DD] [--timezone TZ] [--overwrite]
python3 scripts/launch.py subreddits [--type TYPE] [--language LANG] [--json]
python3 scripts/launch.py awesome [TARGET] [--limit 10] [--json]
python3 scripts/launch.py calendar --launch-date YYYY-MM-DD [--timezone TZ] [--json]
python3 scripts/launch.py lint [--dir launch]
```
`init` also takes `--offline`; `awesome` takes `--type`. Details in section 12.

### 7.8 stats.py

```
python3 scripts/stats.py collect [TARGET] [--history FILE] [--no-save] [--json] [--max-star-pages 10] [--source local|actions]
python3 scripts/stats.py report  [TARGET] [--history FILE ...] [--launch-date YYYY-MM-DD] [--window 7] [--days 30] [--json]
python3 scripts/stats.py install-workflow [TARGET] [--write] [--overwrite]
```
- Without subcommand: `collect` then `report` with the same TARGET.
- TARGET must resolve to a slug; a local path without a GitHub origin → `E_BAD_TARGET`.
- `--history` default: `./.leadout/stats.jsonl` (relative to the current directory);
  `report` accepts it several times and merges (section 13.4).
Details in section 13.

---

## 8. RepoFacts format

```json
{
  "schema": 1,
  "as_of": "2026-11-25",
  "target": {"kind": "remote", "slug": "owner/name", "path": null},
  "repo": {
    "private": false,
    "archived": false,
    "fork": false,
    "has_issues": true,
    "description": "Text or empty string",
    "homepage": "https://example.org or empty string",
    "topics": ["cli", "rust"],
    "default_branch": "main",
    "license_spdx": "MIT",
    "uses_custom_og_image": true,
    "stars": 120,
    "forks": 8,
    "subscribers": 5,
    "primary_language": "Rust"
  },
  "files": [".github/workflows/ci.yml", "LICENSE", "README.md"],
  "files_truncated": false,
  "readme": {"path": "README.md", "text": "# name\n..."},
  "manifests": {"package.json": {"name": "x", "bin": {}}, "pyproject.toml": {}},
  "last_commit_at": "2026-11-20T10:00:00Z",
  "releases": [
    {"tag": "v1.2.0", "published_at": "2026-10-01T12:00:00Z", "draft": false,
     "prerelease": false, "body_chars": 420,
     "assets": [{"name": "tool-x86_64.tar.gz", "download_count": 42}]}
  ],
  "tags": ["v1.2.0", "v1.1.0"],
  "ci": {
    "workflow_files": [".github/workflows/ci.yml"],
    "workflows_with_runs": [".github/workflows/ci.yml"],
    "other_ci_files": [],
    "latest_default_branch_run": {"conclusion": "success", "created_at": "2026-11-20T10:05:00Z"}
  },
  "issues": {
    "open_sample": [{"number": 12, "created_at": "2026-11-01T08:00:00Z", "comments": 0}],
    "open_sample_complete": true,
    "gfi_labels": ["good first issue"],
    "gfi_open": 2
  },
  "community": {"code_of_conduct": true, "contributing": true, "issue_template": true,
                "pull_request_template": false},
  "ext_bytes": {".md": 4000, ".rs": 12000},
  "unavailable": {"repo": "offline"}
}
```
Rules: `files` holds repo-relative POSIX paths, sorted, without `.git/`. Any section
that could not be collected is `null` with the reason in `unavailable` (key = section
name, value = short reason: `offline` (asked by the user), `no_network` (GitHub unreachable,
local target audited from its files; remote targets fail with E_NETWORK), `no_remote`, `forbidden`, `disabled` (issues
turned off), `not_found` (community profile), `no_git`, `no_commits`). `ext_bytes`
(added in S2) maps each file extension to its total bytes; `detect.py` uses it for the
language when GitHub reports none (offline, or no remote). `manifests`
keeps only the keys `detect.py` and `fix.py` need (name, version, bin, main, exports,
module, scripts, dependency names, keywords, requires-python, project.scripts,
package.name, lib/bin targets, module path). README text is capped at 200 KB.

---

## 9. Audit rubric and scoring

### 9.1 Rubric JSON format (`scripts/rubric.json`)

```json
{
  "schema": 1,
  "name": "leadout default",
  "version": "1.0.0",
  "effort_levels": {"low": 1, "medium": 2, "high": 3},
  "grades": [[90, "A"], [75, "B"], [60, "C"], [40, "D"], [0, "F"]],
  "categories": [
    {"id": "readme", "title": "README"},
    {"id": "metadata", "title": "Metadata"},
    {"id": "distribution", "title": "Distribution"},
    {"id": "community", "title": "Community"},
    {"id": "health", "title": "Health"}
  ],
  "checks": [
    {
      "id": "readme.tagline",
      "category": "readme",
      "type": "readme_tagline",
      "title": "README opens with one sentence that says what the project does",
      "weight": 6,
      "effort": "low",
      "phase": "fix",
      "enabled": true,
      "skip_types": [],
      "params": {"max_lines": 15, "min_chars": 20, "max_chars": 300},
      "fix": "Open the README with a single sentence: what it is, for whom, and the main benefit."
    }
  ]
}
```

Field rules (validated by `audit.load_rubric`, else `E_RUBRIC_INVALID`):
`schema` = 1; `id` unique, matching `^[a-z]+\.[a-z0-9_]+$`; `category` exists; `type`
is a known evaluator; `weight` a number > 0; `effort` a key of `effort_levels`;
`phase` one of `fix`, `visuals`, `launch`, `manual`; `enabled` optional (default true);
`skip_types` optional list of project types; `params` optional, merged over the
evaluator defaults, unknown params rejected. Weights need not sum to 100: scores are
normalized. Users customize by copying the file to `./.leadout/rubric.json` or
passing `--rubric`.

### 9.2 Check result

Each evaluator is a pure function `(facts, project_type, params) -> (score, evidence)`
where `score` is `None` (unknown), the string `"skip"`, or a float in [0, 1] rounded to
2 decimals. Status: `pass` (1.0), `partial` (0 < s < 1), `fail` (0), `skip`, `unknown`.
`evidence` is one short English sentence with the concrete data seen
(e.g. `First paragraph is 412 characters (max 300).`). A check whose project type is in
`skip_types` is `skip` without running the evaluator.

### 9.3 Score

- Evaluated set E = enabled checks whose status is not `skip` or `unknown`.
- `score = round_half_up(100 × Σ_E weight·s / Σ_E weight)`, an integer 0–100.
- `coverage = round_half_up(100 × Σ_E weight / Σ_(enabled, not skip) weight)`.
  The report always prints coverage; under 100 %, it lists the unknown checks and why.
- `points(check) = 100 × weight / Σ_E weight` (its share of the score);
  `lost = points × (1 − s)`. Both rounded to 1 decimal for display only.
- Category score = Σ points·s over its checks, shown against Σ points.
- Grade from `grades` (first threshold ≤ score).
- `round_half_up(x) = int(Decimal(str(x)).quantize(Decimal(1), ROUND_HALF_UP))`,
  computed from 2-decimal inputs, so identical facts always give the identical score.
- Determinism contract: the same RepoFacts, rubric and `as_of` produce byte-identical
  `--json` output. The JSON includes `rubric.sha256` and `as_of` so two reports can be
  compared.

### 9.4 Fix ordering

Fixes = evaluated checks with s < 1, sorted by `priority = lost / effort_levels[effort]`
descending, then `lost` descending, then `id` ascending. Each fix line shows
`+lost points`, effort, phase and the rubric `fix` text plus the check's evidence.

### 9.5 Default checks (weights sum to 100)

| id | type | w | effort | phase | rule (params in parentheses) |
|---|---|---|---|---|---|
| `readme.exists` | `readme_exists` | 3 | low | fix | README.* at root, `.github/` or `docs/` → 1, else 0. When 0, every other `readme.*` check scores 0 with evidence "no README". |
| `readme.tagline` | `readme_tagline` | 6 | low | fix | Within the first non-empty lines (`max_lines` 15), skip front matter, HTML comments, headings, lines made only of images/badges/links/HTML wrappers (`<p align>`, `<picture>`, `<a><img></a>`, `[![..](..)](..)`). Take the first prose paragraph, strip markdown/HTML. `min_chars` 20 ≤ len ≤ `max_chars` 300 → 1; paragraph present but out of range → 0.5; none, or it starts with `TODO`, `Lorem`, `This project was bootstrapped with` → 0. |
| `readme.visual_demo` | `readme_visual` | 7 | medium | visuals | Media in the first `max_lines` (50) lines: markdown image, `<img>`, `<video>`, `<source>`, or a link to YouTube/asciinema. Badges are excluded: hosts `img.shields.io`, `badgen.net`, `badge.fury.io`, `codecov.io`, `coveralls.io`, `deepwiki.com`, `app.codacy.com`, `api.securityscorecards.dev`, `goreportcard.com`, `pkg.go.dev`, `docs.rs`, `crates.io`, `api.netlify.com`, `snyk.io`, plus any URL whose path contains `badge` or ends with `badge.svg`. GIF/MP4/WebM/MOV, YouTube, asciinema, or PNG/JPG/WebP → 1; only SVG (often a logo) → 0.5; media only after `max_lines` → 0.5; none → 0. |
| `readme.install` | `readme_install` | 6 | low | fix | Section whose heading matches `install|installation|getting started|quick ?start|setup|download`: its first fenced block with exactly 1 command line (comments, blank lines and `$ `/`> ` prompts ignored) → 1; 2–3 lines → 0.5; section without a code block, or > 3 lines → 0.25; none → 0. For `desktop-app`, a link to a release download (`/releases`) in that section also scores 1. For `claude-plugin`, `/plugin install` or `claude plugin install` lines count as commands. `skip_types: ["website"]`. |
| `readme.usage` | `readme_usage` | 5 | medium | fix | Heading matching `usage|example|examples|quick ?start|getting started|tutorial|how to use|demo|commands` whose section has a fenced code block, a table or an image → 1; heading without any → 0.5; none → 0. |
| `readme.contributing` | `readme_contributing` | 3 | low | fix | Heading matching `contribut` or a link to `CONTRIBUTING` → 1, else 0. |
| `readme.length` | `readme_length` | 5 | medium | fix | Words outside code blocks and HTML tags. `min_words` 300 ≤ n ≤ `max_words` 5000 → 1; 150 ≤ n < 300 or 5000 < n ≤ 10000 → 0.5; else 0. |
| `meta.description` | `meta_description` | 5 | low | fix | Repo description 10–350 characters and not equal to the repo name → 1; shorter or equal to the name → 0.5; empty → 0. Unknown offline. |
| `meta.topics` | `meta_topics` | 5 | low | fix | `count = min(n / min_count(5), 1)`. A topic is pertinent if one of its `-`-separated tokens of ≥ 3 chars appears (case-insensitive) in the description, README text, primary language, detected type or frameworks. `s = round(count × pertinent / n, 2)`; n = 0 → 0. Evidence lists non-pertinent topics. Unknown offline. |
| `meta.homepage` | `meta_homepage` | 3 | low | fix | Homepage set to an http(s) URL → 1, else 0. Unknown offline. |
| `meta.social_preview` | `meta_social_preview` | 4 | medium | visuals | `uses_custom_og_image` true → 1, false → 0, null → unknown. |
| `meta.license` | `meta_license` | 3 | low | fix | GitHub SPDX id present and not `NOASSERTION` → 1; license file present but SPDX `NOASSERTION`/null, or offline with a license file → 0.5; no license → 0. |
| `dist.release` | `dist_release` | 6 | medium | fix | Latest non-draft release has ≥ 1 asset → 1; README installs from a registry (`pip install`, `pipx install`, `uv tool install`, `uvx`, `npm i|install`, `npx`, `pnpm add`, `yarn add`, `cargo install`, `go install`, `brew install`, `gem install`, `composer require`, `docker pull|run`, `winget install`, `scoop install`) with the manifest's package name → 1; `claude-plugin` with `.claude-plugin/marketplace.json` → 1; release without assets → 0.5; none → 0. `skip_types: ["website"]`. Releases unknown offline; the README/marketplace paths still score offline. |
| `dist.semver` | `dist_semver` | 5 | low | fix | Semver regex `^v?\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$`. Latest release tag (or newest tag if no release) is semver → 1; some tags semver → 0.5; none → 0. Local: tags from `git tag`. `skip_types: ["website"]`. |
| `dist.changelog` | `dist_changelog` | 4 | low | fix | File `CHANGELOG*`, `CHANGES*`, `HISTORY*`, `NEWS*`, `RELEASES*` at root or `docs/` → 1; latest release `body_chars` ≥ 50 → 1; releases with shorter bodies → 0.5; none → 0. |
| `comm.contributing` | `comm_file` | 3 | low | fix | `CONTRIBUTING(.md|.rst|.txt)` at root, `.github/` or `docs/`, or the community profile reports it (org default) → 1, else 0. `params.file: "contributing"`. |
| `comm.code_of_conduct` | `comm_file` | 2 | low | fix | Same rule for `CODE_OF_CONDUCT`. |
| `comm.issue_templates` | `comm_file` | 3 | low | fix | `.github/ISSUE_TEMPLATE/*.md|yml|yaml` other than `config.yml`, or `ISSUE_TEMPLATE.md` at root/`.github/`/`docs/`, or the community profile → 1, else 0. |
| `comm.pr_template` | `comm_file` | 2 | low | fix | `pull_request_template.md` (any case) at root, `.github/`, `docs/`, or a `PULL_REQUEST_TEMPLATE/` dir, or the community profile → 1, else 0. |
| `comm.good_first_issue` | `comm_gfi` | 3 | medium | manual | A label in (`good first issue`, `good-first-issue`, `first-timers-only`, `beginner`, `easy`), case-insensitive, with ≥ 1 open issue → 1; label exists, 0 open → 0.5; no label → 0; issues disabled → skip. Unknown offline. |
| `comm.security` | `comm_file` | 2 | low | fix | `SECURITY.md` at root, `.github/` or `docs/` → 1, else 0. |
| `health.recent_commit` | `health_recent_commit` | 6 | high | manual | Days since `last_commit_at` relative to `as_of`: ≤ `fresh_days` 90 → 1; ≥ `stale_days` 365 → 0; in between, linear, 2 decimals. Archived → 0 with the archived warning. |
| `health.ci` | `health_ci` | 5 | medium | fix | No CI file (`.github/workflows/*.yml|yaml`, `.gitlab-ci.yml`, `.circleci/config.yml`, `.travis.yml`, `azure-pipelines.yml`, `Jenkinsfile`, `.buildkite/`) → 0; Actions present and latest completed default-branch run `success` → 1; present with an unknown result (no runs, other CI, offline) → 0.5; latest run failed → 0.25. |
| `health.issue_response` | `health_issue_response` | 4 | medium | manual | Among open issues (sample of newest 100, PRs excluded), unanswered = `comments == 0` and older than `response_days` 7. `ratio ≤ tolerance` 0.1 → 1; else `round(1 − ratio, 2)`. No open issues → 1. Issues disabled → skip. Unknown offline. |

Category totals: README 35, Metadata 20, Distribution 15, Community 15, Health 15.

Clarifications fixed in S2 (the table above stays the rule; these settle cases it left open):
- `readme.visual_demo`: a media URL whose last path segment has no extension (for example
  `github.com/user-attachments/assets/<id>`, GitHub's upload link) counts as an image or
  video (1). Such a bare URL on its own line also counts, as GitHub renders it. HTML
  comments and fenced code are ignored. Reference-style images `![a][ref]` are not seen.
- `readme.install`: when several headings match, the best scoring section counts. A
  section runs to the next heading of the same or higher level, so subsections
  (`### macOS`) belong to it. For `claude-plugin`, a `/plugin install` or
  `claude plugin install` line anywhere in the section (fenced or inline code) scores 1.
- `readme.length`: the 150 and 10000 bounds are `min_words / 2` and `max_words × 2`.
- `dist.release`: the package names are the manifest `name`/`module` values plus the
  repository name; URLs after the command are ignored, so
  `pip install git+https://github.com/o/r` is not a registry install. `pip3 install`
  counts like `pip install`.
- `comm.good_first_issue` and `health.issue_response` return `skip` with the evidence
  `issues are disabled` when the repository has issues turned off.
- Evidence of `skip` is the reason (`not applicable to website`); evidence of `unknown`
  is the `unavailable` reason (`offline`, `no_remote`, ...).

### 9.6 Audit report

Markdown (stdout by default):

```markdown
# Launch readiness: owner/name: 72/100 (C)

Audited 2026-11-25 · rubric "leadout default" 1.0.0 · coverage 100% · type: cli (detected: package.json bin)

| Category | Score |
|---|---|
| README | 28.0 / 35.0 |
| Metadata | 14.0 / 20.0 |
| Distribution | 12.5 / 15.0 |
| Community | 8.0 / 15.0 |
| Health | 9.5 / 15.0 |

## Fixes, highest impact per effort first

| # | Fix | Gain | Effort | Phase |
|---|---|---|---|---|
| 1 | Add a demo GIF or screenshot in the first screen of the README. No media in the first 50 lines. | +7.0 | medium | visuals |

## All checks

| | Check | Evidence |
|---|---|---|
| ✅ | README opens with one sentence ... | First paragraph is 96 characters. |
| ⚠️ | ... | ... |
| ❌ | ... | ... |
| ➖ | ... (skipped: not applicable to website) | |
| ❔ | ... (unknown: offline) | |
```

The numbers above are illustrative of the format only.

`--json` output: `{"schema": 1, "target", "as_of", "rubric": {"name", "version", "sha256"},
"project_type", "detection": {"type", "evidence", "language", "frameworks", "overridden"},
"score", "grade", "coverage", "categories": [{"id", "title", "points", "max"}],
"checks": [{"id", "category", "title", "status", "score", "points", "lost", "evidence",
"phase", "effort"}], "fixes": [{"rank", "id", "gain", "effort", "phase", "text"}],
"warnings": [{"code", "message"}]}`. `detection` (added in S2) feeds the report's
"type: … (detected: …)" line; `overridden` is true when `--type` replaced the detected
type. `checks[].score` is `null` for `skip` and `unknown`; disabled checks are left out.
`rubric.sha256` is the SHA-256 of the rubric file bytes.

---

## 10. Fix phase

### 10.1 Flow (skill `repo-fix`)

1. Require a local git checkout (`E_NOT_GIT` otherwise; for a remote target, tell the
   user to clone it first and stop).
2. Run `fix.py plan`. Show the actions with the files each one creates or changes,
   and the TODOs expected. Ask which actions to apply (default: all `file` actions).
3. If the tree is dirty, stop with `E_DIRTY_TREE` guidance. Ask for approval to create
   branch `leadout/fix-YYYYMMDD` (suffix `-2`, `-3` if taken) from the current
   HEAD, then run `git switch -c`.
4. For each selected new file: `fix.py write ACTION`, after one batch approval
   listing all new files.
5. For each existing file (README, CONTRIBUTING...): produce the new content, show the
   diff (`git diff --no-index -- <old> <new-temp>` with the temp file in the session's
   scratch space, never inside the repo), ask, then `fix.py write ACTION --overwrite
   --content-file <temp>`.
6. Show `git diff --stat` and the full diff of the branch. Ask to commit, showing the
   message (`docs: improve launch readiness (leadout)` plus a body listing the
   actions). Commit only on yes.
7. Ask to push, showing remote and branch. Push only on yes. Offer `gh pr create
   --draft` as a separate approval.
8. Metadata (topics, description, homepage): show the exact `gh repo edit` command
   per change; run each only on its own yes.
9. Release: when `dist.release`/`dist.semver` fail and the user wants one, draft notes
   with `fix.py release-notes`, show them, and on yes run
   `gh release create <tag> --draft --notes-file <temp>`. Never a non-draft release
   without a second explicit yes.
10. Re-run the audit and show before/after score and remaining TODOs.

### 10.2 Actions

| id | kind | creates/changes | from checks |
|---|---|---|---|
| `readme` | file | `README.md` | `readme.*` |
| `badges` | file (part of readme) | badge line at README top | `readme.*` (presentation) |
| `contributing` | file | `CONTRIBUTING.md` | `comm.contributing`, `readme.contributing` |
| `code-of-conduct` | file | `CODE_OF_CONDUCT.md` | `comm.code_of_conduct` |
| `security` | file | `SECURITY.md` | `comm.security` |
| `issue-templates` | file | `.github/ISSUE_TEMPLATE/{bug_report.yml,feature_request.yml,config.yml}` | `comm.issue_templates` |
| `pr-template` | file | `.github/pull_request_template.md` | `comm.pr_template` |
| `ci` | file | `.github/workflows/ci.yml` | `health.ci` |
| `changelog` | file | `CHANGELOG.md` (Keep a Changelog, entries from tags/releases only) | `dist.changelog` |
| `license` | manual | tells the user to choose a license (link https://choosealicense.com); never picks one | `meta.license` |
| `topics` | metadata | `gh repo edit --add-topic` | `meta.topics` |
| `description` | metadata | `gh repo edit --description` (text written with the user) | `meta.description` |
| `homepage` | metadata | `gh repo edit --homepage` (only a URL the user gives or one already in the README/manifest) | `meta.homepage` |
| `release` | release | draft release | `dist.release`, `dist.semver` |
| `good-first-issues` | manual | suggests up to 5 open issues that look small; labeling each needs its own yes | `comm.good_first_issue` |
| `social-preview` | manual | points to `/leadout:visuals` and the Settings upload | `meta.social_preview` |

### 10.3 README rewrite

- Skeleton: `templates/readme/<type>.md`, rendered by `fix.py render readme` with facts:
  `name`, `slug`, `tagline` (repo description), `badges`, `install_command` (only from
  evidence: the existing README install block, the registry name in a manifest when the
  README already installs from that registry, `/plugin install` for plugins, a release
  download link for desktop apps), `usage_example` (existing README block only),
  `license_name`, `demo_path` (existing media in the repo, else TODO).
- Claude merges the existing README into the skeleton. It keeps every true fact and
  section the old README had (nothing is dropped silently), reorders to the template's
  order, and adds `TODO(...)` for gaps. Every sentence must trace to the old README,
  code, manifests, or the user's words in the conversation. The skill states this rule
  and lists TODOs at the end.
- Section order per type:
  - `cli`: title, badges, tagline, demo, install (one command), quick start, usage/commands, configuration, contributing, license.
  - `library`: title, badges, tagline, install, minimal example with output, demo if any, API/docs link, compatibility, contributing, license.
  - `desktop-app`: title, badges, tagline, screenshot/GIF, download (releases link per OS), features (from evidence only), build from source, contributing, license.
  - `website`: title, badges, tagline, live link, screenshot, run locally, deploy, contributing, license.
  - `claude-plugin`: title, badges, tagline, demo, install (`/plugin marketplace add` + `/plugin install`), commands/skills table, example, contributing, license.
  - `other`: title, tagline, demo, install, usage, contributing, license.

### 10.4 Badges

Built from facts; each URL uses the real `owner/repo`:
- License: `https://img.shields.io/github/license/<o>/<r>` only if `license_spdx` is set.
- CI: `https://github.com/<o>/<r>/actions/workflows/<file>/badge.svg` per workflow file
  in `ci.workflows_with_runs` (link to the workflow page).
- Release: `https://img.shields.io/github/v/release/<o>/<r>` only if a non-draft release exists.
- Last commit: `https://img.shields.io/github/last-commit/<o>/<r>`.
- Registry (npm, PyPI, crates.io): only when the README already installs that exact
  package name from that registry.
- Verification (default on): `urllib.request` GET of each shields.io SVG
  (timeout 10 s, User-Agent `leadout`); a badge whose SVG text contains
  `not found`, `invalid`, `inaccessible`, `no releases` or `repo not found` is dropped
  with a warning. `--no-verify` skips the check and says so. These are the only
  non-GitHub HTTP calls in the plugin.

### 10.5 Topics

Candidates: primary language, detected type (`cli`, `library`, `desktop-app`,
`website`, `claude-code-plugin`), frameworks found in manifests (fixed allow-list in
`fix.py`), manifest keywords. Normalized to GitHub's format
`^[a-z0-9][a-z0-9-]{0,49}$`. Existing topics and non-pertinent candidates
(rule of `meta.topics`) are dropped. Total after the change ≤ 20. Output: list plus
the `gh repo edit` command.

### 10.6 CI templates

`ci-python.yml` (setup-python matrix of supported minor versions read from
`requires-python` when present, else latest two; install `-e .` or
`requirements.txt`; `python -m pytest` if a tests folder exists, else
`python -m compileall -q .`), `ci-node.yml` (setup-node LTS, `npm ci` with lockfile else
`npm install`, `npm test --if-present`, `npm run build --if-present`), `ci-go.yml`
(`go build ./...`, `go test ./...`), `ci-rust.yml` (`cargo build --locked` if a lockfile
exists, `cargo test`). Other languages: the `ci` action is not offered and the plan says
why. Action versions are pinned to the current major, checked on 2026-11-25 with
`gh api repos/actions/<name>/releases/latest`: `actions/checkout@v7`, `actions/setup-python@v7`,
`actions/setup-node@v7`, `actions/setup-go@v7`. The Python matrix runs from the `requires-python`
lower bound to 3.15 (constant `PY_LATEST_MINOR` in `fix.py`, newest stable minor on that date). Workflows declare
`permissions: contents: read`.

### 10.7 Community templates

- `CONTRIBUTING.md`: setup, run tests, branch/PR flow, commit style, where to ask;
  commands filled from detected tooling or `TODO(...)`.
- `CODE_OF_CONDUCT.md`: adopts Contributor Covenant 2.1 by reference (link to
  https://www.contributor-covenant.org/version/2/1/code_of_conduct/) with a contact
  `TODO(contact email)`; the template does not paste the full text.
- `SECURITY.md`: supported versions (from tags), private reporting via GitHub Security
  Advisories (`https://github.com/<o>/<r>/security/advisories/new`), response time
  `TODO(...)`.
- Issue forms (YAML): bug report (version, OS, steps, expected, actual, logs) and
  feature request; `config.yml` with `blank_issues_enabled: true`.
- PR template: summary, linked issue, how tested, checklist.

---

## 11. Visuals phase

### 11.1 Flow (skill `repo-visuals`)

1. `visuals.py plan` → show the GIF storyboard and the commands for the tools present.
2. The user records. Claude never records the screen on its own initiative. Then
   `visuals.py compress` brings the GIF under the limit.
3. `visuals.py social-preview` → SVG, then `to-png`; tell the user to upload the PNG
   in Settings → Social preview.
4. `visuals.py banner` → light/dark SVGs plus the `<picture>` snippet for the README
   (the insertion goes through the repo-fix rules: diff + confirmation).

### 11.2 GIF script (first 5 seconds)

Per type: what is on screen at 0 s, 1–3 s, 3–5 s, then the optional rest (≤ 20 s total).
- `cli`: terminal ready with a large font → type the one install/run command → output
  that shows the main result.
- `library`: short code snippet (≤ 8 lines) → run → result.
- `desktop-app`: app window already open → the main action → visible result.
- `website`: landing page → the key interaction → result state.
- `claude-plugin`: Claude Code prompt → slash command → result.
Rules: 1280×720 or 960×540 window, no personal data or tokens on screen, high-contrast
theme, no idle frames over 1 s, loop cleanly.

### 11.3 Recording and compression commands

`plan` detects tools with `shutil.which`: `ffmpeg`, `gifski`, `vhs`, `asciinema`, `agg`,
`screencapture` (macOS), `wf-recorder`. It prints only commands for tools present, plus
install hints for missing ones (`brew install gifski`, `apt install ffmpeg`...).

`compress` ladder (stop at the first result ≤ `--max-mb`):
1. gifski when present: `ffmpeg -i IN -vf fps=F,scale=W:-1:flags=lanczos frames/%05d.png`
   then `gifski --fps F --width W --quality 80 -o OUT frames/*.png` (frames in a temp dir).
2. ffmpeg palette: `ffmpeg -i IN -vf "fps=F,scale=W:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" OUT`.
3. Retry with (W, F) = (960, 12) → (800, 10) → (640, 10) → (640, 8).
If still too large: report the size and suggest trimming. Neither tool present →
`E_TOOL_MISSING`. It never overwrites without `--overwrite` and reports the final size,
dimensions and frame rate.

### 11.4 Social preview (1280×640 SVG)

Generated from `templates/visuals/social-preview.svg` with `[[...]]` values: title
(repo name), tagline (description, wrapped to ≤ 3 lines by an average-character-width
estimate, `# ponytail: width estimate, no font metrics`), `owner/repo`, type label,
primary language, accent color (default `#2f81f7`), theme. Content stays within a 40 px
safe margin (GitHub crops on some surfaces). Font stack: `-apple-system, "Segoe UI",
Roboto, Helvetica, Arial, sans-serif`. All text XML-escaped. No external references,
so the SVG renders anywhere.

### 11.5 Banner

`banner-light.svg` and `banner-dark.svg` (1280×320) plus the printed snippet:
```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.svg">
  <img alt="[[name]]: [[tagline]]" src="assets/banner-light.svg" width="100%">
</picture>
```

### 11.6 SVG → PNG

`to-png` tries, in order: `rsvg-convert -w W -h H`, `inkscape --export-type=png`,
`magick` (ImageMagick 7) / `convert`, then headless Chrome/Chromium/Edge
(`--headless --disable-gpu --screenshot=OUT --window-size=W,H file://IN`, located via
`shutil.which` and the standard macOS app paths). It reads the PNG IHDR with `struct` to
verify the size. If no converter exists, it prints the manual options and exits with
`E_TOOL_MISSING`.

---

## 12. Launch phase

### 12.1 Flow (skill `launch-kit`)

1. `launch.py init` creates `launch/` with the template files, prefilled with facts
   and `TODO(...)` slots. Existing files are untouched (overwrite needs confirmation and
   `--overwrite`).
2. Claude writes the prose in each file from facts only: README, audit, the user's
   answers. It asks the user up to 5 short questions first (who it is for, why they
   built it, what is new versus alternatives, a real limitation, launch date and
   timezone).
3. `launch.py awesome` → fill `awesome-lists.md`.
4. `launch.py calendar` → fill `calendar.md`.
5. `launch.py lint` must pass (exit 0) before the phase is reported done. Remaining
   TODOs are listed.
6. Closing message: the plugin does not publish; the user posts by hand from their
   own single account.

### 12.2 Files in `launch/`

| File | Content |
|---|---|
| `show-hn.md` | 3 title options `Show HN: <name> – <what it does>` (≤ 80 chars, no superlatives, no emoji, no exclamation marks), the URL to submit (repo or live demo), first-comment text (why built, how it works, what is rough, what feedback is wanted), links to https://news.ycombinator.com/showhn.html and https://news.ycombinator.com/newsguidelines.html, a reminder that votes and comments are never solicited. |
| `reddit.md` | One section per fitting subreddit (from `subreddits.json` by type and language): rule summary + rules link + flair, then a post written for that community (different angle, title and body each time). A note on Reddit's self-promotion guidance with a link to Reddit's own policy page (verified in S4). |
| `x-thread.md` | 4–7 posts, each ≤ 280 characters (URLs count as 23), first post = what it is + demo media; at most 2 hashtags. |
| `devto.md` | Front matter (`title`, `published: false`, `tags` ≤ 4, `canonical_url` when cross-posting), article outline + draft: problem, approach, how it works, limitations, link. |
| `awesome-lists.md` | Table of relevant lists (name, stars, last push, link to its contributing guide) plus PR instructions: read the list's CONTRIBUTING, check the quality bar (often a minimum age or stars), add one line in the right section in the list's exact format, one PR per list, no follow-up pinging. |
| `calendar.md` | Ordered schedule with dates and times in the user's timezone (section 12.4). |
| `checklist.md` | Launch-day checklist: audit ≥ 80, README demo loads, install command tested on a clean machine, release published, social preview uploaded, issues and discussions enabled, notifications on, time blocked to answer comments for the first hours, `stats.py collect` run before and after. |
| `objections.md` | Prepared answers to predictable objections, written from facts or left as TODO: "why not <alternative>?", "is it maintained?", license, privacy/telemetry, security, performance (numbers only from user-provided benchmarks, else TODO), pricing/business model, platform support, "is this AI-generated?". |

Format the lint parses (S4): `show-hn.md` titles are the list items under a heading that contains
"title"; `reddit.md` has one `## r/<name>` section per community with a `**Title:**` line and a
`**Body:**` block; `x-thread.md` posts start with `### Post N`; `devto.md` has `---` front matter
with `title`, `published` and `tags`. With no verified subreddit, `init` writes a single
`## r/TODO(subreddit)` stub.

### 12.3 subreddits.json

```json
{
  "schema": 1,
  "subreddits": [
    {
      "name": "commandline",
      "url": "https://www.reddit.com/r/commandline/",
      "rules_url": "https://www.reddit.com/r/commandline/about/rules/",
      "fits_types": ["cli"],
      "fits_languages": [],
      "self_promotion": "One or two sentences summarising the subreddit's self-promotion rules, written from the live rules page.",
      "flair": "Flair required for project posts, or null",
      "verified_at": "YYYY-MM-DD"
    }
  ]
}
```
S4 tried to build the list by reading each candidate's live rules page. Reddit answered every
automated request with HTTP 403 on 2026-11-25 (www, old, oauth, and its help center), so the
shipped list is empty. Add an entry only after reading that community's live rules. Entries whose rules
cannot be read are left out, never guessed. Candidates to check: opensource,
coolgithubprojects, SideProject, commandline, selfhosted, programming, Python, rust,
golang, javascript, node, webdev, reactjs, macapps, linux, ClaudeAI. `launch.py
subreddits` filters by type and language (`fits_types`/`fits_languages`; an empty list
means any). Entries older than 180 days print a "re-check the rules" warning.

### 12.4 calendar.json

Slots relative to launch day `D`, each with a time in a reference zone, converted with
`zoneinfo` to `--timezone` (default: the system zone; `E_BAD_ARGS` for unknown zones):

| When | What |
|---|---|
| D−14 | Run the audit; fix until ≥ 80 |
| D−7 | Visuals done; dev.to draft ready; launch kit lint passes |
| D−1 | Release published; `stats.py collect` baseline |
| D 08:00 America/New_York (Tue–Thu preferred) | Submit Show HN; post the first comment |
| D +2 h | X thread |
| D+1 … D+n | One subreddit per day, each with its own post |
| D+3 | Publish the dev.to post |
| D+7 | Awesome-list PRs (only lists whose quality bar the project meets) |
| D+14 | `stats.py report --launch-date D` |

These timings are common practice, not measured data. The calendar says so in a
single line. If `--launch-date` falls on Fri–Mon, the calendar keeps it and notes the
weekday preference.

### 12.5 lint rules (`launch.py lint`)

Errors (exit 1):
- Vote or star requests (case-insensitive regexes): `\bupvote`, `\bvote (for|up)\b`,
  `\b(please|pls) (star|vote)`, `\bgive (it|us|me) a star\b`, `\bstar (it|this|the repo)\b`,
  `star[- ]for[- ]star`, `\bstar exchange\b`, `\bsupport (us|me) by (starring|voting)\b`.
  A line that states the rule itself (e.g. in `checklist.md`) is allowed when it ends
  with the marker `<!-- lint-allow -->`.
- Two reddit post bodies with `difflib.SequenceMatcher(None, a, b).ratio() ≥ 0.6`,
  or identical titles.
- Show HN title not starting with `Show HN: ` or over 80 characters.
- Any X post over 280 characters (URLs counted as 23).
- dev.to `tags` > 4 or `published: true`.
Warnings (exit 0): remaining `TODO(` count per file; superlatives in titles
(`best`, `fastest`, `revolutionary`, `ultimate`, `world's first`).

### 12.6 awesome-list search

`launch.py awesome` calls `gh api search/repositories` with queries built from topics,
language and type: `awesome <term> in:name topic:awesome`, `awesome-<term> in:name`.
It keeps repos pushed in the last 365 days, not archived, and dedupes. It sorts by stars
descending, then name, and returns up to `--limit`, each with `full_name`, `html_url`,
`stars`, `pushed_at`, `contributing_url` (when a CONTRIBUTING file exists, checked with
one `contents` call per result). Search API limits (30 requests/min) are respected by
issuing at most 5 queries.

---

## 13. Stats phase

### 13.1 Flow (skill `repo-stats`)

1. `stats.py collect` (saves a snapshot) then `stats.py report` with the user's
   `--launch-date` if given. Show the report.
2. If traffic is unavailable, say that it needs push access and show public data only.
3. Offer the daily workflow (`install-workflow`); writing it goes through repo-fix
   rules: new branch, confirmation, diff, commit/push approvals.

### 13.2 Collection

Calls: `repos/{slug}` (stars `stargazers_count`, forks, `subscribers_count`, open issues),
`traffic/views`, `traffic/clones`, `traffic/popular/referrers`, `traffic/popular/paths`,
`releases?per_page=100` (paginated up to 3 pages), and stargazers with
`Accept: application/vnd.github.star+json`. Stargazers are fetched newest first: read
the last page number from the `Link` header (`gh api -i`) and walk backwards up to
`--max-star-pages`. On 404 or an empty list while stars > 0: `available: false`,
warning `W_STARS_HIDDEN`. Traffic 403 → `available: false, error: "no_push_access"`,
warning `W_NO_PUSH`; the other data is still saved.

Stargazer walk (S2): page 1 is read first, with headers, to get the `Link` header; then
pages `last, last−1, …` down to `last − max_star_pages + 1` (never page 1 again). When
that reaches page 2, every page was read and `complete` is true. Otherwise page 1 (the
oldest stars) is dropped, so the stored events form one contiguous recent range. Zero
stars needs no call (`complete: true`, no events). Draft releases are not stored.
Confirmed with recorded responses on 2026-11-25 (`tests/fixtures/gh/hyperfine`):
stargazers 404 and traffic 403 `Must have push access to repository` on a public repo
the account does not own.

### 13.3 History file (`.leadout/stats.jsonl`)

- One JSON object per line, appended; never rewritten except by the user.
- On first write, create `.leadout/` and `.leadout/.gitignore` containing `*`, so
  git ignores the folder without touching any of the user's files.
- Line schema:

```json
{
  "schema": 1,
  "collected_at": "2026-11-25T09:30:00Z",
  "repo": "owner/name",
  "source": "local",
  "public": {"stars": 120, "forks": 8, "watchers": 5, "open_issues": 3},
  "stargazers": {
    "available": true,
    "error": null,
    "complete": false,
    "events": ["2026-11-24T18:03:11Z", "2026-11-25T07:12:40Z"]
  },
  "traffic": {
    "available": true,
    "error": null,
    "views": {"count": 300, "uniques": 120,
              "days": [{"date": "2026-11-12", "count": 20, "uniques": 9}]},
    "clones": {"count": 40, "uniques": 22,
               "days": [{"date": "2026-11-12", "count": 3, "uniques": 2}]},
    "referrers": [{"referrer": "news.ycombinator.com", "count": 120, "uniques": 90}],
    "paths": [{"path": "/owner/name", "title": "owner/name: tagline", "count": 200, "uniques": 100}]
  },
  "releases": [
    {"tag": "v1.0.0", "published_at": "2026-11-20T10:00:00Z",
     "assets": [{"name": "tool.tar.gz", "download_count": 42}]}
  ]
}
```
- `events` stores only `starred_at` timestamps, never usernames (privacy). When
  `available` is false, `events` is `[]`. `complete` is true when every stargazer
  page was read.
- `traffic.available: false` → `views`, `clones`, `referrers`, `paths` are `null`.
- Days in `views.days`/`clones.days` are dates in UTC as GitHub returns them
  (timestamps truncated to `YYYY-MM-DD`).
- A corrupt line is skipped with `E_HISTORY_CORRUPT` as a warning; the report continues.

### 13.4 Report

Merge rules over all snapshots of the slug (from every `--history`, deduped by
`collected_at`):
- Traffic per day: for each date, take the values from the latest snapshot containing
  that date (later readings of a day are more complete). The current UTC day is marked
  partial.
- Stars per day: if star events exist, count events per UTC day (deduped by timestamp
  across snapshots) and mark days before the oldest fetched event as "not covered".
  Otherwise use the change in `public.stars` between consecutive snapshots, credited
  to the later snapshot's date and labeled "from snapshots".
- Forks and watchers: change between consecutive snapshots.
- Release downloads: per tag and asset, latest count and change since the previous
  snapshot.

Sections:
1. Header: repo, collected range, snapshot count, data limits (traffic, star events).
2. Totals now: stars, forks, watchers, views (14 d), unique visitors (14 d), clones
   (14 d), release downloads (all time).
3. Daily growth table for the last `--days` (default 30): date, +stars, views, unique
   visitors, clones, unique cloners. Missing data shows `–`, never 0.
4. Referrers: for the latest snapshot, referrer, views, unique visitors, share of
   views; plus the change versus the most recent snapshot at least 7 days older when one
   exists. A note says that GitHub reports referrers as 14-day totals and does not tie
   stars to referrers: the stars column for the same period is context, not
   attribution.
5. Popular pages: top 10 paths.
6. Before/after launch (with `--launch-date D`): window `W = --window` (default 7) days
   before D (D−W … D−1) versus D … D+W−1. Per metric (stars/day, views/day, unique
   visitors/day, clones/day), it shows the mean before, the mean after, and the change
   (absolute and ×). A side with < 50 % day coverage prints "insufficient data" for
   that metric.
7. Release downloads table.

`--json` returns the same sections as data: `{"schema", "repo", "generated_at",
"header": {"snapshots", "first", "last", "traffic_available", "traffic_as_of",
"stars_source": "events"|"snapshots", "stars_since"}, "totals", "daily": [{"date",
"stars", "views", "uniques", "clones", "clone_uniques", "partial"}], "referrers": null |
{"as_of", "compared_with", "period_stars", "rows": [{"referrer", "count", "uniques",
"share", "change", "new"}]}, "paths", "launch": null | {"date", "window", "metrics":
[{"metric", "before", "after", "change", "ratio"}]}, "releases": [{"tag", "asset",
"downloads", "change"}], "warnings"}`. Missing values are `null` (`–` in markdown).

Details fixed in S2:
- The daily table starts at the first day with any data when that is later than
  `today − days + 1`, so a fresh history does not print 30 empty rows.
- Totals come from the latest snapshot; views/clones from the latest snapshot that has
  traffic (`traffic_as_of`). Changes compare with the previous snapshot.
- Star events: days after the latest snapshot with visible stargazers are unknown.
- Before/after means skip today's partial day; a side is "insufficient data" when
  fewer than half of its `W` days have a value. `ratio` is omitted when the mean before
  is 0.
- `period_stars` (stars in the referrers' 14 days) is printed only when all 14 days are
  known.
- `report` without a launch date omits section 6. `stats.py` without a subcommand runs
  `collect` (saving to the first `--history`) and then `report`; `--json` prints the
  report only.

### 13.5 Daily workflow (`templates/workflows/leadout-stats.yml`)

- Triggers: `schedule` (cron `17 6 * * *`, off the hour to avoid peak load) and
  `workflow_dispatch`.
- `permissions: contents: write` (to push the data branch only).
- Steps: checkout default branch; fetch `leadout-stats` branch into a worktree
  `stats-data/` (create an orphan branch on the first run); run
  `python3 .github/leadout/stats.py collect "$GITHUB_REPOSITORY" --history stats-data/stats.jsonl --source actions`
  with `GH_TOKEN: ${{ secrets.LEADOUT_TOKEN || github.token }}`; commit as
  `github-actions[bot]` and push the data branch.
- Traffic endpoints need a token with push/administration read access, which the
  default `GITHUB_TOKEN` lacks: without the optional `LEADOUT_TOKEN` secret
  (fine-grained PAT, repository access to this repo only, Administration: read-only),
  the workflow saves public data only and the run log says so. The skill explains how
  to create the secret but never handles the token itself.
- `install-workflow` writes `.github/workflows/leadout-stats.yml` and vendors
  `scripts/stats.py` and `scripts/common.py` into `.github/leadout/` (stdlib only, so
  the runner's Python works). Without `--write` it prints the files it would create.
- Locally, merge the Actions history with
  `git fetch origin leadout-stats && git show origin/leadout-stats:stats.jsonl > .leadout/stats-actions.jsonl`,
  then `stats.py report --history .leadout/stats.jsonl --history .leadout/stats-actions.jsonl`.
  The skill runs these read-only git commands with the user's approval through the
  normal permission prompt.
- Note for the user: GitHub disables scheduled workflows after 60 days without
  repository activity.

---

## 14. Plugin README (S6)

Must score ≥ 90 on its own audit (`audit.py .` on this repo, online). Contents in order:
1. First screen, nothing else above the fold: the cover (`docs/cover.png`, name and
   tagline on the logo colours), one sentence, the demo
   (`docs/demo.png`, or the GIF once it exists: a real `/leadout:audit` run, no SVG-only
   demo since the rubric gives SVG 0.5) and the install command
   `claude plugin marketplace add cc-forge/leadout && claude plugin install leadout@leadout`.
2. Commands: two columns, command and one line; details in `docs/commands.md`.
3. Real audits: a table of public repositories of different types, with date and rubric
   version, the JSON outputs in `docs/audits/<date>/`. Real, reproducible data only:
   no stars, downloads or users of leadout itself.
4. Project rules, privacy, requirements, development, roadmap: three lines each at most,
   the rest in `docs/` behind a link (`docs/rules.md`).
5. Contributing, License.
No "Case study" section until real before/after numbers exist: a table of `TODO(...)`
is never shown in public.

---

## 15. Tests

### 15.1 Rules

- `python3 -m unittest discover -s tests -v`; stdlib `unittest` only.
- No network: `tests/helpers.py` sets `LEADOUT_FIXTURES` and patches
  `subprocess.run`, so any `gh` call outside fixture mode fails the test. Badge
  verification is disabled in tests (`--no-verify` path).
- Time is injected (`as_of`, `now`); no test depends on today's date.
- Temp git repos (for `fix.py write` refusals) are created with local `git init` in a
  `tempfile.TemporaryDirectory`.

### 15.2 Fixtures

- `tests/fixtures/gh/<repo-name>/<key>.json`: responses recorded with
  `LEADOUT_RECORD` from real repos (S2/S5/S6), committed after review (no tokens,
  no emails, no user objects beyond `login`).
- `tests/fixtures/facts/*.json`: small RepoFacts for check tests (hand-written, minimal).
- `tests/fixtures/stats/*.jsonl`: history samples, including overlapping 14-day
  windows, a 403 traffic snapshot, hidden stargazers, and a corrupt line.
- S2 recorded `tests/fixtures/gh/hyperfine/` (audit and stats calls on
  `sharkdp/hyperfine`) and `tests/fixtures/gh/errors/` (two real 404 responses). Other
  error samples (rate limit, secondary limit, network, 401) and the responses of a
  repository with push access (traffic, stargazer pages) are written by the tests into
  temporary folders in the same fixture format, since no such repository can be recorded
  without committing its owner's traffic data.
- `tests/fixtures/expected/<repo>.json`: audit `--json` output of recorded repos with a
  fixed `as_of`, used as snapshot tests.

### 15.3 Required tests

- `test_rubric.py`: default rubric loads; weights sum to 100; invalid rubrics (duplicate
  id, unknown type, weight ≤ 0, unknown category, unknown param, bad effort) raise
  `E_RUBRIC_INVALID`; normalization with skip/unknown; coverage; grade thresholds;
  rounding half-up; byte-identical JSON on repeated runs; fix ordering and tie-breaks.
- `test_checks.py`: every evaluator with pass/partial/fail (and skip/unknown when
  applicable), including README edge cases: badges-only first lines, HTML-centered
  header, SVG logo only, demo after line 50, multi-line install block, plugin install
  line, desktop release link, no README.
- `test_audit_e2e.py`: recorded gh fixtures → `audit.py --json` equals the expected file
  for each recorded repo; offline local audit of a fixture directory.
- `test_common.py`: target parsing (paths, https/ssh URLs, `owner/repo`, invalid);
  gh error mapping from recorded stderr samples (404 repo, 404 other, rate limit,
  secondary rate limit, 403 traffic, auth failure, gh missing, network); redaction;
  `render` TODO markers; `run_main` never prints a traceback without `LEADOUT_DEBUG`.
- `test_fix.py`: badges from facts; topic normalization and pertinence; `write` refuses
  on default branch, detached HEAD, existing file; writes on a feature branch;
  release-notes grouping from a temp repo.
- `test_launch_lint.py`: each banned phrase, the lint-allow marker, similar reddit posts,
  title rules, X length with URLs, dev.to tags/published.
- `test_stats.py`: parse views/clones/referrers/paths/releases; 403 → public only;
  hidden stargazers; merge of overlapping windows (latest wins); star events dedupe;
  stars from snapshots; before/after windows and insufficient data; corrupt history line.

### 15.4 Real-world check (S6)

Run `audit.py` (online) on three public repos of different types and fix what breaks:
`sharkdp/hyperfine` (cli, Rust), `psf/requests` (library, Python),
`localsend/localsend` (desktop-app, Dart/Flutter). Then the plugin repo itself
(claude-plugin). Record their gh fixtures and expected outputs with a fixed `as_of`.

---

## 16. Decisions log

| # | Decision | Why |
|---|---|---|
| D1 | Skills hold the logic; commands are thin wrappers with different names | Docs: skills are preferred, and a skill shadows a command of the same name |
| D2 | Skills `user-invocable: false` | One clean slash entry per phase |
| D3 | Write-capable commands never in `allowed-tools` | Harness prompt as a second gate behind conversational confirmation |
| D4 | Commands do not reference `${CLAUDE_PLUGIN_ROOT}` | Docs conflict on expansion in command bodies; skills cover it (expansion verified there) |
| D5 | Facts collection separated from evaluation | Determinism, offline re-scoring, network-free tests |
| D6 | `unknown`/`skip` excluded from the score, coverage printed | Missing data must not look like a failing repo |
| D7 | Placeholder syntax `[[name]]`, missing → visible `TODO(name)` | No clash with Actions/Jinja; no invented content |
| D8 | Star timeline from snapshots; events only when visible | Stargazer listing returned 404 for non-owned repos on 2026-11-25 |
| D9 | History in `./.leadout/` with a self-ignoring `.gitignore` | Required path, without editing the user's files |
| D10 | Daily workflow stores data on an orphan branch `leadout-stats` | Artifacts expire; keeps the default branch clean |
| D11 | Subreddit rules only from live pages, dated, re-check after 180 days | No guessed rules |
| D12 | Badge verification is the only non-GitHub HTTP call | "Working badges" requirement; disabled offline and in tests |
| D13 | Owner `cc-forge` in manifests and LICENSE; repository `cc-forge/leadout` | Authenticated gh account at S1 (`gh api user`); confirmed by the author on 2026-11-25 with the rename to leadout |
| D14 | Time-based checks use `as_of` (default today) | "Same repo, same score" holds for the same repo state and date; tests pin `as_of` |
| D15 | S2 implements both read-only phases (audit and stats); S5 merged | They share `common.py` and the gh fixture machinery, and neither writes to the repository |
| D16 | Skill bodies call scripts with an unquoted `${CLAUDE_PLUGIN_ROOT}` path | The command text must match the `allowed-tools` pattern to be pre-approved |
| D17 | `install-workflow` pre-approved only in its bare form | A trailing `*` would also match `--write` |
| D18 | RepoFacts gains `ext_bytes` | Offline language detection needs byte counts per extension |

| D19 | Skill `allowed-tools` for fix, visuals and launch are narrower than first specified | A trailing `*` matches `--overwrite`, `git branch -D` and `launch.py init`; the harness prompt stays the second gate |
| D20 | `fix.py plan` consumes the audit's `--json` output (SPEC 9.6) and never imports audit code | The audit JSON is the contract between phases; audit and fix stay independent |
| D21 | `subreddits.json` ships empty; `launch.py init` writes an explicit TODO stub | Reddit blocked automated reads (HTTP 403); unread rules are never guessed (D11) |
| D22 | `slug_of` and `repo_name` live in `common.py` | Used by fix, visuals and launch; section 6.1 keeps shared code in one module |
| D23 | Each command pre-approves only its own skill: `allowed-tools: Skill(leadout:<skill>)`; `all` lists the four pipeline skills | Live run 2026-11-25: without it the Skill call itself needed approval before any script ran |

## 17. Open items

- Case study in the README: add it only with real before/after numbers from a launch,
  provided by the author (removed from the public README until then).
- Fill `templates/launch/subreddits.json` after reading each community's live rules page (Reddit
  blocked automated reads on 2026-11-25).
- Optional: record traffic and stargazer-page fixtures from a repository the owner can
  push to, if they agree to commit its traffic numbers (today they are synthetic).
