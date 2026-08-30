# Zenith

**One folder for your work. Any AI can use it.**

You tell it things. It works out what they are and puts them somewhere sensible.
You never pick a folder, never name a file, never move anything.

## Getting started

Put this folder wherever you keep your stuff. Then open a terminal
(**Terminal** on a Mac, **PowerShell** on Windows), and point it at the folder:

```bash
cd path/to/Zenith
./os demo
```

That's a two-minute tour. It cleans up after itself.

> **If it says `permission denied`**, run `bash os demo` once instead — Zenith
> fixes itself, and `./os` works from then on. (Never happens on Windows.)

**What's `os`?** The little program in this folder that does the actual work — it
reads what you write down, decides where it goes, names and numbers it, searches
it back, and undoes anything it did. When an AI files something for you, it's
running `os` too.

**And `os.cmd`?** The same thing, for Windows. Your computer uses whichever one it
understands — you never pick between them. Leave both alone.

> On Windows, drop the `./` — type `os demo`, not `./os demo`. That goes for
> every command on this page.

## Using it

```bash
./os save "the billing thing breaks every Friday"
./os find billing
./os W.01
./os
```

`save` writes it down **and** files it, in one step. `find` gets it back, forgiving
typos and plurals, so `biling` still finds it. Typing a number shows you that one
thing; `./os` alone shows where everything stands. The rest is `./os help`.

Or skip the commands entirely — open this folder in Claude Code, Cursor, Codex,
Gemini CLI, or anything else that reads `AGENTS.md`, and just talk:

> *"remind me the auth token expires on the 14th"*
> *"what did I decide about pricing?"*
> *"start something for the redesign"*

## What's in here

| | |
| --- | --- |
| `Work/` | Everything you're carrying. |
| `Notes/` | Anything you'll look up later — PDFs and pictures too. |
| `Archive/` | No longer live. Still searchable. |

Three folders, and you never choose between them. There's no inbox: `./os save`
writes a thing down and files it in the same breath, so nothing is ever left
pending somewhere you have to remember. Dragged a pile in from Finder instead?
Drop them in either folder and run `./os sort`.

Everything gets a tag like `W.04`. **The letter says what it is** — `W` for work,
`N` for a note — then a count: `W.04` is the fourth thing you started.

The tag never changes: rename it, move it, archive it, and `./os open W.04` still
works. Closing it puts it in `Archive/` and it stays `W.04` — the letter says what
it *is*, not where it sits. It's also how you and your AI talk about things
without repeating the whole title: *"close out W.04"*.

Your notes are plain text. Open one and you'll see a `---` block at the top holding
its tag, name and date. Edit the words below it freely; leave that block alone.

## Pushing, and holding

Everything you're carrying lives in `Work/`. The folder never asks whether it
will finish — that's a guess about the future, made when you know least. It asks
what the thing needs from you **now**:

| | |
| --- | --- |
| **pushing** | There's a next action. It's on the go. |
| **holding** | There's a standard you keep level. No next action, no deadline. |

That's one word in the file, not a folder, because the same job flips between the
two constantly — you ship the redesign, and now you maintain the site. `./os hold
W.04` and `./os push W.04` flip it. Held things are never nagged for going quiet;
sitting still is what they're for.

## Two things worth knowing

**Nothing is ever deleted.** `./os close W.04` puts something in the archive, where
search still finds it — closed means *no longer live*, not *finished*.

**Everything can be undone.** `./os undo` reverses the last move — where files went
*and* what they said, twenty steps back. Not your own hand edits; use your editor.

## Jobs you keep doing by hand

A **skill** is not work you're doing at all — it's a job you want *done the same
way every time*, written down once so any AI opening this folder can just do it.
Explained the same steps twice? That's a skill: `./os new skill "Draft the
weekly invoice"`. `./os tidy` spots them for you.

Seven come with the folder, in `.claude/skills/`. In Claude Code type `/save`,
`/find`, `/learn`, `/catchup`, `/wrapup`, `/tidy` or `/make-skill` **in the chat**
— not the terminal. Everything works without them through `./os` too.

## Making it yours

- `./os name "Your Name"` — put your name on it.
- **`.os/words.json`** — the one file worth opening. Add the words you actually
  use — your projects, your clients, the names of things — and it gets better at
  guessing where they go. `./os words` does the same without opening an editor.
- `AGENTS.md` — the rules your AI reads every session. Keep it short.
- `.os/templates/` — what new work (`pushing.md`, `holding.md`) or a note starts
  out looking like.

## How much room it takes

The folder itself is about **450 KB** — smaller than one photo. After that it only
holds what you put in it, as plain text. Two things it keeps for you, both capped
so they can't run away: **undo history** and **backups** (`./os backup`).

## Moving it, and what it needs

Move it, rename it, put it in Dropbox or git — it finds its own root, so nothing
breaks. It needs Python 3.9 or newer and nothing else: macOS and most Linux have it,
Windows gets it from python.org. No packages, no account, no network — bar
`./os learn`, which goes to the web and wants `yt-dlp` to hear a video.

---

`./os help` · everything you can type   ·   `./os check` · is anything broken?
`./os test` · run against a throwaway copy — never your folder
