---
name: repo-visuals
description: Plan and produce a repository's visuals - a demo GIF script for the first 5 seconds, recording and compression commands (under 5 MB with ffmpeg or gifski), a 1280x640 social preview SVG convertible to PNG, and a README header banner.
when_to_use: The user needs a demo GIF, screenshot, social preview or banner for a repository.
argument-hint: "[path]"
user-invocable: false
allowed-tools: Read Glob Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/visuals.py plan) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/visuals.py plan *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect.py *) Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/facts.py *)
---

# repo-visuals

Produce the visuals a repository needs to look alive in its first screen. Talk to the
user in their language; text inside images and files stays English. Scripts live at
`${CLAUDE_PLUGIN_ROOT}/scripts/`. Only `plan` is pre-approved; the commands that create
files go through the normal permission prompt, which is intended.
Run the scripts exactly as written here, without a `cd` prefix or other wrappers: only
that form is pre-approved. Add no flag the user did not ask for.

## Flow

1. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/visuals.py plan <target>`. Show the GIF
   storyboard for the project type and the recording commands for the tools present.
2. The user records. You never record the screen on your own initiative. When they have a
   recording, run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/visuals.py compress <input> --out <file.gif>`;
   it brings the GIF under 5 MB and reports size, dimensions and frame rate. If it is still
   too large, suggest trimming to the first 5-10 seconds.
3. Social preview: run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/visuals.py social-preview <target> --out <file.svg>`,
   then `visuals.py to-png <file.svg> --out <file.png>`. Tell the user to upload the PNG at
   `https://github.com/<owner>/<repo>/settings` under Social preview. There is no API for
   that upload, so you never do it.
4. Banner: `visuals.py banner <target> --out-dir <dir>` writes light and dark SVGs and
   prints the `<picture>` snippet. Inserting the snippet into the README is an edit of an
   existing file: show the diff and ask first, as `repo-fix` does.

## Rules

- Ask before creating any file, and name the paths. Never pass `--overwrite` without the
  user's explicit yes for that file.
- Title and tagline come from the repository's name and description. If either is missing
  the image shows a visible `TODO(...)`; ask the user for the text and pass `--title` or
  `--tagline`. Never invent a tagline.
- Recording rules to repeat to the user: no personal data or tokens on screen, high
  contrast, a 1280x720 or 960x540 window, no idle frame over one second, a clean loop.
- When a tool is missing, relay the install hint from the script. Do not install anything.
- End with the list of files created and the remaining TODOs.
