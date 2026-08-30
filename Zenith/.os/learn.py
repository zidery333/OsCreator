"""Fetching and cleaning source material, so any AI can learn from it.

This is the mechanical half of learning: list what a channel has, pull the
words out of a video, cache them. It makes no judgements — which sources are
worth reading, what the method is, and whether somebody is selling you a
course are all questions for whatever AI is driving. Those cannot be hardcoded
and this file does not try.

The one part of Zenith that goes out to the internet, and the one part that
wants something installed (yt-dlp). Everything else works with neither.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional

CACHE_DIRNAME = "transcripts"
STAMP_EVERY = 30            # seconds between timestamps in cleaned text
LIST_LIMIT = 60             # titles pulled when ranking a channel
FETCH_TIMEOUT = 300


# ── finding yt-dlp ───────────────────────────────────────────────────────────

def ytdlp() -> Optional[str]:
    """Where yt-dlp is, or None. Checks PATH, then the usual pip locations."""
    found = shutil.which("yt-dlp")
    if found:
        return found
    for guess in (Path.home() / ".local/bin/yt-dlp",
                  Path("/opt/homebrew/bin/yt-dlp"),
                  Path("/usr/local/bin/yt-dlp")):
        if guess.exists() and os.access(guess, os.X_OK):
            return str(guess)
    return None


def install_hint() -> str:
    """The one line to type. Whichever installer this machine actually has."""
    if shutil.which("pipx"):
        return "pipx install yt-dlp"
    if shutil.which("brew") and sys.platform == "darwin":
        return "brew install yt-dlp"
    return f"{Path(sys.executable).name} -m pip install --user yt-dlp"


# ── urls ─────────────────────────────────────────────────────────────────────

_ID = re.compile(r"(?:v=|/shorts/|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})")


def video_id(url: str) -> Optional[str]:
    """The eleven characters that name a video, from any shape of link."""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    hit = _ID.search(url)
    return hit.group(1) if hit else None


def as_listing(url: str) -> str:
    """Point a channel link at its uploads, leaving playlists alone."""
    if "list=" in url or "/playlist" in url:
        return url
    if re.search(r"/(videos|streams|shorts|featured)/?$", url):
        return re.sub(r"/(streams|shorts|featured)/?$", "/videos", url)
    if re.search(r"(youtube\.com/(@|c/|channel/|user/))", url):
        return url.rstrip("/") + "/videos"
    return url


# ── cleaning ─────────────────────────────────────────────────────────────────

_TAG = re.compile(r"<[^>]+>")
_CUE = re.compile(r"^(\d\d):(\d\d):(\d\d)[.,]\d+\s+-->")
_ENTITY = (("&gt;", ">"), ("&lt;", "<"), ("&amp;", "&"),
           ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "))


def clean_vtt(path: Path, stamp_every: int = STAMP_EVERY) -> str:
    """Auto-captions into readable prose, with a timestamp every half minute.

    YouTube's automatic captions roll: each cue repeats the tail of the one
    before it so the words appear to scroll. Left alone that triples the size
    and makes it unquotable. Dropping any line identical to the last one
    collapses it — 271KB of subtitles became 30KB of prose in testing, and the
    timestamps survive, which is what makes a claim in a note checkable.
    """
    out: List[str] = []
    last = None
    seconds = 0.0
    next_stamp = 0.0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cue = _CUE.match(line)
        if cue:
            h, m, s = (int(x) for x in cue.groups())
            seconds = h * 3600 + m * 60 + s
            continue
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        text = _TAG.sub("", line).strip()
        for entity, char in _ENTITY:
            text = text.replace(entity, char)
        if not text or text == last:
            continue
        last = text
        if seconds >= next_stamp:
            out.append("\n[%02d:%02d] " % (int(seconds) // 60, int(seconds) % 60))
            next_stamp = seconds + stamp_every
        out.append(text + " ")
    return "".join(out).strip() + "\n"


# ── the cache ────────────────────────────────────────────────────────────────

def cache_dir(root: Path) -> Path:
    """Transcripts live in .os/, not in a bucket.

    Anything unfiled sitting in Work or Notes gets adopted by `./os sort`
    and reported as clutter until it is. Six transcripts per subject would
    either be numbered as notes or nag forever, so they are kept out of the
    buckets entirely — and keyed by video, so a source pulled for one subject
    is free for the next.
    """
    path = root / ".os" / CACHE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached(root: Path, vid: str) -> Optional[Path]:
    path = cache_dir(root) / f"{vid}.txt"
    return path if path.exists() else None


# ── talking to yt-dlp ────────────────────────────────────────────────────────

def _run(args: List[str], timeout: int = FETCH_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def listing(url: str, limit: int = LIST_LIMIT) -> List[dict]:
    """What a channel has, cheaply — titles and view counts, nothing fetched.

    Deliberately returns everything and ranks nothing. Sorting by views alone
    is the surest way to end up with whatever had the best thumbnail, which in
    exactly the subjects people want to learn is the sales material.
    """
    exe = ytdlp()
    if not exe:
        raise RuntimeError("no-ytdlp")
    fields = "%(id)s\t%(view_count)s\t%(upload_date)s\t%(duration)s\t%(title)s"
    done = _run([exe, "--flat-playlist", "--playlist-end", str(limit),
                 "--ignore-errors", "--no-warnings", "--print", fields,
                 as_listing(url)])
    rows = []
    for line in done.stdout.splitlines():
        bits = line.split("\t")
        if len(bits) != 5 or len(bits[0]) != 11:
            continue
        vid, views, date, secs, title = bits
        rows.append({
            "id": vid,
            "views": int(views) if views.isdigit() else None,
            "date": date if date.isdigit() else None,
            "minutes": round(int(secs) / 60) if secs.isdigit() else None,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    if not rows and done.returncode:
        said = (done.stderr or "").strip().splitlines()
        raise RuntimeError(said[-1] if said else "nothing came back")
    return rows


def transcript(root: Path, url: str, force: bool = False) -> dict:
    """The words of one video, cleaned and cached. Cheap on a second call."""
    vid = video_id(url)
    if not vid:
        return {"url": url, "ok": False, "why": "that is not a video link"}
    have = cached(root, vid)
    if have and not force:
        return {"id": vid, "ok": True, "path": str(have),
                "words": len(have.read_text(encoding="utf-8").split()), "cached": True}
    exe = ytdlp()
    if not exe:
        raise RuntimeError("no-ytdlp")
    with tempfile.TemporaryDirectory() as tmp:
        done = _run([exe, "--skip-download", "--write-subs", "--write-auto-subs",
                     "--sub-lang", "en.*", "--sub-format", "vtt",
                     "--no-warnings", "-o", str(Path(tmp) / "%(id)s"),
                     f"https://www.youtube.com/watch?v={vid}"])
        vtts = sorted(Path(tmp).glob("*.vtt"))
        if not vtts:
            why = "no captions on this one"
            err = (done.stderr or "").lower()
            if "sign in" in err or "bot" in err:
                why = "YouTube asked for a sign-in — try again later"
            return {"id": vid, "ok": False, "why": why}
        # A hand-written track beats machine captions where both exist.
        best = min(vtts, key=lambda p: (".auto." in p.name, len(p.name)))
        text = clean_vtt(best)
    out = cache_dir(root) / f"{vid}.txt"
    out.write_text(text, encoding="utf-8")
    return {"id": vid, "ok": True, "path": str(out),
            "words": len(text.split()), "cached": False}


def transcripts(root: Path, urls: Iterable[str], force: bool = False) -> List[dict]:
    return [transcript(root, u, force) for u in urls]


def inventory(root: Path) -> List[dict]:
    out = []
    for path in sorted(cache_dir(root).glob("*.txt")):
        out.append({"id": path.stem, "path": str(path),
                    "words": len(path.read_text(encoding="utf-8").split())})
    return out


def forget(root: Path, ids: Iterable[str]) -> int:
    gone = 0
    for vid in ids:
        path = cache_dir(root) / f"{vid}.txt"
        if path.exists():
            path.unlink()
            gone += 1
    return gone


# ── vocabulary ───────────────────────────────────────────────────────────────
#
# The other half of learning, and the half that compounds. Every subject comes
# with its own words — "ad set", "learning phase", "roas" — and this folder
# decides where things go by matching words. Feeding what a study run picked up
# back into `.os/words.json` is what makes the next `os save "hook rate was
# terrible"` land in the right place without anybody being told.
#
# It only ever writes a `learned` list. The person's own `keywords` stay theirs,
# and anything written here can be deleted without disturbing them.

def words_path(root: Path) -> Path:
    return root / ".os" / "words.json"


def load_words(root: Path) -> dict:
    return json.loads(words_path(root).read_text(encoding="utf-8"))


def write_words(root: Path, data: dict) -> None:
    """Rewrite words.json, keeping its indentation, its accents and its newline.

    Through a temporary file: a half-written taxonomy is worse than an old one,
    and this is the file the person is invited to edit by hand."""
    path = words_path(root)
    body = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def domains(root: Path) -> List[dict]:
    """Every domain this folder knows, and how much vocabulary each carries."""
    out = []
    for name, spec in load_words(root).get("domains", {}).items():
        out.append({"domain": name, "label": spec.get("label", name),
                    "keywords": len(spec.get("keywords", [])),
                    "learned": len(spec.get("learned", []))})
    return out


def teach(root: Path, domain: str, terms: Iterable[str]) -> dict:
    """Add what a subject taught to one domain's `learned` list."""
    data = load_words(root)
    blocks = data.get("domains", {})
    if domain not in blocks:
        return {"ok": False, "why": f"no domain called {domain!r}",
                "domains": sorted(blocks)}
    spec = blocks[domain]
    # Never say twice what the person already said once, in either list.
    known = {w.strip().lower() for w in spec.get("keywords", [])}
    known |= {w.strip().lower() for w in spec.get("learned", [])}
    added: List[str] = []
    already: List[str] = []
    for raw in terms:
        term = re.sub(r"\s+", " ", str(raw).strip().lower())
        if not term or len(term) > 40:
            continue
        if term in known:
            already.append(term)
            continue
        known.add(term)
        added.append(term)
    if added:
        spec["learned"] = list(spec.get("learned", [])) + added
        blocks[domain] = spec
        data["domains"] = blocks
        write_words(root, data)
    return {"ok": True, "domain": domain, "added": added,
            "already_known": already, "learned_now": len(spec.get("learned", []))}


# ── the command line ─────────────────────────────────────────────────────────
#
# Everything above is a library; this is the half an AI can actually reach.
# Output is JSON on stdout, always, so whatever is driving can read it without
# parsing prose. Anything gone wrong is JSON too, with a `why` a person could
# act on — a missing yt-dlp says the one line to type on *this* machine.

def find_root(start: Optional[Path] = None) -> Path:
    """The Zenith folder this is running inside. Same rule the engine uses."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".os" / "config.json").exists():
            return candidate
    return Path(__file__).resolve().parent.parent


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "help"
    rest = args[1:]
    root = find_root()

    def emit(payload) -> int:
        print(json.dumps(payload, indent=2))
        return 0

    if command in ("-h", "--help", "help"):
        return emit({"commands": {
            "list <channel-or-playlist-url>": "what it has: id, title, views, date, minutes",
            "get <video-url> [more...]": "fetch and cache the words of each",
            "have": "transcripts already cached",
            "forget <video-id> [more...]": "drop cached transcripts",
            "words": "the domains this folder knows",
            "words <domain> <term> [more...]": "teach a domain the words a subject uses",
        }, "note": "every command prints JSON. list and get need yt-dlp; words does not."})

    try:
        if command == "list":
            if not rest:
                return emit({"ok": False, "why": "give me a channel or playlist link"})
            return emit({"ok": True, "videos": listing(rest[0])})
        if command == "get":
            if not rest:
                return emit({"ok": False, "why": "give me one or more video links"})
            return emit({"ok": True, "results": transcripts(root, rest)})
        if command == "have":
            return emit({"ok": True, "cached": inventory(root)})
        if command == "forget":
            return emit({"ok": True, "removed": forget(root, rest)})
        if command == "words":
            if not rest:
                return emit({"ok": True, "domains": domains(root),
                             "usage": 'words <domain> "<term>" [more...]'})
            if len(rest) == 1:
                return emit({"ok": False,
                             "why": "give me a domain and at least one word",
                             "domains": [d["domain"] for d in domains(root)]})
            return emit(teach(root, rest[0], rest[1:]))
    except RuntimeError as exc:
        if "no-ytdlp" in str(exc):
            return emit({"ok": False, "why": "yt-dlp is not installed",
                         "fix": install_hint(),
                         "note": "everything else in this folder works without it"})
        return emit({"ok": False, "why": str(exc)})
    except (OSError, ValueError) as exc:
        return emit({"ok": False, "why": f"could not read .os/words.json — {exc}"})
    except subprocess.TimeoutExpired:
        return emit({"ok": False, "why": "that took too long — try one video at a time"})

    return emit({"ok": False, "why": f"no such command '{command}'",
                 "try": "python3 .os/learn.py help"})


if __name__ == "__main__":
    raise SystemExit(main())

