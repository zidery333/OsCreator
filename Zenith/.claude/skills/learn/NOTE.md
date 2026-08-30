# The note to write

Make it a note outright rather than saving prose and hoping — a method full of
numbered steps reads as *work* to the sorter:

```bash
./os new note "How <the craft> is actually done" --domain <the subject's own domain>
```

**The domain is the subject's, not `learning`.** Meta ads is `marketing`, squat
form is `personal`, Postgres indexes is `engineering`. Put every learned note in
`learning` and the domain stops telling `./os find` anything.
`./os words` with no arguments lists the ones this folder knows.

## The shape

`.os/templates/learning.md` **is** the shape — read it and follow it. Open the
file `./os new` just made, leave the `---` block at the top alone, and replace
everything below it with that template's headings, filled in. The guidance for
each one is in the comment under it.

Don't improvise headings, don't keep the plain note's `## What it says`, and
don't nest the method underneath something else — the numbered steps are the
point of the note and they belong at the top level where they can be scanned.

Cite a `[MM:SS]` for every claim. Cite where the claim *starts*: sentences run
across a stamp, so the line you found it on is often its second half.

## Topping up later

Same subject again? Append a dated section under `## The method` and extend
`## Sources`. Never rewrite what's there.
