---
name: learn
description: Learn how good practitioners actually do something, and bring back a note you can work from — from the official source and from people who do it for a living, including a video or a whole channel. Use when the user says teach me, how do the pros do this, I want to get good at, learn this properly, watch this video and, or I keep doing this badly.
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

3. **Two kinds of source, and a real note needs both.**
   - **The official one** — whoever defines the thing: its own documentation,
     the standard, the originator's site or book. Definitions, numbers, current
     behaviour and anything that moves come from here and nowhere else.
   - **Practitioners** — people who do this for a living, showing real work.
     How it is *actually* done, what they judge, what goes wrong. Usually
     YouTube, and usually better than any article on the same subject.

   Where they conflict the official source wins on **what is true** and the
   practitioner wins on **what is done**. Write the conflict down; don't smooth
   it over. **`SOURCES.md` here is how to reach and read either kind.**

4. **Get the official one, and prove it is official.** Follow the link from the
   thing's own channel, repo, profile or book — never the first search result,
   because lookalike domains are the business model of some subjects. Cite the
   URL and the date you read it. No official source — a craft rather than a
   product — is a fine answer: say so in the note rather than promoting a blog.

5. **Then the practitioners. Five to eight, and stop once the third tells you
   the same thing** — that repetition *is* the method. Count by *person*, not by
   video: two uploads from one creator agree by construction and add nothing.
   Past three or four sources, hand it to `@student` — six transcripts is 40,000
   words and belongs in its own context, not in their chat.

6. **Judge the teacher before the lesson.** Who made it, what they have actually
   shipped, and when. A confident stranger is not a professional; say so when
   you couldn't tell. `SOURCES.md` has the tells and what they mean.

   **Then stop, once, before writing.** Three lines: how much was advert and
   what survived, do the sources agree, what it'll be called. Then ask.

7. **Keep only what transfers** — the steps, the judgement behind each, the
   beginner mistakes, the kit really used. Drop theory that changes nothing they
   would do on Monday.

8. **Write it — one note, or a folder if the sources really disagree.** Ads,
   Excel, SEO: everyone does the same thing with better tips, so blend them —
   the blend *is* the truth. Trading, diet, lifting: rival schools, so
   `./os new ongoing "<Subject>"`, one file per school, never blended. Mixing
   one school's entries with another's risk rules is how people get hurt.
   For one note, make it a note outright — numbered steps read as *work* to the
   sorter. **`.os/templates/learning.md` is the shape, and `NOTE.md` here is how
   to fill it.** Follow those headings: don't invent your own, and don't keep the
   plain note's. Every subject hides one real disagreement — find it.

9. **Teach the folder the words.** A subject arrives with its own vocabulary,
   and that is how `./os` guesses where the next thing goes:
   ```bash
   ./os words <domain> "ad set" "learning phase" "roas"
   ```
   Eight to fifteen terms *this subject uses and the folder doesn't* — what
   they'd type in a passing thought six months from now, minus anything another
   domain already owns. It writes a `learned` list, never their own. Say nothing.

10. **Then make it usable** — the step that earns this skill. Offer **one**:
    - a job they'll repeat → `/make-skill`, written while the note is still open
    - a craft they'll get better at → `./os new ongoing "<the craft>"`, with the
      practice step as the standard it holds to
    - neither → the note is enough. Say so and stop.

## Rules

- Never teach from a source you didn't open — no summarising a video by its
  title, a page by its search snippet, or a book by its blurb. If the words
  won't come, say that rather than guessing.
- Where practitioners genuinely disagree, say who argues what. Don't flatten a
  live debate into one confident answer.
- One short quote per source, in quotes, attributed. Never reproduce a
  transcript, page, lesson or chapter wholesale — cite the timestamp or URL.
- Date anything where the craft moves. Six-year-old advice about a platform is a
  claim, not a fact, and so is undated documentation.
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
every claim traced to a named source — a timestamp or a URL with a date — they
could go and check themselves. And the folder knows the subject's words.
