---
name: wrapup
description: Close out a working session — write down what happened, update the projects touched, file anything loose. Use when the user says done for now, wrap up, end session, save my progress, that's enough for today, or before they walk away from real work.
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Edit, Write
---

# Wrap up

The session isn't over until the folder knows what happened.

## Do this, in order

1. **Write down the day, into the work it happened to.** For each piece of work
   touched, in its `README.md`:
   - append one dated line to `## Log` — what was worked on, what's blocked and on what
   - append to `## Decisions` if something was actually decided, and what it rules out
   - **overwrite** `## Next action` (pushing) or `## Where it stands` (holding)
   - if it ran out of next actions but isn't over, `./os hold <id>` — that is the
     honest ending for most sessions, and far more common than closing something

   Facts only — no summary of the conversation, no adjectives. It goes here and not
   in a diary because this is where it is still findable a year from now, by the
   number of the thing itself.

2. **Something decided that belongs to no one item?** `./os save "<the decision, and
   what it rules out>"` — it gets filed as a note with its own number.

3. **Anything raised but not resolved** → `./os save "<the loose end>"`

4. **Settle it:** `./os sort`

5. **Report in four lines or fewer:** what moved, what was decided, what's next,
   what's still open.

## Rules

- Never rewrite an old log entry or decision. Append a correction with today's date.
- Never mark something done that they didn't say was done.
- A decision with no stated consequence isn't a decision. Write what it rules out.
- Nothing meaningful happened? Write one line saying so. Don't manufacture progress.

## Done when

Everything touched has a dated line in its `## Log`, and everything they pushed
on has a current next action — held work doesn't need one, that's what holding
means.
