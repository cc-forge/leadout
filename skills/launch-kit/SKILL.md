---
name: launch-kit
description: Write a launch kit for a GitHub repository in launch/ - Show HN title and text, one tailored post per fitting subreddit with its self-promotion rules summarised and linked, an X thread, a dev.to post, relevant awesome lists with PR instructions, a release calendar, a launch-day checklist and prepared answers to likely objections. Writes text only and never publishes anything.
when_to_use: The user is preparing to launch or announce a project, or asks for launch posts, a launch plan or awesome lists.
argument-hint: "[path] [--launch-date YYYY-MM-DD] [--timezone TZ]"
user-invocable: false
allowed-tools: Read Glob Grep Write(launch/**) Edit(launch/**) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py subreddits) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py subreddits *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py awesome *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py calendar *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py lint) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py lint *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/facts.py *)
---

# launch-kit

Write the texts for a launch. This skill writes files only. It never posts, submits,
comments, votes or messages anyone, on any platform; the user posts by hand from their own
single account. Talk to the user in their language; the kit stays English unless they ask
otherwise for their own project. Scripts live at `${CLAUDE_PLUGIN_ROOT}/scripts/`.
Run the scripts exactly as written here, without a `cd` prefix or other wrappers: only
that form is pre-approved. Add no flag the user did not ask for.

## Platform rules (never break them)

- Never ask for votes, upvotes or stars, and never suggest star exchanges.
- Never use more than one account or any sock puppet.
- Never reuse a post across subreddits: each gets its own angle, title and body.
- Never invent features, numbers, benchmarks, users, testimonials or press. A missing fact
  stays `TODO(...)` and is listed at the end.

## Flow

1. Create the files with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py init <target>
   [--launch-date D] [--timezone TZ]`. This command is not pre-approved: it goes through
   the permission prompt. Existing files are kept; replacing one needs the user's yes for
   that file and `--overwrite`.
2. Ask the user up to five short questions before writing prose: who it is for, why they
   built it, what is new versus the alternatives, one real limitation, and the launch date
   and timezone. Use their answers, the README and the audit as the only sources.
3. Write each file in `launch/`:
   - `show-hn.md`: three titles in the list under "Title options", each
     `Show HN: <name> – <what it does>`, at most 80 characters, no superlatives, no emoji,
     no exclamation marks; the URL to submit; the first comment.
   - `reddit.md`: for each fitting community from
     `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py subreddits --type <type> --language <lang>`,
     one `## r/<name>` section with its rules summary and link, a `**Title:**` line and a
     `**Body:**` written for that community. If the list is empty, say that no verified
     rules ship yet: ask the user which communities they know fit, tell them to read each
     live rules page, and write one section per community.
   - `x-thread.md`: four to seven `### Post N` blocks of at most 280 characters (a URL
     counts as 23), the first with what it is and the demo media, two hashtags at most.
   - `devto.md`: front matter with `published: false`, at most four tags, then the draft.
   - `awesome-lists.md`: run `launch.py awesome <target>` and paste its table.
   - `calendar.md`: run `launch.py calendar --launch-date D --timezone TZ` and paste it.
   - `checklist.md` and `objections.md`: answers from facts, or `TODO(...)`.
4. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/launch.py lint --dir <target>/launch`. It must
   exit 0 before you report the phase done; fix every error it prints. Warnings list the
   TODOs left.
5. Close by listing the remaining TODOs and saying that nothing was published: the user
   posts each item by hand from their own account.

Writing into `launch/` is pre-approved, but overwriting a file that already has content
still needs the user's confirmation in the conversation.
