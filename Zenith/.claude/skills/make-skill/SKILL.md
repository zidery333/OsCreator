---
name: make-skill
description: Build a new reusable skill or helper for this folder — interview, write, check, register. Use when the user says make a skill, automate this, I keep asking you to do X, turn this into a command, make this a button, or build me a helper.
argument-hint: [skill|helper] [what it should do]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Write, Edit
---

# Make a skill

A **skill** is how a recurring job gets done — it runs in this conversation.
A **helper** is who does a big job that deserves its own head-space — it runs
apart and reports back. Pick before writing anything.

| Skill | Helper |
| --- | --- |
| A procedure or checklist | A job with its own context |
| Runs right here | Would flood this conversation |
| Needs what's already on screen | Needs its own tools and isolation |

If it must *always* hold no matter what the AI decides, it's neither — it's a hook
in `.claude/hooks/`. Say so.

## Do this

1. **Ask three questions, all at once.** Skip entirely if `$ARGUMENTS` answers them:
   - What exact words will you say when you want this?
   - What does finished look like?
   - What should it never do?

2. **Scaffold it:**
   ```bash
   ./os new skill "<Name>"      # or: ./os new agent "<Name>"
   ```

3. **Write the body.** Replace the template, don't append to it.
   - `description:` decides whether it ever runs. Lead with the use case, then
     the trigger words exactly as a person would say them.
   - Under 120 lines. Imperative. Say what to do, not why.
   - Long reference material → a second file in the folder, mentioned in `SKILL.md`
     so it only loads when needed.
   - For a helper: name its tools, and say exactly what its final message contains.
     Nothing else comes back.

4. **Check it:** `./os check`

5. **Run it once in front of them** on a real case. An untested skill is a guess.

## Rules

- Never name it after a built-in: `doctor`, `review`, `run`, `init`, `debug`,
  `loop`, `help`, `status`, `config`, `model`, `code-review`. `./os new skill`
  refuses these outright.
- Lowercase-with-hyphens. The folder name *is* the command.
- One skill, one job. If the description needs "and", it's two skills.
- Never write a skill for something done once. Just save a note.

## Done when

`./os check` is clean, it shows up in `.claude/CATALOG.md`, and you've run it once.
