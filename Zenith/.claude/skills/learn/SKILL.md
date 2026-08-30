---
name: learn
description: Learn how good practitioners actually do something, and bring back a note you can work from — including from a video or a whole channel. Use when the user says teach me, how do the pros do this, I want to get good at, learn this properly, watch this video and, or I keep doing this badly.
argument-hint: [the craft, or a link to learn it from]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Bash(grep:*), Task, Agent, Read, Write, Edit, Grep, Glob, WebSearch, WebFetch
---

# Learn

They don't want a summary of a topic. They want to **do the thing**, the way
somebody good at it does it. Everything below serves that.

## Do this

1. **Look inside first:** `./os find "$ARGUMENTS" --limit 10`. Already covered?
   Extend that note and cite its number — never write a rival one.

2. **One message, then go.** Only what changes the answer: what they'll do with
   it, and whether they've never tried, are doing it badly, or are improving.
   If `$ARGUMENTS` already says, skip it entirely.

3. **Practitioners, not explainers.** Best first: someone who does this for a
   living showing real work; then the reference the field itself uses; then
   long-form from someone with a real body of work; articles last, to fill gaps.

   **Five to eight sources, and stop once the third tells you the same thing** —
   that repetition *is* the method. Count sources by *person*, not by video: two
   uploads from one creator agree with each other by construction, and pull the
   count without adding anything. Check who is talking before you count it.

4. **For anything on YouTube, read the words, not the page.**
   ```bash
   ./os learn --list --json "<channel or playlist url>"   # what they have
   ./os learn --json "<video url>" "<another>"            # the actual words
   ```
   No channel, just a subject? `--list --json "ytsearch12:<what they asked>"`.
   **Never pick by views**; they predict nothing either way — read the titles
   and judge. Fetching caches, so a top-up is free. If yt-dlp is missing it says
   the one line to install it; pass that on and use `WebFetch` meanwhile.

   Past three or four sources, hand it to `@student` — six transcripts is 40,000
   words and belongs in its own context, not in their chat.

   Transcripts carry a `[MM:SS]` stamp every half minute. **Cite them** — that
   is what turns a claim into something they can go and check.

   **Triage before you read.** Every line starts with its own timestamp, so grep
   whole lines — a character window silently misses the middle of a 600-character
   one. Rank by substance, then find the pitch:
   ```bash
   grep -icE "(term|term|term)" *.txt          # density, ranks them
   grep -inE "(link in (the )?desc|book a call|sponsored|\.com/)" f.txt
   ```
   Read the dense ones properly; skim the rest. On a real run this turned 21,000
   words into a read order for nothing.

5. **Judge the teacher before the lesson.** Who made it, what they have actually
   shipped, and when. A confident stranger is not a professional; say so when
   you couldn't tell.

   The tells are in the transcript, timestamped: *our community*, *payouts*,
   *link in the description*, *book a call*, *free training*, a Discord.

   **Where the pitch lands matters more than whether there is one.** Almost
   everyone worth reading sells something: a pitch after nine minutes of method
   is a sponsor, a pitch at `[03:04]` before anything is taught *is* the video.
   Inverse tell: real numbers — sizing, thresholds, what they'd actually spend —
   means real work. Say who sells what and let them price it in.

   **Then stop, once, before writing.** Three lines: how much was advert and
   what survived, do the sources agree, what it'll be called. Then ask.

6. **Keep only what transfers** — the steps, the judgement behind each, the
   beginner mistakes, the kit really used. Drop theory that changes nothing they
   would do on Monday.

7. **Write it — one note, or a folder if the sources really disagree.** Ads,
   Excel, SEO: everyone does the same thing with better tips, so blend them —
   the blend *is* the truth. Trading, diet, lifting: rival schools, so
   `./os new ongoing "<Subject>"`, one file per school, never blended. Mixing
   one school's entries with another's risk rules is how people get hurt.
   For one note, make it a note outright — numbered steps read as *work* to the
   sorter. **`.os/templates/learning.md` is the shape, and `NOTE.md` here is how
   to fill it.** Follow those headings: don't invent your own, and don't keep the
   plain note's. Every subject hides one real disagreement — find it.

8. **Teach the folder the words.** A subject arrives with its own vocabulary,
   and that is how `./os` guesses where the next thing goes:
   ```bash
   ./os words <domain> "ad set" "learning phase" "roas"
   ```
   Eight to fifteen terms *this subject uses and the folder doesn't* — what
   they'd type in a passing thought six months from now, minus anything another
   domain already owns. It writes a `learned` list, never their own. Say nothing.

9. **Then make it usable** — the step that earns this skill. Offer **one**:
   - a job they'll repeat → `/make-skill`, written while the note is still open
   - a craft they'll get better at → `./os new ongoing "<the craft>"`, with the
     practice step as the standard it holds to
   - neither → the note is enough. Say so and stop.

## Rules

- Never teach from a source you didn't open — no summarising a video by its title
  or a book by its blurb. If the words won't come, say that rather than guessing.
- Where practitioners genuinely disagree, say who argues what. Don't flatten a
  live debate into one confident answer.
- One short quote per source, in quotes, attributed. Never reproduce a
  transcript, lesson or chapter wholesale — cite the timestamp and link instead.
- Date anything where the craft moves. Six-year-old advice about a platform is a
  claim, not a fact.
- Anything physical, or carrying real risk — electrical, medical, legal,
  financial, structural — say where the note stops and a qualified person starts.
  Never write a trade plan or a dose.
- Quiet about risk **unless a source is reckless** — a claim the base rate
  contradicts is reckless. "Consistent daily profits" earns one line about most
  retail traders losing money. Once, then teach.
- Same subject again? Read only what's new and append.
- One craft per note. "Learn photography" is a bookshelf; get them to the thing
  they are actually stuck on.

## Done when

They have a number they can open, a first step small enough to do today, and
every claim traced to a named source — with a timestamp — they could go and
check themselves. And the folder now knows the subject's words.
