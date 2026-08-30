# OsCreator

[![test](https://github.com/zidery333/OsCreator/actions/workflows/test.yml/badge.svg)](https://github.com/zidery333/OsCreator/actions/workflows/test.yml)

Workspace for building **[Zenith](Zenith/)** — one folder for your work, usable by
any AI that can read `AGENTS.md` and run `./os`.

Everything that ships lives in [`Zenith/`](Zenith/); start with its
[README](Zenith/README.md).

```bash
cd Zenith
./os demo     # two-minute tour, cleans up after itself
./os test     # the suite, against a throwaway copy — never your folder
```

## What's inside Zenith

| | |
| --- | --- |
| `os` / `os.cmd` | the entry point, macOS/Linux and Windows |
| `.os/engine.py` | filing, search, undo, index — the whole system |
| `.os/learn.py` | the one part that goes online: transcripts, and vocabulary |
| `.os/words.json` | the words it files by. Add yours, or use `./os words` |
| `.os/templates/` | what each kind of thing starts out looking like |
| `.os/tests/run.py` | the suite, run against a throwaway copy |
| `AGENTS.md` | the rules any AI reads on opening the folder |
| `.claude/` | skills and helpers for Claude Code — optional, `./os` alone works |

Python 3.9+, standard library only. `yt-dlp` only if you want `./os learn` to
hear a video. Tested on Linux, macOS and Windows.

## Your notes are not in here

`Zenith/.gitignore` keeps `Work/`, `Notes/`, `Archive/`, `INDEX.md` and
`.os/state.json` out of the repository. Clone this and you get the system with
nothing in it — which is what a fresh one should be. Delete that block from
`Zenith/.gitignore` if you'd rather version your own contents too.

Two files are shipped and also yours to change: `.os/config.json` records your
name once you run `./os name`, and `.os/words.json` grows a `learned` list as
you use `/learn`. Check both before pushing if this folder is also the one you
actually work in.

## Licence

[MIT-0](LICENSE) — do anything, no attribution required.
[`Zenith/LICENSE.md`](Zenith/LICENSE.md) says the same thing in plain English.
