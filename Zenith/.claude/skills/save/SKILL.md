---
name: save
description: Write something down into this Zenith folder and file it — a thought, a link, a quote, a file, a half-formed idea. Use whenever the user says remember this, save this, note that, write this down, jot this down, don't let me forget, or drops something mid-conversation that shouldn't be lost.
argument-hint: [what to save]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*)
---

# Save

Saving costs nothing. Deciding costs everything. Never ask where it should go.

```bash
./os save "<their words, plus the context they didn't say>"
./os save <path>          # something already on disk
```

`./os save` works out what the thing is, which phase it starts in, and where it
goes — a sentence with a next action comes back **pushing**, one describing a
standard they keep comes back **holding**. It is numbered by the time the command
returns, so never follow it with `./os new work` for the same thing.

## What a good save looks like

Their words, kept exactly, plus the context future-them will need and didn't say:
what it's about, why it came up, the source if there is one. Five things in one
message is five saves, not one.

## Rules

- Never ask a clarifying question first. A rough save beats a lost thought.
- Keep their exact words for anything quoted. Only paraphrase the context you add.
- Don't announce a plan to create a project — the save already did it.
- Reply in one line: what you wrote down and where it went. Say "I wrote that
  down — it's in Notes as N.03", not "captured" and not the folder mechanics.

## Done when

They've heard one sentence confirming it's safe.
