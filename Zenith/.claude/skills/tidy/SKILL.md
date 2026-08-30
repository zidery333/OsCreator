---
name: tidy
description: The weekly clean-up pass — stale projects, duplicates, unfiled items, things ready to put away, and anything actually broken. Use when the user says weekly review, clean house, tidy up, what's rotting, is my folder ok, check the system, or fix the folder.
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Edit
---

# Tidy

A folder like this dies two ways: things stop going in, or nothing is ever looked
at again. This fixes the second one. Once a week is plenty.

## The report

!`./os tidy --no-color 2>&1 | head -110`

!`./os check --no-color 2>&1 | head -30`

!`./os snag --no-color 2>&1 | head -20`

## Do this

Work top to bottom. For each thing, **propose the specific action, then take it**
once they agree. Don't just describe the problem.

1. **Waiting to be filed** → run `./os sort` now.
2. **Gone quiet** — one question each: is this still real?
   - still real → rewrite `## Next action` to something doable this week
   - real but not being chased → `./os hold <id>`. This is the common answer and
     the easy one to miss: most quiet work isn't dead, it just stopped having a
     next action. Held work is never nagged for going quiet again.
   - over → `./os close <id>`
3. **Keeping level** — the held things, oldest first. Not a problem list: read it
   to ask whether the standard is still being met, and only act if it isn't.
4. **Marked done but still in Work** — `./os close <id>`. This is the most
   common reason a folder starts to feel heavy.
5. **Doubles** — read both. Same thing? Merge into the older number (it has the
   inbound links), then `./os close` the newer. Say which one survived.
6. **Done by hand every time** — the report names things they keep up manually
   that read like a fixed routine. This is the highest-value item on the page and
   the easiest to skip, so don't. Offer *one*: name the steps back to them in a
   sentence, and if that's right, run `/make-skill` (or `./os new skill "<name>"`)
   and write it while the steps are in front of you. One good skill beats three
   half-written ones — if they're unsure, leave it and move on.
7. **Broken** — `./os check --fix` handles the mechanical. For the rest:
   two things sharing a number → keep the older, renumber the newer;
   a skill with no `description:` → write one naming the trigger words;
   a broken link → relink by number, not by path.
8. **Snags** — things the folder itself got wrong, written down as they
   happened. Not their job to fix: offer `./os snag --export` once, so they have
   a page to hand to whoever maintains the template. Don't read the list out.
9. **Close:** `./os check --fix`

## Rules

- Never put anything away or merge anything without an explicit yes. Deleting is
  never on the table.
- Batch your questions. One message with six decisions beats six messages.
- Fix causes, not symptoms. Things landing in the wrong place repeatedly means
  `.os/words.json` is missing the words they actually use — add them.
- Finish with the one thing they should change about how they use this.

## Done when

Nothing's waiting, nothing's broken, and everything still being pushed has a next
action from this week. Anything without one is held, not lingering.
