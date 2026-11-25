# Commands

Every command takes an optional target: `owner/name`, a GitHub URL or a local path.
Without one it works on the current directory. Type the commands inside a Claude Code
session; you can also ask in plain words ("audit this repo") and Claude picks the skill.

## /leadout:audit

```text
/leadout:audit [owner/repo | URL | path] [--json] [--offline] [--rubric FILE]
```

A deterministic 0-100 launch-readiness score over 24 checks in five categories: README,
metadata, distribution, community and health. Fixes are ranked by impact per effort and
tagged with the phase that applies them.

- Writes nothing.
- Checks without data (no remote, no push access, `--offline`) leave the score and are
  reported as coverage, so missing data never looks like a failing repository.
- If GitHub cannot be reached, a local checkout is audited from its files and the report
  says so.
- The rubric is [`scripts/rubric.json`](../scripts/rubric.json); a repository can
  override it in `.leadout/rubric.json`.

## /leadout:fix

```text
/leadout:fix [path] [--only ACTION[,ACTION]]
```

Turns the audit into a plan and applies it on a new branch: a README rewritten for the
project type (desktop app, CLI, library, website, Claude Code plugin), badges checked
before use, at least as many pertinent topics as the audit asks for, CONTRIBUTING, code
of conduct, security policy, issue and PR templates, CI for the detected language and
release notes from Conventional Commits.

- Writes new files on a new branch, after you approve each step.
- Never commits, pushes or changes repository metadata without asking.

## /leadout:visuals

```text
/leadout:visuals [path]
```

A storyboard for the first 5 seconds of a demo GIF, recording and compression commands
(under 5 MB with `ffmpeg` or `gifski`), a 1280x640 social preview and a light/dark README
banner.

- Writes new image files.
- GitHub has no API for the social preview: upload it under Settings → Social preview.

## /leadout:launch

```text
/leadout:launch [path] [--launch-date YYYY-MM-DD] [--timezone TZ]
```

A `launch/` folder with a Show HN post, one Reddit post per fitting community, an X
thread, a dev.to post, awesome lists to submit to, a dated calendar, a launch-day
checklist and answers to likely objections, plus a lint for each platform's rules.

- Writes `launch/` only. Never publishes anything.

## /leadout:stats

```text
/leadout:stats [owner/repo | path] [--launch-date YYYY-MM-DD] [--install-workflow]
```

Stars over time, forks, watchers, 14-day views and clones, top referrers and pages,
and release downloads, appended to a local history with a before/after comparison
around the launch date. Traffic needs push access to the repository.

- Writes `.leadout/` only. Stores counts, never stargazer usernames.
- `--install-workflow` prints a daily GitHub Actions workflow that keeps the history
  beyond GitHub's 14 days.

## /leadout:all

```text
/leadout:all [path]
```

Audit, fix, visuals and launch kit in order, asking before each phase.
