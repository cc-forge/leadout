# Project rules and privacy

## Rules

These are choices of the project, not settings:

- It never asks anyone for votes, upvotes or stars, and never suggests star exchanges.
- It never suggests multiple accounts, and never writes the same post for several
  communities: each subreddit gets its own text, and the lint flags near-duplicates.
- It writes texts only. It never publishes, posts or submits anything on any platform.
- It always asks before writing to an existing file, committing, pushing, changing
  repository metadata or creating a release. The plugin's permissions pre-approve only
  the read-only scripts and the drafts inside `launch/`; every other write, commit or
  push goes through Claude Code's own permission prompt as well.
- It never invents numbers, features, benchmarks, users or press: a missing fact becomes
  a visible `TODO(...)`, and every phase lists the ones left.

## Privacy

- No telemetry. The plugin sends nothing anywhere on its own.
- Every GitHub call goes through your own `gh`; the scripts never read, print or store
  your token.
- The only other network call checks that a badge URL answers before it goes into your
  README; it is off with `--offline`.
- The stats history stores counts only, never the usernames of stargazers.
