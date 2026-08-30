---
name: find
description: Find and pull together anything in this Zenith folder — notes, projects, decisions, files, past sessions. Use when the user asks what did I write about X, where is that thing, did I already, what did we decide about, look that up, or search my notes.
argument-hint: [what to look for]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Grep, Glob
---

# Find

## Do this

1. Search first — it's ranked and covers everything, archive included:
   ```bash
   ./os find "$ARGUMENTS" --limit 15
   ```
2. Thin results? Try their synonyms, then `Grep` across `Notes/` and `Work/`
   for the distinctive words. Don't bother trying spelling
   variants — `./os find` already handles typos and plurals, and tells you when
   it searched for something other than what you typed.
3. Read the top three to five properly. Don't answer from snippets.
4. Answer the question they actually asked. Then cite **number · name · path**
   for everything you used.
5. If the answer changed over time — a decision revised, a plan replaced — say
   which one is current and when it changed. The `## Decisions` and `## Log`
   sections inside the item are the authority on that; they are dated and
   append-only, so the last entry wins.

## Rules

- Never say "I couldn't find anything" until you've tried two phrasings and one `Grep`.
- Never invent a link between two notes. If they just share a word, say that.
- If it genuinely isn't there, say so and offer to write the question down.
- Three or more notes covering the same ground is worth mentioning — they
  probably want them merged.

## Done when

They have the answer and can check every part of it by opening one number you cited.
