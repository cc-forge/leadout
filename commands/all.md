---
description: Run audit, fix, visuals and launch in order, asking for confirmation at each phase
argument-hint: "[path]"
allowed-tools: Skill(leadout:repo-audit) Skill(leadout:repo-fix) Skill(leadout:repo-visuals) Skill(leadout:launch-kit)
---

Run the leadout pipeline on this target: $ARGUMENTS
If no target is given, the target is the current working directory.
Talk to the user in their language; everything written to the repository stays in English.

Run the phases one at a time with the Skill tool, passing the target unchanged. Stop
after each phase and wait for the user's answer before starting the next one.

1. Invoke skill `leadout:repo-audit`. Show the score.
2. Ask: "Continue to the fix phase?" On yes, invoke `leadout:repo-fix`; it keeps its
   own confirmation before every write, commit, push or metadata change. When it ends,
   invoke `leadout:repo-audit` again on the fix branch and show the score before and
   after.
3. Ask: "Continue to the visuals phase?" On yes, invoke `leadout:repo-visuals`.
4. Ask: "Continue to the launch kit?" On yes, invoke `leadout:launch-kit`.
5. End with: every remaining `TODO(...)` from all phases, the files each phase produced,
   and a note that `/leadout:stats` is for after the launch, with `--launch-date
   YYYY-MM-DD` once the date is known.

A "no" at any step ends the pipeline. Everything already produced stays where it is, and
the summary says so. Nothing is pushed unless the user approved the push inside the fix
phase. Never publish anything on any platform.
