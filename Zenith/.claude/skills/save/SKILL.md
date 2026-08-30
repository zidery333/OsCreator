---
name: save
description: Write something down into this Zenith folder and file it — a thought, a link, a quote, a file, a half-formed idea. Use whenever the user says remember this, save this, note that, write this down, jot this down, don't let me forget, or drops something mid-conversation that shouldn't be lost.
argument-hint: [what to save]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*)
---

# Save

Saving costs nothing. Deciding costs everything. Never ask where it should go.

## Do this

1. Take `$ARGUMENTS`, or the thing in the conversation they just pointed at.
2. Write it down **in their words**, then add the context they didn't say but
   future-them will need: what it's about, why it came up, the source if there is one.
3. Run it:
   ```bash
   ./os save "<their words, plus the context>"
   ```
   For a file already on disk: `./os save <path>`.
4. Reply in one line: what you wrote down and where it went. Nothing else.

`./os save` decides for itself what the thing is, and which phase it starts in —
a sentence with a next action comes back **pushing**, one that describes a standard
they keep comes back **holding**. Either way it is already numbered, so do not then
run `./os new work` for the same thing. One thing gets one number.

## Rules

- Never ask a clarifying question first. A rough save beats a lost thought.
- Five things in one message means five saves, not one.
- Keep their exact words for anything quoted. Only paraphrase the context you add.
- If it's obviously a whole project, don't announce a plan to create one — the
  save already did. Just say where it landed.
- Don't say "captured" or name the folder mechanics. Say "I wrote that down —
  it's in Notes as N.03."

## Done when

They've heard one sentence confirming it's safe.
