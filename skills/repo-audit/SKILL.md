---
name: repo-audit
description: Audit a GitHub repository's launch readiness (README, metadata, distribution, community, health) with a deterministic 0-100 score and fixes ranked by impact per effort. Read-only. Works on a local path or a remote owner/repo.
when_to_use: The user asks to audit, score, review or check a repository before launch, or asks why a repo gets no attention.
argument-hint: "[owner/repo | URL | path] [--json] [--offline] [--rubric FILE]"
user-invocable: false
allowed-tools: Read Glob Grep Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/audit.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/facts.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect.py *)
---

# Repository launch audit

This phase is read-only. Never edit, create, stage or commit files, and never change
repository metadata. Talk to the user in their language; quote the report as it is.

## 1. Pick the target

The arguments are: $ARGUMENTS

- No target given: use the current working directory (`.`).
- A path, a GitHub URL or `owner/repo` is passed through unchanged.
- Keep any flag the user gave (`--json`, `--offline`, `--rubric FILE`, `--type TYPE`,
  `--as-of YYYY-MM-DD`), and add none of your own. In particular pass `--offline` only
  when the user asked for it: a repository without a remote needs no flag, and when
  GitHub cannot be reached the script falls back to local files by itself and says so.
- Run the command exactly as written below, without a `cd` prefix or other wrappers:
  only that form is pre-approved, anything else stops on a permission prompt.

## 2. Run the audit

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/audit.py <target> <flags>
```

The script prints a markdown report on stdout and warnings on stderr. Show the report
to the user exactly as printed (do not recompute or round numbers). If the user asked
for `--json`, show the JSON.

If the command fails, the script prints `leadout: error: ...` and a `hint: ...`
line. Repeat both in plain words and suggest the hint. Common cases:

- `gh` missing or not authenticated: the user runs `gh auth login` themselves.
- Repository not found: check the spelling, or the repo is private and not visible.
- Rate limit: wait for the reset time shown. Suggest `--offline` on a local checkout,
  but do not rerun with it unless the user agrees.

## 3. Explain the result

After the report:

1. Say the score, grade and coverage in one sentence. When coverage is below 100 %,
   say which checks had no data and why (offline, no network, no remote, permissions); they are
   excluded from the score, not counted as failures.
2. Explain the top three fixes from the "Fixes" table in plain language: what to change
   and why it matters to a first-time visitor. Use only the evidence in the report;
   do not invent numbers or claims about the project.
3. Point to the next phase:
   - fixes marked `phase: fix` → `/leadout:fix`
   - fixes marked `phase: visuals` → `/leadout:visuals`
   - fixes marked `phase: manual` → things only the maintainer can do (answer issues,
     label good first issues, ship a commit).

## Notes

- The score is deterministic for the same repository state, rubric and date. Pass
  `--as-of YYYY-MM-DD` to reproduce an earlier audit.
- To customize the rubric, tell the user to copy `${CLAUDE_PLUGIN_ROOT}/scripts/rubric.json`
  to `./.leadout/rubric.json` and edit weights or set `"enabled": false` there; the
  audit picks it up automatically. This phase does not create that file itself.
- `--save-facts FILE` writes the collected data to a new file outside the audited
  repository, so the audit can be re-scored later with `--facts FILE` without network.
