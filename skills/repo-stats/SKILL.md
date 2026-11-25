---
name: repo-stats
description: Read a GitHub repository's stars over time, forks, watchers, clones and views for the last 14 days, top referrers and pages, and release downloads via gh api; save each reading to a local history (.leadout/stats.jsonl) and report daily growth, referrers and a before/after comparison around a launch date. Read-only on the repository.
when_to_use: The user asks how a launch went, wants repository traffic, stars, referrers or download numbers, or wants to keep traffic history beyond GitHub's 14 days.
argument-hint: "[owner/repo | path] [--launch-date YYYY-MM-DD] [--install-workflow]"
user-invocable: false
allowed-tools: Read Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py collect *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py report *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py install-workflow)
---

# Repository stats

Read-only on the repository: never edit tracked files, commit, push or change metadata in
this phase. The only file written without asking is the stats history under
`./.leadout/` (the folder carries its own `.gitignore`, so git ignores it). Talk to the
user in their language; show numbers exactly as the script prints them and never invent,
estimate or round them.
Run the scripts exactly as written here, without a `cd` prefix or other wrappers: only
that form is pre-approved. Add no flag the user did not ask for.

The arguments are: $ARGUMENTS

## 1. Collect and report

- Target: the `owner/repo` or path the user gave, else `.` (it must have a GitHub
  `origin` remote; otherwise ask the user for `owner/repo`).
- Keep `--launch-date YYYY-MM-DD` if given.

Run:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py collect <target>
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py report <target> <--launch-date D if given>
```

Show the report as printed. Then, in plain words:

- Totals and the daily growth of the last days.
- Referrers: GitHub gives 14-day totals and does not link stars to referrers. Present the
  stars column as context only, never as "stars from Hacker News" or similar.
- With a launch date: the before/after table. Where it says "insufficient data", say
  that fewer than half of the days on that side have data; do not fill the gap.

## 2. When data is missing

- Warning `Traffic data needs push access`: views, clones, referrers and pages are
  visible only to accounts with push access. Say so and show the public data.
- Warning `GitHub does not list stargazers`: stars per day come from the difference
  between snapshots; more frequent snapshots give a finer timeline.
- One snapshot only: changes appear from the second reading on; suggest running
  `/leadout:stats` again tomorrow, or the daily workflow below.
- Errors print `leadout: error: ...` and `hint: ...`; repeat both plainly.

## 3. Offer the daily workflow (only if the user wants history kept automatically)

GitHub keeps traffic for 14 days only. The daily workflow stores one snapshot a day on
an orphan branch `leadout-stats`, leaving the default branch untouched.

1. Preview, from inside the user's checkout (run with no arguments, so it is the only
   pre-approved form):

   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py install-workflow
   ```

   It prints the three files it would create (`.github/workflows/leadout-stats.yml`,
   `.github/leadout/stats.py`, `.github/leadout/common.py`) and the workflow.
2. Writing follows the repo-fix rules and asks with `AskUserQuestion`, one approval at a
   time: create a new branch (for example `leadout/stats-workflow`), then create the
   three new files listed above, then the commit (show the message), then the push
   (show remote and branch). A "no" skips that step.
   After the branch approval, run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py install-workflow . --write`; it refuses
   the default branch and existing files, and it never stages or commits.
3. Traffic in the workflow needs a token: explain that the user can create a
   fine-grained personal access token limited to this repository with
   "Administration: Read-only" and save it as the repository secret
   `LEADOUT_TOKEN`. Never ask for the token, never read, print or store it; the
   user does this in the GitHub settings. Without it the workflow saves public data only.
4. Mention that GitHub disables scheduled workflows after 60 days without repository
   activity.

## 4. Merging the workflow history locally

When the user wants the Actions history in the report, propose these read-only commands
(they go through the normal permission prompt):

```
git fetch origin leadout-stats
git show origin/leadout-stats:stats.jsonl > .leadout/stats-actions.jsonl
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stats.py report <target> --history .leadout/stats.jsonl --history .leadout/stats-actions.jsonl
```

Snapshots are merged and deduplicated by collection time; for each day the latest
reading wins.
