# How to work in this folder

The rules live in [`AGENTS.md`](../AGENTS.md) at the top of this folder. Read it
before doing anything, and follow it — it is the one set of rules every AI here
works from, so there is no second copy to drift out of step.

In short: this folder keeps itself organised through a program called `./os`
(`os` on Windows). Run `./os` to see where things stand, `./os save "<text>"` to
write something down, `./os find <words>` to get it back. Never move or rename
things by hand — `./os undo` cannot reverse what it did not do.
