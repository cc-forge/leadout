![leadout: from finished code to launched and measured.](docs/cover.png)

A Claude Code plugin that takes a GitHub repository from finished code to launched and measured: audit, fixes, visuals, launch kit and post-launch stats.

![A real /leadout:audit run on sharkdp/hyperfine, 2026-11-25: 79/100 (B)](docs/demo.png)

## Install

```bash
claude plugin marketplace add cc-forge/leadout && claude plugin install leadout@leadout
```

Then, inside a Claude Code session: `/leadout:audit owner/name`, a GitHub URL or a local
path. Inside a session the install is `/plugin marketplace add cc-forge/leadout` and
`/plugin install leadout@leadout`.

## Commands

| Command | What it does |
|---|---|
| `/leadout:audit` | Scores launch readiness 0-100 over 24 checks and ranks the fixes. Read-only. |
| `/leadout:fix` | Applies the fixes on a new branch, asking before every write. |
| `/leadout:visuals` | Plans the demo GIF and makes the social preview and banner. |
| `/leadout:launch` | Writes a launch kit in `launch/`: Show HN, Reddit, X, dev.to, calendar. |
| `/leadout:stats` | Keeps a local history of stars, traffic, referrers and downloads. |
| `/leadout:all` | Runs audit, fix, visuals and launch in order, asking before each phase. |

Flags, outputs and what each command writes: [docs/commands.md](docs/commands.md).

## Real audits

Ten well-known public repositories of different types, audited on 2026-11-25 with the
rubric "leadout default" 1.0.0, all at 100% coverage:

| Repository | Detected type | Score | Weakest category |
|---|---|:---:|---|
| [cli/cli](https://github.com/cli/cli) | CLI | 89 · B | README 27.0 / 35 |
| [pallets/flask](https://github.com/pallets/flask) | library | 85 · B | README 23.0 / 35 |
| [rustdesk/rustdesk](https://github.com/rustdesk/rustdesk) | desktop app | 84 · B | README 22.5 / 35 |
| [localsend/localsend](https://github.com/localsend/localsend) | desktop app | 83 · B | Community 7.5 / 15 |
| [axios/axios](https://github.com/axios/axios) | library | 81 · B | README 25.5 / 35 |
| [sharkdp/hyperfine](https://github.com/sharkdp/hyperfine) | CLI | 79 · B | Community 1.5 / 15 |
| [BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep) | CLI | 78 · B | Community 6.0 / 15 |
| [tldr-pages/tldr](https://github.com/tldr-pages/tldr) | other | 77 · B | Distribution 10.0 / 15 |
| [github/docs](https://github.com/github/docs) | website | 75 · B | README 17.5 / 35 |
| [psf/requests](https://github.com/psf/requests) | library | 75 · B | README 21.0 / 35 |

The full reports are in [docs/audits/2026-11-25](docs/audits/2026-11-25). Rerun any of
them with `python3 scripts/audit.py owner/name --json`; live repositories change, so
scores move with them.

## Project rules

It never asks for stars or votes, never posts the same text twice and never publishes
anything: it writes drafts and asks before every write, commit or push. No telemetry;
GitHub calls go through your own `gh`. Full rules and privacy: [docs/rules.md](docs/rules.md).

## Requirements

Python 3.9 or newer (standard library only) and the [GitHub CLI](https://cli.github.com/)
signed in with `gh auth login`. Optional: `ffmpeg` or `gifski` for GIFs, and Chrome,
`rsvg-convert`, Inkscape or ImageMagick to turn SVG into PNG.

## Development

```bash
git clone https://github.com/cc-forge/leadout && cd leadout && python3 -m unittest discover -s tests
```

Tests run offline on recorded GitHub fixtures; the specification is [docs/SPEC.md](docs/SPEC.md).

## Roadmap

Subreddit rules read from each community's live rules page, traffic fixtures from a
repository with push access, and CI templates beyond Python, Node, Go and Rust. A case
study follows once a real launch has before and after numbers.

## Contributing

Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md), and
[SECURITY.md](SECURITY.md) to report a vulnerability privately.

## License

[MIT](LICENSE)
