---
name: catchup
description: Catch up on everything in this Zenith folder — what's moving, what's gone quiet, what's waiting, what's broken. Use at the start of a session, or when the user asks where things stand, what should I work on, what's going on, catch me up, or what did I leave.
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read
---

# Catch up

## Where things stand

!`./os status --no-color 2>&1`

!`./os tidy --json 2>/dev/null | head -60`

## Do this

Turn that into something a person actually wants at 9am. Plain sentences, no
tables, no preamble. Four short sections, and skip any that's empty:

**Moving** — projects touched in the last week. One line each: number, name, the
next action from its README.

**Gone quiet** — open projects nobody's touched in a while. One line each with how
long. Say plainly if one should just be closed out.

**Waiting** — anything unfiled, and whether it's worth clearing now.

**Broken** — only if something actually is.

Close with one sentence: what you'd do first, and why. Commit to an answer.

## Rules

- Never list everything. More than five in a section? Name three, count the rest.
- Never pad. Nothing quiet and nothing broken means a three-line answer, and
  that's the right length.
- Use numbers so they can act: "open W.04".
- No jargon and no health score. Say "everything's in good shape."

## Done when

They know what to do next without asking a follow-up question.
