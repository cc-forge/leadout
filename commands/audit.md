---
description: Audit a GitHub repo for launch readiness: read-only, deterministic 0-100 score
argument-hint: "[owner/repo | URL | path] [--json] [--offline] [--rubric FILE]"
allowed-tools: Skill(leadout:repo-audit)
---

Invoke the Skill tool with skill `leadout:repo-audit` and pass these arguments
unchanged: $ARGUMENTS

If no target is given, the target is the current working directory.
Talk to the user in their language; everything written to the repository stays in English.
