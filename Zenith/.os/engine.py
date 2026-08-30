#!/usr/bin/env python3
"""
Zenith — engine
=================
A folder that keeps itself organised, in one file. Works with any AI, or none.

Rules this file obeys:
  1. Python 3.9+ standard library only. No installs, no network, works offline.
  2. Never destroys data. Every move is written down and reversible with `os undo`.
  3. Location independent. Rename or move the folder; it finds its own root.
  4. Deterministic. Same input tree -> same output tree. Filing is idempotent.
  5. Fails loudly and specifically, never silently.

The folders it manages (see .os/config.json):
    Work         work, in either phase        -> auto-grouped, tagged W.nn
                 (status: pushing = has a next action;
                  holding = has a standard, no next action)
    Notes        anything you look up later   -> auto-grouped, tagged N.nn
                 (prose, and files too: a PDF gets a tagged card beside it)
    Archive      no longer live, still searchable -> filed by date

The letter in `W.04` says what a thing *is*, not where it sits: closing it moves
it into Archive and it stays W.04. Only Work and Notes ever hand out a tag, so
Archive has no letter of its own — it never needed one.

Three folders, and none of them is a decision the person has to make. `os save`
writes a thing down and files it in one step, through a staging file under
.os/cache/ that exists for the length of one command — there is no drop folder
to remember, and so nothing that can sit in one being forgotten. Anything
dropped straight into a bucket by hand is adopted where it lies by `os sort`.

A file is a thing you look up later, so it is a note that happens to be bytes;
what happened and what was decided belongs in the item it happened to, under
`## Log` and `## Decisions`, findable by that item's own number.

Skills, helpers and hooks live in .claude/ and are catalogued, never moved.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import zipfile
from pathlib import Path

ENGINE_VERSION = "3.0.0"
MARKER = ".os"
TOOLKIT = "_tools"   # skills/helpers/hooks: they live in .claude/, not a numbered folder
#: Where `os save` puts a thing for the moment between writing it down and
#: working out where it goes. Under .os/, deliberately: a drop folder somebody
#: can see is a drop folder things get left in.
STAGING = "incoming"
#: A capture still sitting in there after this long has been missed rather than
#: only just written, so `os check` stops calling it a warning and calls it an
#: error. It is never deleted on a timer: see `staged_captures`.
STALE_STAGE_DAYS = 7
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".org"}

# ---------------------------------------------------------------------------
# terminal style
# ---------------------------------------------------------------------------


class S:
    """ANSI styling that degrades to plain text when it should."""

    enabled = True

    RESET = "\033[0m"
    B = "\033[1m"

    INK = "\033[38;5;252m"
    MUTE = "\033[38;5;245m"
    FAINT = "\033[38;5;240m"
    GOLD = "\033[38;5;179m"
    AMBER = "\033[38;5;215m"
    JADE = "\033[38;5;72m"
    SKY = "\033[38;5;110m"
    RED = "\033[38;5;167m"

    @classmethod
    def setup(cls, mode: str = "auto") -> None:
        if mode == "never" or os.environ.get("NO_COLOR"):
            cls.enabled = False
        elif mode == "always":
            cls.enabled = True
        else:
            cls.enabled = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
        if not cls.enabled:
            for k in list(vars(cls)):
                if k.isupper() and isinstance(getattr(cls, k), str):
                    setattr(cls, k, "")


def speak_utf8() -> None:
    """Make the output streams carry the characters this program actually prints.

    A terminal running under `LANG=C`, or any narrow locale, encodes stdout as
    ASCII — and a single box-drawing rule is then an unhandled exception rather
    than a line, so `os status` dies on its own heading before saying anything.
    Ask for UTF-8, and fall back to replacing whatever cannot be rendered: a
    question mark in place of a tick beats a traceback in place of the answer."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def paint(text: str, *styles: str) -> str:
    if not S.enabled or not styles:
        return text
    return "".join(styles) + text + S.RESET


def vlen(text: str) -> int:
    """Visible length: strips ANSI, counts wide glyphs as 2 columns."""
    bare = re.sub(r"\033\[[0-9;]*m", "", text)
    n = 0
    for ch in bare:
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def pad(text: str, width: int, align: str = "<") -> str:
    gap = max(0, width - vlen(text))
    if align == ">":
        return " " * gap + text
    if align == "^":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def trunc(text: str, width: int) -> str:
    if vlen(text) <= width:
        return text
    out = ""
    for ch in text:
        if vlen(out + ch) > width - 1:
            break
        out += ch
    return out + "…"


class Out:
    """Everything the CLI prints goes through here."""

    quiet = False

    @staticmethod
    def raw(line: str = "") -> None:
        if not Out.quiet:
            print(line)

    @staticmethod
    def title(text: str, sub: str = "") -> None:
        Out.raw()
        Out.raw("  " + paint(text.upper(), S.B, S.GOLD) + ("  " + paint(sub, S.FAINT) if sub else ""))
        Out.raw("  " + paint("─" * max(10, vlen(text)), S.FAINT))

    @staticmethod
    def item(bullet: str, text: str, style: str = "") -> None:
        Out.raw("  " + paint(bullet, style or S.MUTE) + " " + text)

    @staticmethod
    def ok(text: str) -> None:
        Out.item("✓", text, S.JADE)

    @staticmethod
    def warn(text: str) -> None:
        Out.item("▲", text, S.AMBER)

    @staticmethod
    def bad(text: str) -> None:
        Out.item("✖", text, S.RED)

    @staticmethod
    def info(text: str) -> None:
        Out.item("·", text, S.SKY)

    @staticmethod
    def note(text: str) -> None:
        Out.raw("    " + paint(text, S.FAINT))

    @staticmethod
    def kv(key: str, value: str, width: int = 16) -> None:
        Out.raw("  " + paint(pad(key, width), S.MUTE) + value)


def die(message: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    print("  " + paint("✖ " + message, S.RED), file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------


def today(fmt: str = "%Y-%m-%d") -> str:
    return _dt.date.today().strftime(fmt)


def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def slugify(text: str, limit: int = 56) -> str:
    """ASCII slug where possible; otherwise keep the author's own characters.

    Transliterating "設計ノート" to "untitled" would be a quiet data loss, so a
    name that carries no ASCII falls back to a filesystem-safe unicode slug."""
    raw = str(text)

    def condense(source: str, flags: int = 0) -> str:
        cleaned = re.sub(r"[^\w\s-]", " ", source, flags=flags).strip().lower()
        return re.sub(r"[\s_-]+", "-", cleaned).strip("-")

    ascii_form = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    ascii_slug = condense(ascii_form)
    native_slug = condense(raw, re.UNICODE)

    # Prefer ASCII, but only when transliteration kept everything. "Проект 2026"
    # must not become "2026" — that silently deletes the name.
    def weight(slug: str) -> int:
        return len([c for c in slug if c.isalnum()])

    slug = ascii_slug if weight(ascii_slug) >= weight(native_slug) else native_slug
    if len(slug) > limit:
        slug = slug[:limit].rstrip("-")
    return slug or "untitled"


def titleize(text: str) -> str:
    text = re.sub(r"[-_]+", " ", str(text)).strip()
    small = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "vs"}
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if w.isupper() and len(w) <= 4:
            out.append(w)
        elif i and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out) or "Untitled"


TIMESTAMP_PREFIX = re.compile(r"^\d{6,8}[-_]\d{4,6}[-_]?")


COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def gist(body: str, limit: int = 180) -> str:
    """The readable opening of a document.

    Blueprints are full of `<!-- prompts -->` telling the author what to write.
    They are instructions, not content — showing them in a search result or a
    summary just leaks the scaffolding at somebody."""
    text = COMMENT_RE.sub(" ", body)
    text = re.sub(r"^#.*$", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*]\s*(\[[ xX]\])?\s*$", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip(" -\t\n")[:limit]


def _shorten(text: str, limit: int = 62, ellipsis: str = "") -> str:
    """Trim to `limit`, but never mid-word, and never leave dangling punctuation."""
    text = text.strip().rstrip(".:;,-– ")
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    if " " in clipped:
        clipped = clipped[:clipped.rfind(" ")]
    return clipped.rstrip(".:;,-– ") + ellipsis


def infer_title(body: str, path: Path) -> str:
    """The best short human title we can find: an H1, then the opening sentence,
    then the filename with any timestamp stripped off.

    Short matters. This becomes the folder name the person actually sees, so a
    whole paragraph is a worse title than its first clause."""
    _, body = parse_frontmatter(body) if body.lstrip().startswith("---") else ({}, body)
    h1 = re.search(r"^#\s+(.+?)\s*$", body, re.M)
    if h1 and h1.group(1).strip():
        return _shorten(h1.group(1), 72)
    for raw in body.split("\n"):
        line = raw.strip()
        if not line or line.startswith(("---", "<!--", "```", "|", ">", "#")):
            continue
        line = re.sub(r"^[-*+]\s*(\[[ xX]\]\s*)?", "", line)
        line = re.sub(r"[*_`]", "", line).strip()
        if len(line) < 3:
            continue
        # first sentence, or first clause if the sentence runs long
        cut = re.split(r"(?<=[.!?])\s|\s[-–—]\s", line)[0].strip()
        if len(cut) > 62:
            head = re.split(r"[:;]\s", cut)[0].strip()
            if 12 <= len(head) < len(cut):
                cut = head
        return _shorten(cut if len(cut) >= 12 else line)
    return titleize(TIMESTAMP_PREFIX.sub("", path.stem))


DIGEST_CAP = 1_000_000   # bytes read per file before we fall back to metadata


def _digest_file(h, path: Path) -> None:
    """Hash a file's content, or its shape when the file is large.

    Reading a 4 GB video to notice it has not changed is not insight, it is a
    stall. Past the cap we hash size and mtime instead, which is enough to spot
    a change and costs nothing."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size > DIGEST_CAP:
        h.update(f"{path.name}:{size}:{int(path.stat().st_mtime)}".encode())
        return
    try:
        h.update(path.read_bytes())
    except OSError:
        pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    try:
        if path.is_dir():
            for f in sorted(p for p in path.rglob("*") if p.is_file())[:500]:
                h.update(str(f.relative_to(path)).encode())
                _digest_file(h, f)
        else:
            _digest_file(h, path)
    except OSError:
        return ""
    return h.hexdigest()[:16]


def is_binary(path: Path) -> bool:
    """A NUL byte in the first block means this is data, whatever it is named.

    People rename things by accident, and a .md file full of bytes should be
    kept as a file, not read as prose and given a title made of noise."""
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return False


def read_text(path: Path, limit: int = 400_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def write_text(path: Path, text: str) -> None:
    """Atomic write: temp file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp~")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def human_size(n: float) -> str:
    if n < 1024:
        return f"{int(n)}B"
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        n /= 1024.0
        if n < 1024 or unit == "PB":
            return f"{n:.1f}{unit}"
    return f"{n:.1f}PB"


def days_since(stamp: str) -> int:
    if not stamp:
        return 9999
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = _dt.datetime.strptime(stamp[: len(fmt) + 2].strip(), fmt)
            return (_dt.datetime.now() - d).days
        except ValueError:
            continue
    return 9999


def unique_path(target: Path) -> Path:
    """Never overwrite. foo.md -> foo-2.md -> foo-3.md

    A directory has no extension, whatever the dots in its name say. Every item
    folder here is called `2.01_ship-the-rewrite`, and splitting that on the last
    dot turns a collision into `2-2.01_ship-the-rewrite` — a name that no longer
    starts with its own number, so the folder stops announcing what it is."""
    if not target.exists() and not target.is_symlink():
        return target
    if target.is_dir():
        stem, suffix = target.name, ""
    else:
        stem, suffix = target.stem, target.suffix
    for n in range(2, 500):
        candidate = target.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
    return target.with_name(f"{stem}-{int(time.time())}{suffix}")


# ---------------------------------------------------------------------------
# front matter  (a deliberately small, predictable YAML subset)
# ---------------------------------------------------------------------------

FIELD_ORDER = [
    "id", "title", "type", "status", "domain", "tags",
    "created", "updated", "owner", "source", "links", "summary",
]

_SCALAR_TRUE = {"true", "yes", "on"}
_SCALAR_FALSE = {"false", "no", "off"}

# Fields that are always text, however numeric they look. An ID like 10.01 must
# never become the float 10.01 — that would round-trip as "10.1" and lose the item.
STRING_KEYS = {
    "id", "title", "status", "domain", "created", "updated", "archived", "was",
    "version", "source", "summary", "name", "description", "origin", "captured",
}


def _scalar(raw: str, key: str | None = None):
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in "\"'" and raw[-1] == raw[0] and len(raw) > 1:
        inner = raw[1:-1]
        if raw[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    low = raw.lower()
    if low in _SCALAR_TRUE:
        return True
    if low in _SCALAR_FALSE:
        return False
    if low in ("null", "none", "~"):
        return None
    if key in STRING_KEYS:
        return raw
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    return raw


def _inline_list(raw: str, key: str | None = None):
    """Split `[a, "b, c", "d\\"e"]` on the commas that separate items.

    Quotes are kept on the part and handed to `_scalar`, which is the one place
    that knows how to unquote and unescape. Splitting them off here as well
    meant a `\\"` inside an item closed it early, so a tag written back out
    correctly still came apart on the way in."""
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    parts, buf, quote, escaped = [], "", "", False
    for ch in inner:
        if quote:
            buf += ch
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf += ch
        elif ch == ",":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [_scalar(p, key) for p in parts if p.strip() != ""]


def parse_frontmatter(text: str):
    """Return (meta_dict, body). Tolerant: bad front matter is treated as body."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, min(len(lines), 400)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return {}, text

    meta, key = {}, None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s*-\s", line) and key is not None:
            value = _scalar(re.sub(r"^\s*-\s*", "", line), key)
            if not isinstance(meta.get(key), list):
                meta[key] = [] if meta.get(key) in ("", None) else [meta[key]]
            meta[key].append(value)
            continue
        m = re.match(r"^([A-Za-z_][\w .-]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, raw = m.group(1).strip(), m.group(2).strip()
        if raw.startswith("[") and raw.endswith("]"):
            meta[key] = _inline_list(raw, key)
        elif raw == "":
            meta[key] = ""
        else:
            meta[key] = _scalar(raw, key)
    body = "\n".join(lines[end + 1:])
    return meta, body.lstrip("\n")


#: Characters that end an item early when it sits inside `[a, b]`. A tag reading
#: "billing, urgent" written bare comes back as two tags, and one holding a `]`
#: truncates every tag after it — so list items get a stricter test than scalars.
_NEEDS_QUOTES_IN_LIST = re.compile(r"[,\[\]{}]|^\s|\s$")


def _emit(value, key: str | None = None, in_list: bool = False) -> str:
    if key in STRING_KEYS and isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value):
        return '"' + value + '"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_emit(v, key, in_list=True) for v in value) + "]"
    text = str(value)
    if text == "":
        return '""'
    if re.search(r"^[\s>|&*!%@`{\[]|:\s|#\s|\"|'|:$", text) \
            or (in_list and _NEEDS_QUOTES_IN_LIST.search(text)):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def render_frontmatter(meta: dict) -> str:
    keys = [k for k in FIELD_ORDER if k in meta]
    keys += [k for k in meta if k not in keys]
    lines = ["---"]
    for k in keys:
        lines.append(f"{k}: {_emit(meta[k], k)}")
    lines.append("---")
    return "\n".join(lines)


def compose(meta: dict, body: str) -> str:
    return render_frontmatter(meta) + "\n\n" + body.lstrip("\n").rstrip() + "\n"


def stamp_file(path: Path, meta_updates: dict, force: tuple = ()) -> dict:
    """Merge front matter into a markdown file on disk. Returns the merged meta.

    Existing values win by default — what somebody wrote by hand is not ours to
    overwrite. `force` names the keys where we know better, which in practice
    means `id` during a renumber: two items cannot keep the same number."""
    text = read_text(path)
    meta, body = parse_frontmatter(text)
    if not body.strip() and not meta:
        body = text
    for k, v in meta_updates.items():
        if v is None:
            continue
        if k in force or k not in meta or meta.get(k) in ("", [], None):
            meta[k] = v
    meta["updated"] = today()
    write_text(path, compose(meta, body))
    return meta


# ---------------------------------------------------------------------------
# root discovery
# ---------------------------------------------------------------------------


def ensure_runnable(root: Path) -> list:
    """Put back the executable bit on `os` and the hooks.

    A zip extracted on Windows, or a download that drops Unix modes, arrives
    with `os` not executable — and `./os` then fails with a bare "permission
    denied" that tells a newcomer nothing. As long as this runs by any route
    (`bash os`, `python3 .os/engine.py`, one chmod), it repairs itself for good.
    Silent, and never fatal: a read-only copy is still perfectly usable."""
    healed = []
    targets = [root / "os"]
    hooks = root / ".claude" / "hooks"
    if hooks.is_dir():
        targets += [h for h in hooks.iterdir()
                    if h.is_file() and h.suffix in (".sh", ".zsh", ".bash", ".py")]
    for target in targets:
        try:
            if target.is_file() and not os.access(target, os.X_OK):
                target.chmod(target.stat().st_mode | 0o755)
                healed.append(target.name)
        except OSError:
            pass
    return healed


def relative_to_root(path: Path, root: Path) -> str:
    """Where something sits inside the folder, in POSIX form.

    Never resolves first. A symlink in Notes/ points outside the folder, and
    resolving it produces a path that is not under the root at all — which used
    to raise straight out of the indexer and leave every command broken until
    somebody found and deleted the link by hand."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        return path.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return str(path)


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` looking for the .os marker. Rename-safe by design.

    An explicit `start` — what `--root` passes — beats everything. The launcher
    always exports ZENITH_HOME, so consulting the environment first made --root
    silently do nothing at all."""
    if start is not None:
        here = Path(start).expanduser().resolve()
        for candidate in [here, *here.parents]:
            if (candidate / MARKER / "config.json").exists():
                return candidate
        die(f"{start} is not a Zenith folder, and neither is anything above it.\n"
            "     --root wants the folder that holds AGENTS.md and .os/")

    env = os.environ.get("ZENITH_HOME")
    if env and (Path(env).expanduser() / MARKER / "config.json").exists():
        return Path(env).expanduser().resolve()
    here = Path.cwd().resolve()
    for candidate in [here, *here.parents]:
        if (candidate / MARKER / "config.json").exists():
            return candidate
    # engine.py itself lives in <root>/.os/
    guess = Path(__file__).resolve().parent.parent
    if (guess / MARKER / "config.json").exists():
        return guess
    die("no Zenith root found (looked for a .os/config.json here and in every parent)")


# ---------------------------------------------------------------------------
# the OS object: config, state, history
# ---------------------------------------------------------------------------


class Zenith:
    def __init__(self, root: Path):
        self.root = root
        self.dot = root / MARKER
        self.config = self._load_json(self.dot / "config.json", required=True)
        # `words.json` is the one file people are told to edit, so it gets a name
        # that says what is in it. Older folders called it taxonomy.json. Only
        # fall back when it is genuinely absent — a broken one must say so by name.
        words = self.dot / "words.json"
        legacy = self.dot / "taxonomy.json"
        self.taxonomy = self._load_json(legacy if legacy.exists() and not words.exists()
                                        else words, required=True)
        self.state = self._load_json(self.dot / "state.json") or {
            "counters": {}, "undo": [], "history": [], "created": now_iso(),
        }
        self.thresholds = self.config["thresholds"]
        self.behaviour = self.config["behaviour"]
        self.date_fmt = self.behaviour.get("date_format", "%Y-%m-%d")
        self._pending: list[dict] = []
        self._rel_cache: dict[str, str] = {}
        self._snapshots: dict[str, str] = {}
        self._run_dir: Path | None = None

    # -- persistence --------------------------------------------------------

    @staticmethod
    def _load_json(path: Path, required: bool = False):
        """Read a settings file. A broken one is always reported by name: these
        are files people edit by hand, and a stray comma should say so."""
        if not path.exists():
            if required:
                die(f".os/{path.name} is missing.\n"
                    "     Restore it from a backup in .os/backups/, or copy it "
                    "from a fresh copy of this folder.")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            die(f".os/{path.name} has a typo in it and can't be read.\n"
                f"     {exc}\n"
                "     Usually a missing comma or an unclosed quote. Fix that line, "
                "or restore the file from .os/backups/.")
        except OSError as exc:
            die(f"cannot read .os/{path.name}: {exc}")

    def save_state(self) -> None:
        self.state["saved"] = now_iso()
        write_text(self.dot / "state.json", json.dumps(self.state, indent=2) + "\n")

    def save_config(self) -> None:
        write_text(self.dot / "config.json", json.dumps(self.config, indent=2) + "\n")

    # -- paths --------------------------------------------------------------

    def rel(self, path: Path) -> str:
        """Path relative to the root, always with forward slashes so it reads the
        same on every machine. Memoised: resolve() is a syscall, and one
        `os check` on a big folder asks for the same paths thousands of times."""
        key = str(path)
        hit = self._rel_cache.get(key)
        if hit is None:
            hit = relative_to_root(path, self.root)
            if len(self._rel_cache) < 20_000:
                self._rel_cache[key] = hit
        return hit

    def buckets(self) -> dict:
        return self.config["buckets"]

    def bucket_for_role(self, role: str) -> str:
        for name, spec in self.buckets().items():
            if spec["role"] == role:
                return name
        die(f"no folder is set up for '{role}' — check .os/config.json")

    # -- ids ----------------------------------------------------------------

    def next_id(self, bucket: str) -> str:
        code = self.buckets()[bucket]["code"]
        counters = self.state.setdefault("counters", {})
        n = int(counters.get(code, 0)) + 1
        counters[code] = n
        return f"{code}.{n:02d}"

    def reserve_id_at_least(self, bucket: str, seen: set[str]) -> None:
        """Push the counter past any IDs already on disk (repairs a lost state file).

        A folder that hands out no tags has no counter to repair. The archive is
        full of other folders' numbers — W.04 keeps its W on the way in — so
        asking what its highest is was always answering a question nobody asked."""
        code = self.buckets()[bucket].get("code")
        if not code:
            return
        top = 0
        for ident in seen:
            if ident.startswith(code + "."):
                try:
                    top = max(top, int(ident.split(".", 1)[1]))
                except ValueError:
                    pass
        counters = self.state.setdefault("counters", {})
        counters[code] = max(int(counters.get(code, 0)), top)

    # -- history / undo -----------------------------------------------------

    def record(self, action: str, src: str, dst: str = "") -> None:
        self._pending.append({"action": action, "src": src, "dst": dst})

    # -- content snapshots, so undo restores what a file said, not just where --

    def _ensure_run_dir(self) -> Path:
        if self._run_dir is None:
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self._run_dir = self.dot / "cache" / "undo" / stamp
            self._run_dir.mkdir(parents=True, exist_ok=True)
        return self._run_dir

    def snapshot(self, src: Path, cap_bytes: int = 512_000, cap_files: int = 400) -> None:
        """Copy the text content of `src` aside before anything rewrites it.

        Only text, only small files, only a bounded number of them: a snapshot
        is insurance, not a second copy of the folder."""
        try:
            targets = [src] if src.is_file() else [p for p in sorted(src.rglob("*")) if p.is_file()]
        except OSError:
            return
        for f in targets[:cap_files]:
            if f.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                if f.stat().st_size > cap_bytes:
                    continue
                data = f.read_bytes()
            except OSError:
                continue
            key = self.rel(f)
            blob = hashlib.sha256(key.encode()).hexdigest()[:20] + ".bak"
            try:
                (self._ensure_run_dir() / blob).write_bytes(data)
            except OSError:
                continue
            self._snapshots[key] = blob

    def _prune_snapshots(self) -> None:
        keep = int(self.behaviour.get("keep_undo_steps", 20))
        live = {e.get("blobs") for e in self.state.get("undo", []) if e.get("blobs")}
        base = self.dot / "cache" / "undo"
        if not base.exists():
            return
        for d in sorted(base.iterdir()):
            if d.is_dir() and d.name not in live:
                shutil.rmtree(d, ignore_errors=True)
        for d in sorted(base.iterdir())[:-max(keep, 1)]:
            shutil.rmtree(d, ignore_errors=True)

    def commit(self, label: str) -> int:
        if not self._pending:
            return 0
        entry = {"at": now_iso(), "label": label, "steps": self._pending,
                 "snapshots": dict(self._snapshots),
                 "blobs": self._run_dir.name if self._run_dir else ""}
        undo = self.state.setdefault("undo", [])
        undo.append(entry)
        keep = int(self.behaviour.get("keep_undo_steps", 20))
        self.state["undo"] = undo[-keep:]
        history = self.state.setdefault("history", [])
        history.append({"at": entry["at"], "label": label, "steps": len(self._pending)})
        self.state["history"] = history[-400:]
        count = len(self._pending)
        self._pending = []
        self._snapshots = {}
        self._run_dir = None
        self.save_state()
        self._prune_snapshots()
        # Deliberately not written into anything a person reads. Every operation
        # is already recorded in state["history"] above, and a log filled with
        # "save — 1 change" buries the one thing worth keeping: what you decided,
        # and why. That belongs in the item it happened to, under ## Decisions.
        return count

    # -- first run ----------------------------------------------------------

    def is_fresh(self) -> bool:
        return bool(self.state.get("fresh", False))

    def initialise(self, owner: str = "", name: str = "") -> dict:
        """Make a shipped template belong to whoever just opened it.

        A template is built on one day and opened on another. Left alone, every
        date in it would be a lie and every project would look stale on arrival."""
        day = today()
        restamped, moved = 0, 0   # moved: kept at 0; nothing needs migrating now

        for bucket, spec in self.buckets().items():
            base = self.root / bucket
            if not base.exists():
                continue
            for path in sorted(base.rglob("*.md")):
                # README.md is an item's spine here, so `ignored()` is the wrong
                # filter — it deliberately hides README from the *scanner*.
                if path.name in ("CLAUDE.md", "_index.md") or path.name.startswith("."):
                    continue
                text = read_text(path)
                meta, body = parse_frontmatter(text)
                if not meta:
                    continue
                if meta.get("created") == day and meta.get("updated") == day:
                    continue
                meta["created"] = day
                meta["updated"] = day
                write_text(path, compose(meta, body))
                restamped += 1

        if owner:
            self.config["owner"] = owner
        if name:
            self.config["name"] = name
        self.config.setdefault("review", {})["last_run"] = None
        self.save_config()

        self.state["fresh"] = False
        self.state["installed"] = day
        self.state.setdefault("undo", [])
        self.state["history"] = []
        self.save_state()
        return {"restamped": restamped, "moved": moved, "day": day,
                "owner": self.config.get("owner", ""), "name": self.config.get("name", "")}

    # -- filesystem moves (always journalled) -------------------------------

    def move(self, src: Path, dst: Path) -> Path:
        """Move one thing, and write down that it happened.

        The source can disappear under us — someone dragged it out of the
        folder, or a second run got to it first. `shutil.move` answers that with
        a raw traceback, which tells a person nothing and looks like a crash."""
        if not src.exists() and not src.is_symlink():
            raise FileNotFoundError(f"{self.rel(src)} was moved or deleted while this ran")
        self.snapshot(src)
        dst = unique_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        self.record("move", self.rel(src), self.rel(dst))
        return dst

    def move_item(self, src: Path, dst: Path) -> Path:
        """Move something, taking its asset card with it.

        A file in Notes carries a sibling `<name>.card.md` holding everything
        searchable about it. Move one without the other and you get an orphan
        card describing a file that is not there."""
        card = src.with_name(src.name + ".card.md")
        moved = self.move(src, dst)
        if card.exists():
            self.move(card, moved.with_name(moved.name + ".card.md"))
        return moved

    def created(self, path: Path) -> None:
        """Remember a file this run brought into existence, so undo can remove it."""
        if path.exists():
            self.record("create", self.rel(path))

    def make_dir(self, path: Path) -> Path:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            self.record("mkdir", self.rel(path))
        return path


class Lock:
    """One mutating command at a time.

    Two Claude sessions in the same folder is normal; two of them re-shelving it
    simultaneously is not. Read-only commands never take the lock."""

    STALE_AFTER = 900   # seconds; a lock older than this belonged to a dead run

    def __init__(self, os_: "Zenith", label: str):
        self.path = os_.dot / ".lock"
        self.label = label
        self.held = False

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (OSError, TypeError, ValueError):
            return False
        return True

    def _holder(self) -> dict | None:
        """Whoever is holding the lock right now, or None.

        None means the file on disk is debris rather than a holder: unreadable,
        ours already, left by a process that has since died, or simply older
        than any run could plausibly still be."""
        try:
            held = json.loads(self.path.read_text())
            pid = int(held["pid"])
            age = time.time() - float(held.get("at", 0))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None
        if pid == os.getpid() or not self._alive(pid) or age >= self.STALE_AFTER:
            return None
        return held

    def __enter__(self) -> "Lock":
        """Claim the lock, or say who has it.

        Checking `exists()` and *then* writing left a window: two runs starting
        together both looked, both saw nothing, and both went ahead. `O_EXCL`
        closed that one and opened a narrower one, which took concurrent CI to
        find: between creating the file and writing into it, the lock exists but
        is *empty*. A second run read it, could not parse it, correctly
        concluded that an unreadable lock is debris, deleted it — and took the
        lock. Both were then inside, and one moved a folder the other was
        midway through moving.

        So the payload is written first, under a name nobody looks at, and only
        then made visible in a single atomic step. `os.link` fails if the name
        is taken, which is the exclusion; and because the content is already
        there, a lock file that exists is always one somebody can read."""
        payload = json.dumps({"pid": os.getpid(), "at": time.time(), "label": self.label})
        tmp = self.path.with_name(f".lock.{os.getpid()}")
        for _ in range(2):
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.link(tmp, self.path)
            except FileExistsError:
                held = self._holder()
                if held is not None:
                    die(f"another run is already working here "
                        f"('{held.get('label', '?')}', pid {held.get('pid')}). "
                        "Wait for it, or remove .os/.lock if it is dead.")
                try:
                    self.path.unlink()   # debris from a run that is long gone
                except OSError:
                    pass
                continue
            except OSError:
                # A filesystem with no hard links, or one that will not take the
                # write at all. Neither is a reason to refuse somebody their own
                # folder: no lock is worse than a lock, and better than a wall.
                return self
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            self.held = True
            return self
        # lost both attempts to a run that keeps replacing the lock
        die("another run is already working here. Wait for it, or remove .os/.lock "
            "if it is dead.")

    def __exit__(self, *exc) -> bool:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
        return False


# ---------------------------------------------------------------------------
# the item model
# ---------------------------------------------------------------------------

IGNORE_NAMES = {
    ".os", ".git", ".DS_Store", ".Trash", "node_modules", "__pycache__",
    ".venv", "venv", ".idea", ".vscode", ".category", ".gitkeep",
    "_index.md", "INDEX.md", "CATALOG.md", "CLAUDE.md", "README.md",
    "desktop.ini", ".localized", "os",
}
#: All of these are plain suffixes, so `str.endswith` settles them in one C-level
#: call. `Path.match` would run five glob compilations per file, and this is asked
#: about every file in the tree, on every single command.
IGNORE_SUFFIXES = (".tmp~", ".pyc", ".swp", "~", ".card.md")
CATEGORY_MARKER = ".category"

#: Files that ARE the thing they sit in. The scanner hides them so a project
#: folder counts as one item rather than two — but anything trying to work out
#: what a folder *is* has to read them, or it is guessing blind.
SPINE_NAMES = {"README.md", "index.md", "SKILL.md", "AGENT.md"}


def ignored(path: Path) -> bool:
    name = path.name
    return (name in IGNORE_NAMES
            or name.startswith("._")
            or name.endswith(IGNORE_SUFFIXES))


def staged_captures(dot: Path) -> list[Path]:
    """Anything `os save` wrote down but never got as far as filing.

    Normally empty: save stages and files in one breath. What lands here is a
    run that died between the two — and it is the only place in the folder
    where something the person typed can sit where nothing is looking at it.
    So everything that reports on the folder looks here, and nothing sweeps
    it: content the person wrote is never thrown away on a timer."""
    stage = dot / "cache" / STAGING
    if not stage.exists():
        return []
    return sorted(p for p in stage.iterdir() if p.is_file() and not ignored(p))


class Item:
    """One thing the OS knows about."""

    __slots__ = (
        "path", "bucket", "kind", "ident", "title", "status", "domain", "tags",
        "created", "updated", "summary", "is_dir", "words", "trail",
        "flags", "fingerprint", "spine", "blurb", "managed",
    )

    def __init__(self, path: Path, bucket: str, kind: str):
        self.path = path
        self.bucket = bucket
        self.kind = kind
        self.ident = ""
        self.title = ""
        self.status = ""
        self.domain = ""
        self.tags: list[str] = []
        self.created = ""
        self.updated = ""
        self.summary = ""
        self.blurb = ""            # a skill/helper `description:`, verbatim
        self.is_dir = path.is_dir()
        self.words = 0
        self.trail: list[str] = []      # category folders between bucket and item
        self.flags: list[str] = []
        self.fingerprint = ""
        self.spine: Path | None = None  # the markdown file that carries front matter
        #: Has the OS ever taken charge of this? True iff its own header carries
        #: an `id:`. A number in the *filename* does not count — anyone can type
        #: one. Decided in hydrate(), where the front matter is already read, so
        #: asking costs nothing: `os brief` asks it about every item, every run.
        self.managed = False

    def as_dict(self, root: Path) -> dict:
        return {
            "id": self.ident,
            "title": self.title,
            "kind": self.kind,
            "bucket": self.bucket,
            "trail": self.trail,
            "path": relative_to_root(self.path, root) if self.path.exists() else "",
            "status": self.status,
            "domain": self.domain,
            "tags": self.tags,
            "created": self.created,
            "updated": self.updated,
            "summary": self.summary,
            "words": self.words,
            "flags": self.flags,
            "fingerprint": self.fingerprint,
        }


# ---------------------------------------------------------------------------
# the classifier
# ---------------------------------------------------------------------------


#: An intent block in words.json names either a kind of thing or a phase of
#: work. Both spellings are accepted: a words.json somebody customised before
#: Projects and Ongoing merged still classifies correctly.
INTENT_RESULT = {
    "pushing": ("project", "pushing"), "project": ("project", "pushing"),
    "holding": ("project", "holding"), "area": ("project", "holding"),
    "ongoing": ("project", "holding"),
    "note": ("note", ""), "asset": ("asset", ""),
}

#: How that reads in the one line the person is shown.
INTENT_WORDS = {
    "pushing": "work with a next action", "project": "work with a next action",
    "holding": "something to keep up", "area": "something to keep up",
    "ongoing": "something to keep up",
    "note": "a note", "asset": "a file",
}


class Classifier:
    """Decides what an unfiled thing is, what phase it is in, and where it goes.

    Signals, strongest first:
        1. explicit front matter (type / domain / bucket)
        2. a filename convention  (project--x.md, note--x.md, ...)
        3. structural shape       (a folder holding SKILL.md is a skill)
        4. file extension         (media and data are assets)
        5. weighted keyword score against .os/words.json
    Anything below `min_classify_score` is filed as a note and flagged
    `needs-review`, so a low-confidence guess is visible rather than silent.
    """

    NAME_HINTS = {
        "project": "project", "proj": "project", "p": "project",
        "area": "project", "ongoing": "project", "work": "project", "w": "project",
        "note": "note", "n": "note", "ref": "note",
        "skill": "skill", "agent": "agent",
        "asset": "asset", "file": "asset",
    }

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.tax = os_.taxonomy
        self.stop = set(self.tax.get("stopwords", []))
        self.asset_ext = set(self.tax.get("asset_extensions", []))
        self._compiled = {
            intent: [re.compile(p, re.I | re.M) for p in spec.get("patterns", [])]
            for intent, spec in self.tax.get("intent", {}).items()
        }
        # `keywords` is theirs and is never written by anything but them.
        # `learned` is where /learn puts the vocabulary a subject taught the
        # folder, kept separate so it stays obvious which words came from where
        # — but scored identically, because a word only helps if it counts.
        self._words = {
            name: [(kw.lower(), self._matcher(kw))
                   for kw in list(spec["keywords"]) + list(spec.get("learned", []))]
            for name, spec in self.tax["domains"].items()
        }
        self._intent_words = {
            name: [(kw.lower(), self._matcher(kw)) for kw in spec.get("keywords", [])]
            for name, spec in self.tax.get("intent", {}).items()
        }

    @staticmethod
    def _matcher(keyword: str):
        """A keyword must match a whole word, not a fragment.

        Without this, "ci" fires inside "pricing" and "ad" inside "already",
        and short keywords quietly poison every score. Phrases and keywords
        carrying punctuation are matched literally, since they cannot collide."""
        k = keyword.lower()
        if not re.fullmatch(r"[a-z0-9]+(?:[ -][a-z0-9]+)*", k):
            return None
        return re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])")

    @staticmethod
    def _hits(hay: str, kw: str, rx) -> int:
        if rx is None:
            return hay.count(kw)
        return len(rx.findall(hay))

    # -- reading ------------------------------------------------------------

    @staticmethod
    def _sample(path: Path) -> tuple[str, str]:
        """(title-ish text, body text) for a file or a folder."""
        if path.is_dir():
            names, body = [], []
            # Read the spine first: a dropped-in folder usually says what it is
            # in its README, and the scanner's ignore list hides exactly that.
            for name in ("README.md", "index.md", "SKILL.md", "AGENT.md"):
                spine = path / name
                if spine.exists() and spine.is_file():
                    body.append(read_text(spine, 20_000))
                    break
            for child in sorted(path.rglob("*"))[:60]:
                if ignored(child) and child.name not in SPINE_NAMES:
                    continue
                names.append(child.name)
                if child.is_file() and child.suffix.lower() in TEXT_SUFFIXES \
                        and child.name not in SPINE_NAMES:
                    body.append(read_text(child, 20_000))
            return path.name + " " + " ".join(names), "\n".join(body)[:120_000]
        if path.suffix.lower() in TEXT_SUFFIXES:
            return path.stem, read_text(path, 120_000)
        return path.stem, ""

    # -- scoring ------------------------------------------------------------

    def score_domain(self, title: str, body: str, suffix: str) -> tuple[str, float, dict]:
        head = " ".join(body.split("\n")[:40])
        headings = " ".join(re.findall(r"^#{1,3}\s+(.+)$", body, re.M)[:20])
        hay_title = (title + " " + headings).lower()
        hay_head = head.lower()
        hay_body = body.lower()

        scores: dict[str, float] = {}
        for name, spec in self.tax["domains"].items():
            total = 0.0
            for kw, rx in self._words[name]:
                if self._hits(hay_title, kw, rx):
                    total += 3.0
                if self._hits(hay_head, kw, rx):
                    total += 1.5
                total += min(self._hits(hay_body, kw, rx), 6) * 0.6
            if suffix and suffix in spec.get("extensions", []):
                total += 2.5
            if total:
                scores[name] = round(total, 2)
        if not scores:
            return "", 0.0, {}
        best = max(sorted(scores), key=lambda k: scores[k])
        return best, scores[best], scores

    def score_intent(self, title: str, body: str) -> tuple[str, float, dict]:
        hay = (title + "\n" + body).lower()
        scores: dict[str, float] = {}
        for intent, spec in self.tax.get("intent", {}).items():
            total = 0.0
            weight = float(spec.get("weight", 1.0))
            for kw, rx in self._intent_words[intent]:
                hits = self._hits(hay, kw, rx)
                if hits:
                    total += weight * min(hits, 4)
            for rx in self._compiled.get(intent, []):
                total += weight * min(len(rx.findall(body)), 6)
            if total:
                scores[intent] = round(total, 2)
        if not scores:
            return "note", 0.0, {}
        best = max(scores, key=lambda k: scores[k])
        return best, scores[best], scores

    def keywords(self, title: str, body: str, limit: int = 6) -> list[str]:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9+-]{2,}", (title + " " + body).lower())
        freq: dict[str, int] = {}
        for w in words:
            if w in self.stop or len(w) > 24:
                continue
            freq[w] = freq.get(w, 0) + 1
        ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
        # A word earns a tag by recurring. With no repetition there is no signal,
        # and picking the first few words just decorates a note with noise —
        # "before, billing, dies, every, fixed" tells nobody anything.
        return [w for w, n in ranked if n > 1][:limit]

    # -- the decision -------------------------------------------------------

    def classify(self, path: Path) -> dict:
        name = path.name
        suffix = path.suffix.lower()

        # A symlink is a pointer, not content. Reading through one can walk back
        # into the folder it came from, and *writing* through one drops a file
        # somewhere nobody asked for — a link to a bucket once had the sorter
        # create a README.md inside it, which the scanner then ignored forever.
        # Keep the link itself, follow nothing.
        if path.is_symlink():
            return {"kind": "asset", "domain": "unsorted", "title": titleize(path.stem),
                    "tags": [], "confidence": 10.0, "flags": [],
                    "why": ["a shortcut to somewhere else — kept as-is, not followed"],
                    "scores": {}, "summary": "", "captured": ""}

        title_src, body = self._sample(path)
        meta, stripped = parse_frontmatter(body) if body.lstrip().startswith("---") else ({}, body)
        if path.is_file() and suffix in TEXT_SUFFIXES:
            meta2, stripped2 = parse_frontmatter(read_text(path, 120_000))
            if meta2:
                meta, stripped = meta2, stripped2
        body = stripped if stripped.strip() else body

        verdict = {
            "kind": "", "status": "", "domain": "", "title": "", "tags": [],
            "confidence": 0.0, "why": [], "flags": [],
        }

        # 1. explicit front matter wins outright
        declared = str(meta.get("type", "")).strip().lower()
        if declared in ("project", "work", "area", "ongoing", "note", "asset", "file",
                        "skill", "agent", "log", "reference", "journal"):
            verdict["kind"] = TYPE_FROM_DISK.get(declared, declared)
            verdict["confidence"] += 10
            verdict["why"].append(f"front matter says type: {declared}")
        if verdict["kind"] == "project" and (meta.get("status") or declared):
            verdict["status"] = normalize_status(meta.get("status"), "project", declared)
        if meta.get("domain"):
            verdict["domain"] = slugify(str(meta["domain"]), 24)
            verdict["confidence"] += 4
            verdict["why"].append("front matter says domain")
        if meta.get("title"):
            verdict["title"] = str(meta["title"])
            verdict["title_from_meta"] = True
        elif meta.get("name") and verdict["kind"] in ("skill", "agent"):
            # skills and helpers carry `name:`, never `title:` — and that name is
            # load-bearing: it is the word people type to invoke the thing
            verdict["title"] = titleize(str(meta["name"]))
            verdict["title_from_meta"] = True
        if meta.get("tags"):
            raw = meta["tags"]
            verdict["tags"] = [slugify(str(t), 24) for t in (raw if isinstance(raw, list) else str(raw).split(","))]

        # 2. filename convention: kind--name.ext
        if not verdict["kind"]:
            m = re.match(r"^([a-z]+)--(.+)$", name, re.I)
            if m and m.group(1).lower() in self.NAME_HINTS:
                verdict["kind"] = self.NAME_HINTS[m.group(1).lower()]
                verdict["confidence"] += 8
                verdict["why"].append(f"filename prefix '{m.group(1)}--'")
                if not verdict["title"]:
                    verdict["title"] = titleize(Path(m.group(2)).stem)

        # 3. structural shape
        if not verdict["kind"]:
            if path.is_dir() and (path / "SKILL.md").exists():
                verdict["kind"] = "skill"
                verdict["confidence"] += 10
                verdict["why"].append("folder contains SKILL.md")
            elif path.is_file() and suffix in TEXT_SUFFIXES and meta.get("name") and meta.get("description") \
                    and not meta.get("type"):
                verdict["kind"] = "agent"
                verdict["confidence"] += 6
                verdict["why"].append("agent-shaped front matter (name + description)")
            elif path.is_dir() and any((path / f).exists() for f in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", ".git")):
                verdict["kind"] = "project"
                verdict["confidence"] += 7
                verdict["why"].append("folder looks like a code project")

        if not verdict.get("title_from_meta") and verdict["kind"] in ("skill", "agent") \
                and meta.get("name"):
            verdict["title"] = titleize(str(meta["name"]))
            verdict["title_from_meta"] = True

        # 4a. a folder holding nothing you would read is a folder of files
        if not verdict["kind"] and path.is_dir():
            files = [q for q in path.rglob("*") if q.is_file() and not ignored(q)]
            prose = [q for q in files if q.suffix.lower() in TEXT_SUFFIXES
                     and not is_binary(q)]
            if files and not prose:
                verdict["kind"] = "asset"
                verdict["confidence"] += 6
                verdict["why"].append(f"{len(files)} file(s) and nothing to read")

        # 4. extension — and what the bytes actually say
        if not verdict["kind"] and path.is_file():
            if suffix in self.asset_ext or (suffix and suffix not in TEXT_SUFFIXES):
                verdict["kind"] = "asset"
                verdict["confidence"] += 5
                verdict["why"].append(f"'{suffix or 'no extension'}' is not prose")
            elif is_binary(path):
                verdict["kind"] = "asset"
                verdict["confidence"] += 6
                verdict["why"].append("named like text, but the contents are data")
                verdict["title"] = verdict["title"] or titleize(path.stem)
                body = ""

        # 5. content scoring
        domain, dscore, dall = self.score_domain(title_src, body, suffix)
        intent, iscore, iall = self.score_intent(title_src, body)
        if not verdict["domain"] and domain:
            verdict["domain"] = domain
            verdict["why"].append(f"reads as {self.tax['domains'][domain]['label'].lower()} ({dscore:g})")
        if not verdict["kind"]:
            kind, phase = INTENT_RESULT.get(intent, (intent, ""))
            verdict["kind"] = kind
            if phase and not verdict["status"]:
                verdict["status"] = phase
            verdict["confidence"] += iscore
            verdict["why"].append(f"content scores as {INTENT_WORDS.get(intent, intent)} ({iscore:g})")

        floor = float(self.os.thresholds.get("min_classify_score", 2.0))
        if verdict["confidence"] < floor:
            verdict["flags"].append("needs-review")
            verdict["why"].append("not sure what this is — kept in Notes so it is easy to spot")
            if verdict["kind"] not in ("asset", "skill", "agent"):
                verdict["kind"] = "note"
                verdict["status"] = ""

        if not verdict["domain"]:
            verdict["domain"] = "unsorted"
        if not verdict["title"]:
            verdict["title"] = infer_title(body, path)
        if not verdict["tags"]:
            verdict["tags"] = self.keywords(title_src, body)
        verdict["confidence"] = round(verdict["confidence"] + dscore * 0.25, 2)
        verdict["scores"] = {"domain": dall, "intent": iall}
        verdict["summary"] = gist(body)
        verdict["captured"] = COMMENT_RE.sub("", body).strip()[:4_000]
        return verdict


# ---------------------------------------------------------------------------
# the scanner
# ---------------------------------------------------------------------------

#: `W.04_ship-the-rewrite` — a letter for what it is, then a count. Folders
#: written before the letters (`2.04_...`) still parse, so an older folder opens.
ID_RE = re.compile(r"^([A-Za-z]\.\d{2,4}|\d{1,2}\.\d{2,4})[_ -]+(.*)$")


def id_order(ident: str) -> tuple:
    """Sort key for a number like `W.04`.

    Sorting these as text puts N.100 between N.10 and N.11, because "1" sorts
    before "9". Compare the count as a number instead. The tag is compared as
    text — it is a letter now — with an older numeric one zero-padded so 2 still
    sorts before 10. Anything without a tag sorts last."""
    try:
        tag, item = ident.split(".", 1)
        return (0, f"{int(tag):03d}" if tag.isdigit() else tag, int(item))
    except (ValueError, AttributeError):
        return (1, "", 0)


class Scanner:
    """Walks the tree and returns every item the OS knows about."""

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.classifier = Classifier(os_)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def is_category(path: Path) -> bool:
        """Is this a grouping folder the walker should descend into?

        Never through a symlink. A link in Notes/ pointing at a folder that
        happens to hold a `.category` marker used to make the walker step
        straight out of the Zenith folder and treat somebody's real directory as
        its own — and then `os sort` *moved their files out of it*. A shortcut is
        one item, always: the thing it points at belongs to whoever put it there."""
        return path.is_dir() and not path.is_symlink() and (path / CATEGORY_MARKER).exists()

    @staticmethod
    def spine_of(path: Path) -> Path | None:
        # A shortcut is a pointer, not a document. Reading through one adopts a
        # file that belongs to another item — which then shows up as a duplicate
        # of itself, and would be rewritten in place by the next `os sort`.
        if path.is_symlink():
            return None
        # The card first, whether this is one file or a folder of them. A folder
        # of photos gets a card too, and only looking for one beside a *file*
        # meant that card was never read: the folder's number, subject and tags
        # sat in it, indexed by nothing and findable by no search.
        card = path.with_name(path.name + ".card.md")
        if card.exists():
            return card              # it was kept as-is; the card describes it
        if path.is_file():
            if path.suffix.lower() in TEXT_SUFFIXES:
                return path
            return None
        for candidate in ("README.md", "index.md", "SKILL.md", "AGENT.md"):
            if (path / candidate).exists():
                return path / candidate
        mds = sorted(p for p in path.glob("*.md") if not ignored(p))
        return mds[0] if mds else None

    def hydrate(self, item: Item) -> Item:
        m = ID_RE.match(item.path.name)
        if m:
            item.ident = m.group(1)
        item.spine = self.spine_of(item.path)
        meta: dict = {}
        item.blurb = ""
        if item.spine and item.spine.exists():
            meta, body = parse_frontmatter(read_text(item.spine, 60_000))
            item.words = len(body.split())
            if not item.summary:
                item.summary = gist(body)
        item.blurb = str(meta.get("description") or "").strip()
        item.managed = bool(str(meta.get("id") or "").strip())
        if not item.managed:
            # A shortcut has no spine on purpose — reading through one adopts a
            # file belonging to somebody else. But the card *beside* it is ours,
            # and without checking for it a filed link looks unfiled forever, so
            # every sort adopted and renumbered it again.
            item.managed = item.path.with_name(item.path.name + ".card.md").exists()
        item.ident = str(meta.get("id") or item.ident or "")
        declared_name = str(meta.get("name") or "").strip()
        item.title = str(meta.get("title")
                         or (titleize(declared_name) if declared_name else "")
                         or titleize(m.group(2) if m else item.path.stem))
        # One shelf now holds prose and files alike, so what a thing *is* comes
        # from its own header; the folder only says where it lives. Without this
        # a PDF filed in Notes would call itself a note in every listing.
        if item.kind in ("note", "project"):
            declared = str(meta.get("type") or "").strip().lower()
            resolved = TYPE_FROM_DISK.get(declared, declared)
            if resolved in ("project", "note", "asset"):
                item.kind = resolved
        item.status = normalize_status(meta.get("status"), item.kind, meta.get("type"))
        raw_domain = str(meta.get("domain") or "").strip()
        item.domain = (slugify(raw_domain, 24) if raw_domain else "") or "unsorted"
        raw_tags = meta.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [t for t in re.split(r"[,\s]+", raw_tags) if t]
        item.tags = [slugify(str(t), 24) for t in raw_tags][:12]
        item.created = str(meta.get("created") or "")
        item.updated = str(meta.get("updated") or "")
        if not item.created or not item.updated:
            try:
                st = item.path.stat()
                item.created = item.created or _dt.date.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d")
                item.updated = item.updated or _dt.date.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
            except OSError:
                pass
        item.fingerprint = digest(item.spine) if item.spine else digest(item.path)
        if meta.get("flags"):
            raw = meta["flags"]
            item.flags = list(raw) if isinstance(raw, list) else [str(raw)]
        return item

    # -- the walk -----------------------------------------------------------

    def _walk_bucket(self, bucket: str, node: Path, trail: list[str], out: list[Item], role: str) -> None:
        if not node.exists():
            return
        for child in sorted(node.iterdir()):
            if ignored(child):
                continue
            if self.is_category(child):
                self._walk_bucket(bucket, child, trail + [child.name], out, role)
                continue
            item = Item(child, bucket, role)
            item.trail = list(trail)
            out.append(self.hydrate(item))

    def scan(self) -> list[Item]:
        root = self.os.root
        items: list[Item] = []

        for bucket, spec in self.os.buckets().items():
            role = spec["role"]
            base = root / bucket
            if role == "archive":
                for year in sorted(p for p in base.glob("*") if p.is_dir()):
                    for origin in sorted(p for p in year.iterdir() if p.is_dir()):
                        for f in sorted(origin.iterdir()):
                            if ignored(f):
                                continue
                            it = Item(f, bucket, "archive")
                            it.trail = [year.name, origin.name]
                            items.append(self.hydrate(it))
                continue
            self._walk_bucket(bucket, base, [], items, role)

        items.extend(self.scan_toolkit())

        # repair counters if state.json was lost
        by_bucket: dict[str, set] = {}
        for it in items:
            if it.ident:
                by_bucket.setdefault(it.bucket, set()).add(it.ident)
        for bucket, idents in by_bucket.items():
            if bucket in self.os.buckets():
                self.os.reserve_id_at_least(bucket, idents)
        return items

    def scan_toolkit(self) -> list[Item]:
        root, out = self.os.root, []
        skills = root / ".claude" / "skills"
        agents = root / ".claude" / "agents"
        hooks = root / ".claude" / "hooks"
        for d in sorted(p for p in skills.glob("*") if p.is_dir()) if skills.exists() else []:
            if ignored(d) or not (d / "SKILL.md").exists():
                continue
            it = Item(d, TOOLKIT, "skill")
            it.trail = ["skills"]
            it = self.hydrate(it)
            it.title = it.title or titleize(d.name)
            out.append(it)
        for f in sorted(agents.glob("*.md")) if agents.exists() else []:
            if ignored(f):
                continue
            it = Item(f, TOOLKIT, "agent")
            it.trail = ["agents"]
            out.append(self.hydrate(it))
        for f in sorted(hooks.glob("*")) if hooks.exists() else []:
            if ignored(f) or f.is_dir():
                continue
            it = Item(f, TOOLKIT, "hook")
            it.trail = ["hooks"]
            it.title = titleize(f.stem)
            it.domain = "operations"
            it.status = "—"
            out.append(it)
        return out


# ---------------------------------------------------------------------------
# the sorter — adoption, ID assignment, self-balancing categories
# ---------------------------------------------------------------------------

#: Every kind of thing the person owns, and the folder role it lives under.
#: A file is a note with bytes instead of prose — it goes to the same shelf and
#: gets a numbered card beside it, so it is searchable like everything else.
ROLE_FOR_KIND = {
    "project": "project", "note": "note", "asset": "note",
}

#: The two live phases of work. Whether something will ever finish is a
#: prediction, and the person is asked for it at the moment they know least —
#: so the folder does not ask. It asks what the thing needs *now*: a next
#: action (pushing), or a standard held level (holding). The same item moves
#: between the two, repeatedly, which is exactly why this is a status and not
#: a folder.
PUSHING, HOLDING = "pushing", "holding"

#: Words that used to mean these, in files written before the merge, and the
#: ones people type. Anything not here is left alone and shown as written.
STATUS_ALIASES = {
    "active": PUSHING, "in-progress": PUSHING, "in progress": PUSHING,
    "open": PUSHING, "doing": PUSHING, "started": PUSHING,
    "ongoing": HOLDING, "maintained": HOLDING, "area": HOLDING,
    "shipped": "done", "complete": "done", "completed": "done", "closed": "done",
}

#: Live work, and work that has left. `""` counts as live: an item somebody
#: hand-wrote without a status is still theirs.
LIVE_STATUSES = (PUSHING, HOLDING, "")
CLOSED_STATUSES = ("done", "archived", "dropped", "parked")


#: `type:` words from before Projects and Ongoing merged.
LEGACY_HOLDING_TYPES = {"area", "ongoing"}


def normalize_status(raw: str, kind: str, declared_type: str = "") -> str:
    """One vocabulary for work, whatever the file happens to say.

    A file written before the merge says `type: ongoing` and `status: active`,
    where "active" meant *being kept up* — the opposite of what it means now.
    Read those as held, or every ongoing thing somebody already had would come
    back as work on the go, and immediately start being nagged for going quiet."""
    word = str(raw or "").strip().lower()
    if kind != "project":
        return str(raw or "").strip() or "—"
    if str(declared_type).strip().lower() in LEGACY_HOLDING_TYPES \
            and word in ("", "active", "in-progress", "open"):
        return HOLDING
    return STATUS_ALIASES.get(word, word) or PUSHING


#: What `type:` says in a file the person might open. The engine still thinks
#: in "project" and "asset"; the folders say Work and Files, so the files do too.
TYPE_ON_DISK = {"project": "work", "asset": "file"}
#: Words older folders wrote into `type:`. `log` and `journal` had a folder of
#: their own once; they read as notes now, which is what they always were.
TYPE_FROM_DISK = {"work": "project", "ongoing": "project", "area": "project",
                  "file": "asset", "reference": "note",
                  "log": "note", "journal": "note"}


class Sorter:
    """Turns a messy folder into a sorted one, reversibly.

    Three passes:
      1. adopt   — anything the OS never filed is classified, named, stamped
                   and moved: staged captures from `os save`, and anything
                   dropped straight into a bucket by hand
      2. identify— any managed item without an ID gets a permanent one
      3. balance — categories appear when a bucket earns them and collapse
                   when it no longer needs them (with hysteresis, so a bucket
                   hovering at the threshold does not thrash)
    """

    def __init__(self, os_: "Zenith", dry: bool = False):
        self.os = os_
        self.dry = dry
        self.scanner = Scanner(os_)
        self.classifier = self.scanner.classifier
        self.creator = Creator(os_)
        self.moves: list[tuple[str, str, str]] = []   # (what, from, to)
        self.notes: list[str] = []
        self.skipped: list[tuple[str, str]] = []      # (path, why it could not be filed)

    # -- pass 1: staged captures, and anything dropped in by hand ------------

    def _take(self, src: Path) -> Path | None:
        """Classify one unfiled thing and put it where it belongs."""
        verdict = self.classifier.classify(src)
        try:
            dest = self.place(src, verdict)
        except OSError as exc:
            # One thing going missing is not a reason to abandon the other
            # forty-nine — but it is never swallowed: reporting "nothing
            # waiting" while something is still waiting is a lie.
            self.skipped.append((self.os.rel(src), str(exc)))
            return None
        if dest is not None:
            why = verdict["why"][0] if verdict["why"] else "classified"
            self.moves.append((verdict["kind"], self.os.rel(src), self.os.rel(dest)))
            self.notes.append(f"{verdict['kind']}: {why}")
        return dest

    def file_staged(self) -> int:
        """File anything left in the staging area under .os/cache/.

        `os save` stages, classifies and moves in one breath, so this is
        normally empty. It exists for the run that died between the two."""
        stage = self.os.dot / "cache" / STAGING
        if not stage.exists():
            return 0
        filed = 0
        for child in sorted(stage.iterdir()):
            if ignored(child):
                continue
            if self._take(child) is not None:
                filed += 1
        return filed

    @staticmethod
    def unmanaged(it: "Item") -> bool:
        """Is this something the OS has never taken charge of?

        Ours iff its own header carries an `id:` — a number in the filename is
        not enough, because anybody can type one. A bare PDF with no card beside
        it has no header at all, and so is nobody's."""
        return it.kind in ("project", "note", "asset") and not it.managed

    def adopt(self, items: list["Item"]) -> int:
        """Take charge of anything sitting in a bucket that the OS never filed.

        With no drop folder, the natural move is to drag a PDF straight into
        Notes//. Left alone that is a file with no card — no subject, no
        number, and nothing for a search to match on. So the sorter adopts it
        where it lies, rather than asking the person to have put it somewhere
        special first. Where it *ends up* is still the folder's call: a PDF
        dropped into Work/ is a thing you look up later wherever you put it."""
        taken = 0
        for it in items:
            if it.bucket not in self.os.buckets() or not self.unmanaged(it):
                continue
            if self.dry:
                verdict = self.classifier.classify(it.path)
                dest = self.place(it.path, verdict)
                if dest is not None:
                    self.moves.append((verdict["kind"], self.os.rel(it.path),
                                       self.os.rel(dest)))
                    taken += 1
                continue
            if self._take(it.path) is not None:
                taken += 1
        return taken

    def file_one(self, src: Path) -> tuple[Path | None, dict]:
        """Classify and place exactly one thing.

        `os save` uses this so nothing is ever left staged waiting for the
        person to remember a second command."""
        verdict = self.classifier.classify(src)
        dest = self.place(src, verdict)
        if dest is not None:
            self.moves.append((verdict["kind"], self.os.rel(src), self.os.rel(dest)))
        return dest, verdict

    def place(self, src: Path, verdict: dict) -> Path | None:
        kind = verdict["kind"]
        slug = slugify(verdict["title"] or src.stem, 44)
        root = self.os.root

        if kind == "skill":
            dest_dir = root / ".claude" / "skills" / slug
            if self.dry:
                return dest_dir
            if src.is_dir():
                moved = self.os.move(src, dest_dir)
            else:
                self.os.make_dir(dest_dir)
                moved = self.os.move(src, dest_dir / "SKILL.md")
                moved = dest_dir
            self._ensure_skill(moved, verdict)
            return moved

        if kind == "agent":
            dest = root / ".claude" / "agents" / f"{slug}.md"
            if self.dry:
                return dest
            if src.is_dir():
                inner = self.scanner.spine_of(src)
                if inner is None:
                    kind = verdict["kind"] = "asset"
                else:
                    moved = self.os.move(inner, dest)
                    self._ensure_agent(moved, verdict)
                    if not any(p for p in src.iterdir() if not ignored(p)):
                        shutil.rmtree(src, ignore_errors=True)
                    return moved
            else:
                moved = self.os.move(src, dest)
                self._ensure_agent(moved, verdict)
                return moved

        bucket = self.os.bucket_for_role(ROLE_FOR_KIND.get(kind, "note"))
        ident = f"{self.os.buckets()[bucket]['code']}.??" if self.dry else self.os.next_id(bucket)
        base = root / bucket

        if kind == "project":
            dest_dir = base / f"{ident}_{slug}"
            if self.dry:
                return dest_dir
            if src.is_dir():
                moved = self.os.move(src, dest_dir)
            else:
                self.os.make_dir(dest_dir)
                inner = dest_dir / ("README.md" if src.suffix.lower() in TEXT_SUFFIXES else src.name)
                self.os.move(src, inner)
                moved = dest_dir
            self._ensure_spine(moved, ident, verdict, kind)
            return moved

        if kind == "asset":
            suffix = src.suffix if src.is_file() else ""
            dest = base / f"{ident}_{slug}{suffix}"
            if self.dry:
                return dest
            moved = self.os.move(src, dest)
            self._sidecar(moved, ident, verdict)
            return moved

        # note
        if src.is_dir():
            dest = base / f"{ident}_{slug}"
            if self.dry:
                return dest
            moved = self.os.move(src, dest)
            self._ensure_spine(moved, ident, verdict, "note")
            return moved
        dest = base / f"{ident}_{slug}.md"
        if self.dry:
            return dest
        if src.suffix.lower() not in TEXT_SUFFIXES:
            dest = base / f"{ident}_{slug}{src.suffix}"
        moved = self.os.move(src, dest)
        if moved.suffix.lower() in TEXT_SUFFIXES:
            stamp_file(moved, {"id": ident, "title": verdict["title"], "type": "note",
                               "status": "—", "domain": verdict["domain"],
                               "tags": verdict["tags"], "created": today(),
                               "summary": verdict.get("summary", "")})
            self._finish_header(moved, verdict)
        else:
            # It reads as prose but we cannot write a header into it — a file
            # with no extension at all, most often. Give it a card, the same as
            # any other file. Without one it carries no id anywhere, so every
            # later sort saw it as unfiled and numbered it again: one run left
            # `4.10_4.04_no-extension` behind, and burnt a number doing it.
            self._sidecar(moved, ident, verdict)
        return moved

    @staticmethod
    def _finish_header(spine: Path, verdict: dict) -> None:
        """Tidy up a freshly filed item's header.

        `saved:` is a capture artefact and has no business surviving. A
        `needs-review` flag is the opposite — it is the only record that the
        sorter was guessing, and `os tidy` is where somebody goes to find it."""
        meta, body = parse_frontmatter(read_text(spine))
        if not meta:
            return
        changed = bool(meta.pop("saved", None))
        if verdict.get("flags"):
            meta["flags"] = verdict["flags"]
            changed = True
        if changed:
            write_text(spine, compose(meta, body))

    # -- writing the spine --------------------------------------------------

    def _ensure_spine(self, folder: Path, ident: str, verdict: dict, kind: str) -> None:
        """Give a project or an ongoing thing the same shape `os new` gives it.

        Most of these are born from `os save`, where the captured text simply
        becomes the README. Left alone that produces a project with no next
        action, no decisions and no log — nothing the rest of the system, or an
        AI following AGENTS.md, can actually work with. So: keep every word the
        person wrote, and build the structure around it."""
        spine = self.scanner.spine_of(folder) or (folder / "README.md")
        already_there = spine.exists()
        existing, has_shape = "", False
        if already_there:
            _, existing = parse_frontmatter(read_text(spine))
            has_shape = bool(re.search(r"^##\s+", existing, re.M))
            if not has_shape:
                existing = re.sub(r"^#\s+.*$", "", existing, count=1, flags=re.M).strip()
            elif not verdict.get("title_from_meta"):
                heading = re.search(r"^#\s+(.+?)\s*$", existing, re.M)
                if heading:
                    verdict["title"] = heading.group(1).strip()[:90]

        # Something the person already shaped is theirs. Stamp the header so it
        # is findable, and touch nothing below it.
        if not has_shape:
            body = existing or str(verdict.get("captured")
                                   or verdict.get("summary") or "").strip()
            _, blueprint = parse_frontmatter(
                self.creator.render(kind, verdict["title"], ident,
                                    verdict["domain"], verdict["tags"],
                                    verdict.get("status", "")))
            blueprint = blueprint.lstrip("\n")
            if body:
                head, _, rest = blueprint.partition("\n")
                blueprint = head + "\n\n" + body.strip() + "\n" + rest
            write_text(spine, blueprint)
            # Only a file this run brought into existence may be removed by undo.
            # Recording a create for one that was moved here makes undo delete it
            # before it can be moved back.
            if not already_there:
                self.os.created(spine)
        if kind == "project":
            # `make_dir`, not a bare mkdir: undo replays steps backwards and can
            # only remove a directory that is empty, so the scaffolding has to be
            # recorded too or the item folder is never empty enough to go.
            for sub in ("notes", "assets"):
                self.os.make_dir(folder / sub)
        stamp_file(spine, {
            "id": ident, "title": verdict["title"], "type": TYPE_ON_DISK.get(kind, kind),
            "status": verdict.get("status") or (PUSHING if kind == "project" else "—"),
            "domain": verdict["domain"], "tags": verdict["tags"],
            "created": today(), "summary": verdict.get("summary", ""),
        })
        self._finish_header(spine, verdict)

    def _sidecar(self, asset: Path, ident: str, verdict: dict) -> None:
        card = asset.with_name(asset.name + ".card.md")
        if card.exists():
            return
        write_text(card, compose({
            "id": ident, "title": verdict["title"], "type": "file",
            "status": "—", "domain": verdict["domain"], "tags": verdict["tags"],
            "created": today(), "source": asset.name,
        }, f"# {verdict['title']}\n\nAsset card for `{asset.name}`.\n\n{verdict.get('summary','')}\n"))
        self.os.created(card)

    def _ensure_skill(self, folder: Path, verdict: dict) -> None:
        skill = folder / "SKILL.md"
        if not skill.exists():
            write_text(skill, f"---\nname: {folder.name}\ndescription: {verdict['title']}\n---\n\n{verdict.get('summary','')}\n")
            self.os.created(skill)
            return
        meta, body = parse_frontmatter(read_text(skill))
        meta.setdefault("name", folder.name)
        meta.setdefault("description", verdict.get("summary") or verdict["title"])
        write_text(skill, compose(meta, body))

    def _ensure_agent(self, path: Path, verdict: dict) -> None:
        meta, body = parse_frontmatter(read_text(path))
        meta.setdefault("name", slugify(verdict["title"]))
        meta.setdefault("description", verdict.get("summary") or verdict["title"])
        write_text(path, compose(meta, body))

    # -- pass 2: identify ---------------------------------------------------

    def identify(self, items: list[Item]) -> int:
        fixed = 0
        # A number is only permanent if it is unique. Two items can end up
        # sharing one by being copied, or restored from a backup, or edited by
        # hand — so the older keeps it and the newer is given the next free one.
        claimed: dict = {}
        clashing: set = set()
        for it in sorted(items, key=lambda i: (i.created or "9999", str(i.path))):
            if it.kind not in ("project", "note", "asset") or not it.ident:
                continue
            if it.ident in claimed:
                clashing.add(str(it.path))
            else:
                claimed[it.ident] = it

        for it in items:
            if it.kind not in ("project", "note", "asset"):
                continue
            if str(it.path) in clashing:
                it.ident = ""            # give this one a fresh number below
            elif it.ident and ID_RE.match(it.path.name):
                continue
            # A dry run shows what a number *would* look like. Calling next_id
            # here spent one for real, so `sort --dry-run` quietly pushed every
            # later item's number up by the size of the preview.
            if it.ident:
                ident = it.ident
            elif self.dry:
                ident = f"{self.os.buckets()[it.bucket].get('code', '?')}.??"
            else:
                ident = self.os.next_id(it.bucket)
            slug = slugify(it.title or it.path.stem, 44)
            suffix = it.path.suffix if it.path.is_file() else ""
            target = it.path.with_name(f"{ident}_{slug}{suffix}")
            if target != it.path:
                if self.dry:
                    self.moves.append(("id", self.os.rel(it.path), self.os.rel(target)))
                    fixed += 1
                    continue
                new = self.os.move_item(it.path, target)
                self.moves.append(("id", self.os.rel(it.path), self.os.rel(new)))
                it.path = new
                # the spine moved with it; stamping the old path writes nowhere
                it.spine = self.scanner.spine_of(new)
            if not self.dry and it.spine and it.spine.exists():
                stamp_file(it.spine, {"id": ident, "title": it.title,
                                      "type": TYPE_ON_DISK.get(it.kind, it.kind),
                                      "domain": it.domain, "tags": it.tags},
                           force=("id",))
            it.ident = ident
            fixed += 1
        return fixed

    # -- pass 3: balance ----------------------------------------------------

    def _cluster_key(self, group: list[Item], depth_cap: int) -> dict[str, str]:
        """Assign each item in `group` a second-level folder, deterministically."""
        tally: dict[str, int] = {}
        for it in group:
            for t in it.tags[:5]:
                if t and t != "unsorted":
                    tally[t] = tally.get(t, 0) + 1
        floor = max(2, len(group) // 8)
        ranked = [t for t, n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])) if n >= floor]
        ranked = ranked[: min(9, depth_cap)]
        mapping: dict[str, str] = {}
        for it in group:
            for t in ranked:
                if t in it.tags[:5]:
                    mapping[str(it.path)] = titleize(t)
                    break
        return mapping

    def target_trails(self, items: list[Item]) -> dict[str, list[str]]:
        """The trail every item *should* have, given the current population."""
        split = int(self.os.thresholds.get("category_split", 12))
        collapse = max(2, int(split * 0.6))
        max_cats = int(self.os.thresholds.get("max_categories_per_bucket", 9))
        labels = {k: v["label"] for k, v in self.os.taxonomy["domains"].items()}
        plan: dict[str, list[str]] = {}

        for bucket, spec in self.os.buckets().items():
            if not spec.get("categorize"):
                continue
            pool = [it for it in items if it.bucket == bucket]
            if not pool:
                continue
            currently_split = any(it.trail for it in pool)
            should_split = len(pool) > split if not currently_split else len(pool) >= collapse
            if not should_split:
                for it in pool:
                    plan[str(it.path)] = []
                continue

            groups: dict[str, list[Item]] = {}
            for it in pool:
                label = labels.get(it.domain, titleize(it.domain or "Unsorted"))
                groups.setdefault(label, []).append(it)

            # too many thin groups? keep only the biggest, pool the rest
            ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            keep = {name for name, g in ordered[:max_cats] if len(g) >= 2}
            for name, group in groups.items():
                category = name if name in keep else "General"
                for it in group:
                    plan[str(it.path)] = [category]

            # second level, only where a single category is itself crowded
            regrouped: dict[str, list[Item]] = {}
            for it in pool:
                regrouped.setdefault(plan[str(it.path)][0], []).append(it)
            for cat, group in regrouped.items():
                nested_now = any(len(it.trail) > 1 for it in group)
                crowded = len(group) > split if not nested_now else len(group) >= collapse
                if not crowded:
                    continue
                mapping = self._cluster_key(group, max_cats)
                for it in group:
                    sub = mapping.get(str(it.path))
                    if sub:
                        plan[str(it.path)] = [cat, sub]
        return plan

    def balance(self, items: list[Item]) -> int:
        plan = self.target_trails(items)
        moved = 0
        for it in items:
            want = plan.get(str(it.path))
            if want is None or want == it.trail:
                continue
            dest_dir = self.os.root / it.bucket
            for part in want:
                dest_dir = dest_dir / part
            target = dest_dir / it.path.name
            self.moves.append(("sort", self.os.rel(it.path), self.os.rel(target)))
            moved += 1
            if self.dry:
                continue
            self._make_category(dest_dir, want)
            new = self.os.move_item(it.path, target)
            it.path, it.trail = new, list(want)
        if not self.dry:
            self.prune_categories()
        return moved

    def _make_category(self, path: Path, trail: list[str]) -> None:
        """Create and mark *every* level of the trail.

        Marking only the deepest folder leaves the levels above it looking like
        ordinary items, which hides everything beneath them from the index."""
        base = path
        for _ in trail:
            base = base.parent
        for depth in range(1, len(trail) + 1):
            node = base
            for part in trail[:depth]:
                node = node / part
            self.os.make_dir(node)
            marker = node / CATEGORY_MARKER
            if marker.exists():
                continue
            write_text(marker, json.dumps({
                "name": trail[depth - 1],
                "trail": trail[:depth],
                "auto": True,
                "created": today(),
                "engine": ENGINE_VERSION,
            }, indent=2) + "\n")

    def prune_categories(self) -> int:
        removed = 0
        for bucket, spec in self.os.buckets().items():
            if not spec.get("categorize"):
                continue
            base = self.os.root / bucket
            if not base.exists():
                continue
            for path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if not Scanner.is_category(path):
                    continue
                leftovers = [p for p in path.iterdir() if not ignored(p) and p.name != CATEGORY_MARKER]
                if leftovers:
                    continue
                shutil.rmtree(path, ignore_errors=True)
                self.os.record("rmdir", self.os.rel(path))
                removed += 1
        return removed

    # -- the whole run ------------------------------------------------------

    def run(self) -> dict:
        # Learn what IDs are already on disk before handing out new ones, so a
        # lost or hand-edited state.json can never cause a collision.
        items = self.scanner.scan()
        filed = self.file_staged() + self.adopt(items)
        items = self.scanner.scan()
        identified = self.identify(items)
        if not self.dry:
            items = self.scanner.scan()
        balanced = self.balance(items)
        pruned = 0 if self.dry else self.prune_categories()
        if not self.dry:
            self.os.save_state()
        return {"filed": filed, "identified": identified, "balanced": balanced,
                "pruned": pruned, "moves": self.moves, "notes": self.notes,
                "skipped": [{"path": path, "why": why} for path, why in self.skipped]}


# ---------------------------------------------------------------------------
# the indexer — registry, human maps, dashboard
# ---------------------------------------------------------------------------

GENERATED = "<!-- written by Zenith. Don't edit by hand — it gets overwritten. -->"

HOOK_BLURBS = {
    "session-start.sh": "Tells your AI where things stand, before you say anything.",
    "mark-dirty.sh": "Notices when a file changed.",
    "settle.sh": "Re-reads the folder when you stop typing, so search stays current.",
}


class Indexer:
    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.scanner = Scanner(os_)

    def build(self) -> dict:
        items = self.scanner.scan()
        self.os.save_state()
        registry = {
            "engine": ENGINE_VERSION,
            "name": self.os.config.get("name", "Zenith"),
            "root": str(self.os.root),
            "generated": now_iso(),
            "counts": self._counts(items),
            "buckets": {},
            "items": [it.as_dict(self.os.root) for it in items],
        }
        for bucket, spec in self.os.buckets().items():
            pool = [it for it in items if it.bucket == bucket]
            cats: dict[str, int] = {}
            for it in pool:
                key = " / ".join(it.trail) if it.trail else "—"
                cats[key] = cats.get(key, 0) + 1
            registry["buckets"][bucket] = {
                "label": spec["label"], "role": spec["role"], "blurb": spec["blurb"],
                "count": len(pool), "categories": cats,
            }
        write_text(self.os.dot / "registry.json", json.dumps(registry, indent=2) + "\n")
        self.write_index(items, registry)
        self.write_catalog(items)
        return registry

    @staticmethod
    def _counts(items: list[Item]) -> dict:
        counts: dict[str, int] = {}
        for it in items:
            counts[it.kind] = counts.get(it.kind, 0) + 1
        counts["total"] = len(items)
        return counts

    # -- INDEX.md -----------------------------------------------------------

    def write_index(self, items: list[Item], registry: dict) -> None:
        cfg = self.os.config
        yours = len([i for i in items if i.bucket != TOOLKIT])
        lines = [
            GENERATED,
            f"# {cfg.get('name', 'Zenith')}",
            "",
            "Everything in this folder, in one list. Rebuilt automatically.",
            "",
            f"{yours} thing{'' if yours == 1 else 's'} · "
            f"last updated {registry['generated'][:16].replace('T', ' ')}",
            "",
            "| Folder | What's in it | How many |",
            "| --- | --- | --- |",
        ]
        for bucket, spec in self.os.buckets().items():
            lines.append(f"| **{bucket}** | {spec['blurb']} | {registry['buckets'][bucket]['count']} |")

        for bucket, spec in self.os.buckets().items():
            pool = [it for it in items if it.bucket == bucket]
            if not pool:
                continue
            lines += ["", f"## {bucket}", "", f"_{spec['blurb']}_", ""]
            groups: dict[str, list[Item]] = {}
            for it in pool:
                groups.setdefault(" / ".join(it.trail) if it.trail else "", []).append(it)
            for cat in sorted(groups, key=lambda c: (c == "", c)):
                group = sorted(groups[cat], key=lambda i: (id_order(i.ident), i.title.lower()))
                if cat:
                    lines += [f"### {cat}", ""]
                lines += ["| Number | Name | State | Last touched |",
                          "| --- | --- | --- | --- |"]
                for it in group:
                    link = (relative_to_root(it.path, self.os.root)
                            if it.path.exists() else "").replace(" ", "%20")
                    lines.append(f"| `{it.ident or '—'}` | [{it.title}]({link}) | "
                                 f"{it.status or '—'} | {it.updated or '—'} |")
                lines.append("")

        tools = [it for it in items if it.bucket == TOOLKIT]
        if tools:
            skills = len([i for i in tools if i.kind == "skill"])
            agents = len([i for i in tools if i.kind == "agent"])
            lines += ["", "## Extras", "",
                      f"_{skills} skills and {agents} helpers live in `.claude/` — "
                      "see [CATALOG.md](.claude/CATALOG.md). Everything works without "
                      "them too, through `./os`._", ""]
        write_text(self.os.root / "INDEX.md", "\n".join(lines).rstrip() + "\n")

    # -- toolkit catalog ----------------------------------------------------

    def write_catalog(self, items: list[Item]) -> None:
        skills = [i for i in items if i.kind == "skill"]
        agents = [i for i in items if i.kind == "agent"]
        hooks = [i for i in items if i.kind == "hook"]
        lines = [
            GENERATED, "# What this folder can do", "",
            "Extras for Claude Code. Type a `/name` **in the chat** (not the terminal) "
            "to run a skill. Helpers get sent off on their own when a job suits them. "
            "None of this is required — `./os help` is the plain version, and it works "
            "in any terminal with or without an AI.", "",
        ]

        def block(title: str, pool: list[Item], mark: str, lead: str) -> list[str]:
            if not pool:
                return []
            out = [f"## {title}", "", lead, "", "| | What it does |", "| --- | --- |"]
            for it in sorted(pool, key=lambda i: i.path.name.lower()):
                name = it.path.stem if it.path.is_file() else it.path.name
                desc = (it.blurb or it.summary or it.title or "")
                # A description reads "<what it does>. Use when <trigger words>."
                # The trigger half exists to make the model fire; a person reading
                # this page only wants the first half.
                desc = re.split(r"\.\s+(?:Use|Delegate)\b", desc, maxsplit=1)[0]
                out.append(f"| `{mark}{name}` | "
                           f"{_shorten(desc.replace('|', '/'), 128, '…')} |")
            return out + [""]

        lines += block("Skills", skills, "/", "Type these in the chat, or just ask for them in your own words.")
        lines += block("Helpers", agents, "@", "Picked automatically when a job is big enough to deserve its own context.")
        if hooks:
            lines += ["## Automatic", "", "These run on their own. You never call them.",
                      "", "| File | |", "| --- | --- |"]
            for it in sorted(hooks, key=lambda i: i.path.name):
                lines.append(f"| `{it.path.name}` | {HOOK_BLURBS.get(it.path.name, '—')} |")
            lines.append("")
        write_text(self.os.root / ".claude" / "CATALOG.md", "\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# the doctor — health, and safe repair
# ---------------------------------------------------------------------------

RESERVED_COMMANDS = {
    "help", "clear", "compact", "config", "cost", "doctor", "exit", "init",
    "login", "logout", "memory", "model", "permissions", "resume", "review",
    "status", "vim", "code-review", "batch", "debug", "loop", "claude-api",
    "run", "verify", "skill-doctor", "agents", "skills", "hooks", "context",
    "rewind", "usage", "feedback", "add-dir", "mcp", "plugin", "output-style",
}

LEVELS = {"error": 3, "warn": 2, "hint": 1}


class Doctor:
    #: A folder with 400 items will trip the same hint hundreds of times. Report
    #: the first few of each kind in full and roll the rest into one line, so the
    #: report stays readable at any scale.
    PER_CODE_CAP = 20

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.scanner = Scanner(os_)
        self.issues: list[dict] = []
        self.tally: dict[str, int] = {}

    def flag(self, level: str, code: str, message: str, path: str = "", fix: str = "") -> None:
        self.tally[code] = self.tally.get(code, 0) + 1
        if self.tally[code] > self.PER_CODE_CAP:
            return
        self.issues.append({"level": level, "code": code, "message": message,
                            "path": path, "fix": fix})

    def _rollup(self) -> None:
        for code, total in sorted(self.tally.items()):
            if total <= self.PER_CODE_CAP:
                continue
            sample = next(i for i in self.issues if i["code"] == code)
            self.issues.append({
                "level": sample["level"], "code": code,
                "message": f"...and {total - self.PER_CODE_CAP} more '{code}' findings",
                "path": "", "fix": sample.get("fix", ""),
            })

    # -- the checks ---------------------------------------------------------

    def run(self, items: list[Item] | None = None, fix: bool = False) -> dict:
        self.issues = []
        self.tally = {}
        root = self.os.root
        repaired: list[str] = []

        # 1. skeleton
        for bucket in self.os.buckets():
            path = root / bucket
            if not path.exists():
                if fix:
                    path.mkdir(parents=True, exist_ok=True)
                    repaired.append(f"created {bucket}/")
                else:
                    self.flag("error", "missing-folder", f"the {bucket}/ folder is missing", bucket, "./os check --fix")

        want = root / ".claude"
        if not want.exists():
            if fix:
                for sub_ in ("skills", "agents", "hooks"):
                    (want / sub_).mkdir(parents=True, exist_ok=True)
                repaired.append("created .claude/")
            else:
                self.flag("hint", "no-claude-dir",
                          ".claude/ is missing — Claude Code's extras will not load "
                          "(everything else still works)", ".claude", "./os check --fix")

        # 2. the house rules every AI reads
        rules = root / "AGENTS.md"
        for claude_md, pointer in ((root / "CLAUDE.md", "@AGENTS.md"),
                                   (root / ".claude" / "CLAUDE.md", "@../AGENTS.md")):
            if claude_md.exists() and "AGENTS.md" not in read_text(claude_md, 4_000):
                self.flag("warn", "rules-drift",
                          f"{self.os.rel(claude_md)} no longer points at AGENTS.md, so "
                          "Claude Code and every other AI are reading different rules",
                          self.os.rel(claude_md), f"put `{pointer}` on its first line")
        if not rules.exists():
            self.flag("error", "no-agents-md",
                      "AGENTS.md is missing — an AI opening this folder won't know the rules",
                      "AGENTS.md")
        else:
            n = len(read_text(rules).split("\n"))
            cap = int(self.os.thresholds.get("rules_max_lines", 160))
            if n > cap:
                self.flag("warn", "rules-bloat",
                          f"AGENTS.md is {n} lines (cap {cap}) — it is read on every single turn, "
                          "so move long procedures into a skill",
                          "AGENTS.md", "move sections into .claude/skills/")

        settings = root / ".claude" / "settings.json"
        if settings.exists():
            try:
                json.loads(settings.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                self.flag("error", "bad-settings", f".claude/settings.json is not valid JSON: {exc}", ".claude/settings.json")

        # 2b. words that never reached a folder. Saving files in one breath, so
        # anything here is a run that died in between. Left unsaid it is
        # invisible to every command a person would think to run — `os` says
        # "nothing started yet" and `check` says "all good" over the top of it.
        for path in staged_captures(self.os.dot):
            try:
                age = int((time.time() - path.stat().st_mtime) // 86_400)
            except OSError:
                age = 0
            _, body = parse_frontmatter(read_text(path, 2_000))
            words = next((trunc(ln.strip(), 44) for ln in body.split("\n") if ln.strip()),
                         path.name)
            stale = age >= STALE_STAGE_DAYS
            when = "today" if age < 1 else f"{age} days ago"
            self.flag("error" if stale else "warn", "unfiled-capture",
                      f"'{words}' was written down {when} and never filed",
                      self.os.rel(path), "./os sort")

        if items is None:
            items = self.scanner.scan()

        # 3. identity and metadata
        seen: dict[str, Item] = {}
        for it in items:
            if it.kind in ("project", "note", "asset"):
                if not it.ident:
                    self.flag("warn", "no-id", f"'{it.title}' has no number yet", self.os.rel(it.path), "./os sort")
                elif it.ident in seen:
                    self.flag("error", "duplicate-id",
                              f"number {it.ident} is used twice ('{it.title}' and '{seen[it.ident].title}')",
                              self.os.rel(it.path), "./os sort")
                else:
                    seen[it.ident] = it
                if it.spine and (not it.domain or it.domain == "unsorted"):
                    self.flag("hint", "no-domain", f"'{it.title}' has no subject set, so it can't be grouped",
                              self.os.rel(it.path), "add a `domain:` line at the top of the file")
            if it.spine and it.spine.exists() and it.words == 0 and it.kind != "asset":
                self.flag("hint", "empty", f"'{it.title}' is empty", self.os.rel(it.path))

        # 4. duplicates
        by_print: dict[str, list[Item]] = {}
        for it in items:
            if it.fingerprint and it.kind != "asset":
                by_print.setdefault(it.fingerprint, []).append(it)
        for group in by_print.values():
            if len(group) > 1:
                names = ", ".join(self.os.rel(g.path) for g in group[:3])
                self.flag("warn", "duplicate-content", f"the same thing appears in {len(group)} places: {names}",
                          self.os.rel(group[0].path), "./os tidy")
        # Near-duplicate titles, reported as groups rather than pairs. Fifty
        # copies of one title is 1,225 pairs and one useful sentence, so cluster
        # them: sort (which lands similar titles next to each other), then walk.
        # difflib's own cheap upper bounds skip pairs that cannot reach the bar.
        # Sort on the title alone. Without an explicit key, two items sharing a
        # title make Python fall through to comparing Item objects, which it
        # cannot do — and two items sharing a title is the exact case this
        # check exists to find.
        titles = sorted(((it.title.lower(), it) for it in items
                         if it.title and it.kind in ("note", "project")),
                        key=lambda pair: (pair[0], str(pair[1].path)))
        threshold = float(self.os.thresholds.get("duplicate_similarity", 0.86))
        matcher = difflib.SequenceMatcher(autojunk=False)

        def alike(a: str, b: str) -> bool:
            # ratio() <= 2*min/(la+lb), so lengths this far apart cannot match
            if 2 * min(len(a), len(b)) < threshold * (len(a) + len(b)):
                return False
            matcher.set_seq2(a)
            matcher.set_seq1(b)
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                return False
            return matcher.ratio() >= threshold

        i = 0
        while i < len(titles):
            head_title, head_item = titles[i]
            group = [head_item]
            j = i + 1
            while j < len(titles) and alike(head_title, titles[j][0]):
                group.append(titles[j][1])
                j += 1
            if len(group) > 1 and len({g.fingerprint for g in group}) > 1:
                names = ", ".join(f"'{g.title}'" for g in group[:3])
                more = f" and {len(group) - 3} more" if len(group) > 3 else ""
                self.flag("hint", "near-duplicate",
                          f"{len(group)} things look like the same thing: {names}{more}",
                          self.os.rel(group[1].path), "./os tidy")
            i = j if len(group) > 1 else i + 1

        # 5. toolkit validity
        for it in items:
            if it.kind == "skill":
                skill_md = it.path / "SKILL.md"
                meta, body = parse_frontmatter(read_text(skill_md))
                if not meta.get("description"):
                    self.flag("warn", "skill-no-description",
                              f"the skill '{it.path.name}' has no description, so it will almost never run",
                              self.os.rel(skill_md), "add a `description:` line saying when to use it")
                if it.path.name.lower() in RESERVED_COMMANDS:
                    self.flag("error", "skill-name-clash",
                              f"the skill '{it.path.name}' has the same name as a built-in command",
                              self.os.rel(it.path), "rename the folder, e.g. os-" + it.path.name)
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", it.path.name):
                    self.flag("warn", "skill-name-shape",
                              f"skill folder '{it.path.name}' should be lowercase-with-hyphens",
                              self.os.rel(it.path))
                cap = int(self.os.thresholds.get("skill_body_max_lines", 120))
                if len(body.split("\n")) > cap:
                    self.flag("hint", "skill-long",
                              f"skill '{it.path.name}' is {len(body.splitlines())} lines — split reference material into a second file",
                              self.os.rel(skill_md))
            if it.kind == "agent":
                meta, _ = parse_frontmatter(read_text(it.path))
                if not meta.get("name"):
                    self.flag("warn", "agent-no-name", f"the helper '{it.path.name}' has no `name:` line", self.os.rel(it.path))
                elif slugify(str(meta["name"])) != it.path.stem:
                    self.flag("hint", "agent-name-mismatch",
                              f"agent file '{it.path.name}' declares name '{meta['name']}'",
                              self.os.rel(it.path))
                if not meta.get("description"):
                    self.flag("warn", "agent-no-description",
                              f"the helper '{it.path.name}' has no description, so it will never be used",
                              self.os.rel(it.path))
            if it.kind == "hook" and it.path.suffix in (".sh", ".zsh", ".bash", ".py"):
                if not os.access(it.path, os.X_OK):
                    if fix:
                        it.path.chmod(it.path.stat().st_mode | 0o755)
                        repaired.append(f"made {it.path.name} runnable again")
                    else:
                        self.flag("warn", "hook-not-executable",
                                  f"'{it.path.name}' can't run — it needs to be executable",
                                  self.os.rel(it.path), "./os check --fix")

        launcher = root / "os"
        if launcher.is_file() and not os.access(launcher, os.X_OK):
            if fix:
                launcher.chmod(launcher.stat().st_mode | 0o755)
                repaired.append("made ./os runnable again")
            else:
                self.flag("error", "os-not-executable",
                          "./os is not executable, so typing ./os just says "
                          "'permission denied'", "os", "run:  chmod +x os")

        backups = root / MARKER / "backups"
        if backups.is_dir():
            kept = list(backups.glob("*.zip"))
            weight = sum(z.stat().st_size for z in kept)
            live = sum(f.stat().st_size for bucket in self.os.buckets()
                       for f in (root / bucket).rglob("*") if f.is_file())
            # more than twice what it is copying, and big enough to be worth
            # mentioning at all — a 3 MB folder does not need advice
            if kept and weight > live * 2 and weight > 50_000_000:
                self.flag("hint", "heavy-backups",
                          f"{len(kept)} backups are using {human_size(weight)} — more "
                          f"than twice the {human_size(live)} they are backing up",
                          ".os/backups",
                          "delete the older zips, or lower keep_backups in .os/config.json")

        # 6. clutter the desktop leaves behind
        litter = [q for q in root.rglob("*")
                  if q.name in (".DS_Store", ".localized") or q.name.endswith(".tmp~")
                  or q.name.startswith("._")]
        litter += [q for q in root.rglob("__pycache__") if q.is_dir()]
        for q in litter:
            if fix:
                try:
                    shutil.rmtree(q) if q.is_dir() else q.unlink()
                    repaired.append(f"removed {self.os.rel(q)}")
                except OSError:
                    pass
            else:
                self.flag("hint", "clutter",
                          f"{self.os.rel(q)} is junk your computer left behind",
                          self.os.rel(q), "./os check --fix")

        # 7. hygiene and decay
        stale = int(self.os.thresholds.get("stale_project_days", 30))
        dormant = int(self.os.thresholds.get("dormant_project_days", 75))
        for it in items:
            if it.kind != "project" or it.status in CLOSED_STATUSES:
                continue
            # Only work being pushed can go stale. Something you are holding is
            # *supposed* to sit still between the times you tend to it — nagging
            # about that is the system misunderstanding its own vocabulary.
            if it.status == HOLDING:
                continue
            age = days_since(it.updated)
            if age >= dormant:
                self.flag("warn", "dormant", f"'{it.title}' hasn't moved in {age} days",
                          self.os.rel(it.path),
                          f"./os hold {it.ident}, or ./os close {it.ident}")
            elif age >= stale:
                self.flag("hint", "stale", f"'{it.title}' hasn't moved in {age} days",
                          self.os.rel(it.path), "give it a next action, or ./os hold it")

        # 8. category pressure
        cap = int(self.os.thresholds.get("category_max_items", 99))
        pressure: dict[tuple, int] = {}
        for it in items:
            if it.trail:
                key = (it.bucket, tuple(it.trail))
                pressure[key] = pressure.get(key, 0) + 1
        for (bucket, trail), n in pressure.items():
            if n > cap:
                self.flag("warn", "category-overflow",
                          f"{bucket}/{'/'.join(trail)} holds {n} things — that is a lot for one folder",
                          f"{bucket}/{'/'.join(trail)}", "raise category_split in .os/config.json, or split it yourself")

        # 9. broken internal links
        for it in items:
            if not it.spine or not it.spine.exists() or it.spine.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = read_text(it.spine, 80_000)
            for m in re.finditer(r"\[[^\]]*\]\(([^)#:]+\.md)\)", text):
                target = (it.spine.parent / m.group(1).replace("%20", " ")).resolve()
                if not target.exists():
                    self.flag("hint", "broken-link", f"'{it.title}' links to something that isn't there: {m.group(1)}",
                              self.os.rel(it.spine))
                    break

        # Errors are always serious. Warnings matter but saturate. Hints are
        # texture — a folder with 400 items will always have some, and that is
        # not the same as being unhealthy.
        self._rollup()
        tally = {"error": 0, "warn": 0, "hint": 0}
        for issue in self.issues:
            tally[issue["level"]] += 1
        penalty = (
            tally["error"] * 15
            + min(tally["warn"] * 4, 40)
            + min(tally["hint"] * 0.5, 15)
        )
        score = int(max(0, 100 - min(penalty, 100)))
        return {"issues": self.issues, "score": score, "repaired": repaired,
                "items": len(items)}


# ---------------------------------------------------------------------------
# search, archive, undo, backup, review
# ---------------------------------------------------------------------------


#: Suffixes stripped to find a word's root, longest first. Not a full stemmer —
#: just enough that "refreshes" finds "refresh" and "meetings" finds "meeting".
_SUFFIXES = ("ingly", "edly", "ings", "ies", "ied", "ing", "ers", "est", "ed",
             "es", "er", "ly", "s")


def stem(word: str) -> str:
    """The root of a word, or the word itself when stripping would mangle it."""
    for suffix in _SUFFIXES:
        if not word.endswith(suffix):
            continue
        root = word[: -len(suffix)]
        if len(root) < 3:
            continue
        if suffix == "ies":
            return root + "y"
        # "running" -> "runn" -> "run"
        if len(root) > 3 and root[-1] == root[-2] and root[-1] not in "aeiou":
            root = root[:-1]
        return root
    return word


class Term:
    """One search word: how it was typed, and its root.

    The word as typed matches anywhere, the way it always has. The root only
    matches at the start of a word — otherwise "biling" stems to "bil" and
    starts finding every note about anything mobile."""

    __slots__ = ("word", "root", "_rx")

    def __init__(self, word: str):
        self.word = word
        root = stem(word)
        self.root = root if root != word else ""
        self._rx = re.compile(r"(?<![a-z0-9])" + re.escape(root)) if self.root else None

    def weight(self, hay: str) -> float:
        """1.0 for the word as typed, less for a root-only match, 0 for neither."""
        if self.word in hay:
            return 1.0
        return Finder.STEM_WEIGHT if self._rx and self._rx.search(hay) else 0.0

    def count(self, hay: str) -> tuple[int, float, str]:
        """(hits, how much each is worth, the form that matched)."""
        n = hay.count(self.word)
        if n:
            return n, 1.0, self.word
        if self._rx:
            found = self._rx.findall(hay)
            if found:
                return len(found), Finder.STEM_WEIGHT, self.root
        return 0, 0.0, ""


WORD_RE = re.compile(r"[a-z][a-z0-9'-]{2,}")


class Finder:
    """Ranked full-text search over the whole OS. No index server, no daemon."""

    #: how much of a term's score a root-only match earns
    STEM_WEIGHT = 0.6

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.scanner = Scanner(os_)
        self.corrected: dict[str, str] = {}   # what we searched for instead

    # -- scoring ------------------------------------------------------------

    def _pass(self, items: list[Item], terms: list["Term"],
              vocabulary: set | None) -> list[tuple[float, Item, str]]:
        results = []
        for it in items:
            hay_title = f"{it.ident} {it.title} {it.path.name}".lower()
            hay_meta = f"{it.domain} {' '.join(it.tags)} {it.status} {it.blurb}".lower()
            raw = ""
            if it.spine and it.spine.exists() and it.spine.suffix.lower() in TEXT_SUFFIXES:
                _, raw = parse_frontmatter(read_text(it.spine, 60_000))
                raw = COMMENT_RE.sub(" ", raw)
            body = raw.lower()

            # Collect the words we have already read, so a failed search can ask
            # "did you mean" without opening a single extra file.
            if vocabulary is not None and len(vocabulary) < 60_000:
                vocabulary.update(WORD_RE.findall(hay_title))
                vocabulary.update(WORD_RE.findall(hay_meta))
                vocabulary.update(WORD_RE.findall(body[:8_000]))

            score, snippet = 0.0, (it.blurb or it.summary)
            for term in terms:
                score += 10 * term.weight(hay_title)
                score += 4 * term.weight(hay_meta)
                hits, worth, form = term.count(body)
                if not hits:
                    continue
                score += min(hits, 8) * 1.2 * worth
                if not snippet or form not in (snippet or "").lower():
                    pos = body.find(form)
                    snippet = gist(raw[max(0, pos - 60): pos + 120], 200)
            if len(terms) > 1 and all(t.weight(hay_title) for t in terms):
                score += 12
            if score > 0:
                results.append((round(score, 2), it, snippet or ""))
        results.sort(key=lambda r: (-r[0], r[1].title.lower()))
        return results

    # -- the search ---------------------------------------------------------

    def search(self, query: str, limit: int = 20, kind: str = "", bucket: str = "") -> list[tuple[float, Item, str]]:
        self.corrected = {}
        terms = [t for t in re.split(r"\s+", query.lower().strip()) if t]
        if not terms:
            return []
        # Skills and helpers are the machinery, not the person's work. Searching
        # "one sentence" should not hand back nine skill files ahead of the two
        # notes they were actually looking for. Ask for them by kind to see them.
        toolkit = {"skill", "agent", "hook"}
        items = [it for it in self.scanner.scan()
                 if (kind in toolkit or it.kind not in toolkit)
                 and (not kind or it.kind == kind)
                 and (not bucket or it.bucket.lower().startswith(bucket.lower()))]

        vocabulary: set[str] = set()
        hits = self._pass(items, [Term(t) for t in terms], vocabulary)
        if hits:
            return hits[:limit]

        # Nothing matched. Before giving up, assume a typo: every word in the
        # folder is already in hand from the pass above, so this costs no reads.
        fixed = []
        for term in terms:
            if len(term) < 4 or term in vocabulary:
                fixed.append(term)
                continue
            near = difflib.get_close_matches(term, vocabulary, n=1, cutoff=0.75)
            if near and near[0] != term:
                self.corrected[term] = near[0]
                fixed.append(near[0])
            else:
                fixed.append(term)
        if not self.corrected:
            return []
        return self._pass(items, [Term(t) for t in fixed], None)[:limit]

    def like(self, title: str, kinds: tuple = (), threshold: float = 0.82) -> list:
        """Things already here that are near-identical to `title`.

        `os save` files a sentence that reads like work as a project. An AI that
        then also runs `os new project` for the same sentence lands you with
        W.01 and W.02 describing one thing — so both commands ask this first."""
        want = title.lower().strip()
        if not want:
            return []
        matcher = difflib.SequenceMatcher(autojunk=False)
        matcher.set_seq2(want)
        hits = []
        for it in self.scanner.scan():
            if kinds and it.kind not in kinds:
                continue
            if it.bucket == self.os.bucket_for_role("archive"):
                continue
            have = it.title.lower().strip()
            if not have:
                continue
            # "Redesign the pricing page" inside "Redesign the pricing page before
            # the launch on the 14th" is the same piece of work, even though the
            # lengths are far enough apart that a ratio would never say so.
            if len(have) > 12 and (have in want or want in have):
                hits.append((1.0, it))
                continue
            if 2 * min(len(have), len(want)) < threshold * (len(have) + len(want)):
                continue
            matcher.set_seq1(have)
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                continue
            if matcher.ratio() >= threshold:
                hits.append((round(matcher.ratio(), 3), it))
        hits.sort(key=lambda h: -h[0])
        return [it for _score, it in hits]

    def by_id(self, ident: str) -> Item | None:
        """The one item carrying this number. An empty string matches nothing.

        Skills, helpers and anything not yet sorted have no number, so `it.ident
        == ""` was true for the first of them the scanner happened to reach —
        and `./os close ""` quietly archived it."""
        ident = ident.strip()
        if not ident:
            return None
        for it in self.scanner.scan():
            if it.ident == ident or it.path.name.startswith(ident + "_"):
                return it
        return None


class Archivist:
    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.finder = Finder(os_)

    def archive(self, ident: str) -> Path:
        item = self.finder.by_id(ident)
        if item is None:
            die(f"nothing here is numbered {ident} — try  ./os find {ident}")
        shelf = self.os.bucket_for_role("archive")
        if item.bucket == shelf:
            die(f"{ident} is already in the archive — ./os back {ident} brings it out")
        dest = self.os.root / shelf / today()[:4] / item.bucket / item.path.name
        if item.spine and item.spine.exists():
            meta, body = parse_frontmatter(read_text(item.spine))
            # What it was before it left, so ./os back can put it back as it was
            # rather than waking every filed note up as work with a next action.
            was = str(meta.get("status") or "").strip()
            meta["status"] = "archived"
            meta["archived"] = today()
            if was:
                meta["was"] = was
            meta["origin"] = self.os.rel(item.path)
            meta["updated"] = today()
            write_text(item.spine, compose(meta, body))
        moved = self.os.move_item(item.path, dest)
        self.os.commit(f"archive {ident}")
        return moved

    def restore(self, ident: str) -> Path:
        item = self.finder.by_id(ident)
        if item is None or item.bucket != self.os.bucket_for_role("archive"):
            die(f"{ident} is not in the archive — ./os show {ident} says where it is")
        meta, body = ({}, "")
        if item.spine and item.spine.exists():
            meta, body = parse_frontmatter(read_text(item.spine))
        # Where it actually came from, recorded when it was put away. The trail
        # (<year>/<bucket>/) is the fallback, and only then a default.
        origin = str(meta.get("origin") or "")
        origin_bucket = (origin.split("/", 1)[0] if "/" in origin else "") \
            or (item.trail[1] if len(item.trail) > 1 else "") \
            or self.os.bucket_for_role("note")
        if origin_bucket not in self.os.buckets():
            origin_bucket = self.os.bucket_for_role("note")
        dest = self.os.root / origin_bucket / item.path.name
        if item.spine and item.spine.exists():
            # A note or a file is not work, and coming out of the archive must
            # not turn it into work with a next action. Only what was pushed or
            # held goes back to a live phase; everything else keeps its own word.
            was = str(meta.pop("was", "") or "").strip()
            meta["status"] = was or ("—" if str(meta.get("type", "")) != "work" else PUSHING)
            meta.pop("archived", None)
            meta.pop("origin", None)
            meta["updated"] = today()
            write_text(item.spine, compose(meta, body))
        moved = self.os.move_item(item.path, dest)
        self.os.commit(f"restore {ident}")
        return moved


class Undo:
    def __init__(self, os_: "Zenith"):
        self.os = os_

    def rel_of(self, path: Path) -> str:
        return relative_to_root(path, self.os.root)

    def peek(self) -> dict | None:
        undo = self.os.state.get("undo") or []
        return undo[-1] if undo else None

    def revert(self) -> dict:
        undo = self.os.state.get("undo") or []
        if not undo:
            die("nothing to undo")
        entry = undo.pop()
        restored, failed = 0, []
        #: where each thing actually came back to. A name can already be taken
        #: by the time we put something back — two runs undone in turn, the
        #: second having reused the first's filenames — and then it lands beside
        #: its old name rather than on it. Its saved content has to follow it
        #: there, or it is written over whatever now holds that name instead.
        landed: dict[str, str] = {}
        for step in reversed(entry["steps"]):
            try:
                if step["action"] == "move":
                    src = self.os.root / step["dst"]
                    dst = self.os.root / step["src"]
                    # `exists()` follows the link, so a shortcut whose target is
                    # gone reads as "not there" and undo used to abandon it in
                    # whichever bucket the sort had put it in.
                    if src.exists() or src.is_symlink():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        free = unique_path(dst)
                        shutil.move(str(src), str(free))
                        if free != dst:
                            landed[step["src"]] = self.rel_of(free)
                        restored += 1
                    else:
                        failed.append(step["dst"])
                elif step["action"] == "create":
                    path = self.os.root / step["src"]
                    if path.is_file():
                        path.unlink()
                        restored += 1
                elif step["action"] == "mkdir":
                    path = self.os.root / step["src"]
                    if path.is_dir() and not [p for p in path.iterdir() if not ignored(p) and p.name != CATEGORY_MARKER]:
                        shutil.rmtree(path, ignore_errors=True)
                        restored += 1
            except OSError as exc:
                failed.append(f"{step.get('dst','?')}: {exc}")

        # locations are back; now put the contents back too.
        #
        # Only into a file that is actually sitting there. One run can move the
        # same thing twice — the adopt pass files it into Notes/, the balance
        # pass tucks it into a category — and each move snapshots it under the
        # path it had at the time. Writing every snapshot back unconditionally
        # re-created the file at that intermediate path, so undoing a sort left
        # the item where it started *and* a numbered copy of it in Notes.
        blobs = self.os.dot / "cache" / "undo" / str(entry.get("blobs") or "")
        for rel, blob in (entry.get("snapshots") or {}).items():
            source = blobs / blob
            target = self.os.root / landed.get(rel, rel)
            if not source.exists() or not target.is_file():
                continue
            try:
                target.write_bytes(source.read_bytes())
                restored += 1
            except OSError as exc:
                failed.append(f"{rel}: {exc}")
        shutil.rmtree(blobs, ignore_errors=True)

        self.os.state["undo"] = undo
        self.os.save_state()
        # (also not journalled — see Zenith.commit)
        return {"label": entry["label"], "restored": restored, "failed": failed}


class Backup:
    def __init__(self, os_: "Zenith"):
        self.os = os_

    def snapshot(self) -> Path:
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        # two backups in the same second must not become one: the stamp is only
        # second-resolution, and silently overwriting a backup is the exact
        # opposite of what somebody asking for a backup wants
        out = self.os.dot / "backups" / f"zenith-{stamp}.zip"
        out.parent.mkdir(parents=True, exist_ok=True)
        out = unique_path(out)
        # Matched on the whole path, never on a bare folder name. Anchoring these
        # to .os/ matters: somebody's own `Notes//cache/` or a project's
        # `notes/backups/` is their writing, and a backup that quietly leaves it
        # out is worse than no backup at all.
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv"}
        # .os/transcripts is a cache too: keyed by video, re-fetchable, and
        # bigger than everything it sits beside. A backup of it is 100KB of
        # somebody else's words that yt-dlp would hand back for free.
        skip_trees = ((MARKER, "backups"), (MARKER, "cache"), (MARKER, "transcripts"))
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in self.os.root.rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                rel = path.relative_to(self.os.root)
                if any(part in skip_dirs for part in rel.parts):
                    continue
                if any(rel.parts[:len(t)] == t for t in skip_trees):
                    continue
                zf.write(path, rel.as_posix())   # zip entries are always POSIX
        # by when it was written, not by what it is called: "…-2.zip" sorts
        # before "….zip", so sorting by name can drop the newest and keep the oldest
        keep = max(1, int(self.os.behaviour.get("keep_backups", 3)))
        zips = sorted((self.os.dot / "backups").glob("zenith-*.zip"),
                      key=lambda z: z.stat().st_mtime)
        for stale in zips[:-keep]:
            stale.unlink(missing_ok=True)
        return out


class Reviewer:
    """The anti-decay pass. A second brain dies from neglect, not from bad taxonomy."""

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.scanner = Scanner(os_)

    #: A line that is one step of a procedure: a bullet, a checkbox, or "3. do x".
    STEP_LINE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)", re.M)

    def routines(self, items: list, limit: int = 3) -> list:
        """Things being kept up by hand that an AI could just do.

        Nothing else in here ever mentions skills, so somebody who never opens
        the chat can use this folder for a year and not learn they exist. The
        moment to say so is not in a menu — it is when they have written down,
        in their own words, a job they do the same way every time.

        The bar is deliberately high: a cadence *and* a fixed procedure, because
        either alone is ordinary writing. "Every Monday I dread it" is not a
        skill, and neither is a checklist that runs once. A nudge that fires on
        the wrong note is worse than one that never fires — so when in doubt
        this says nothing. The words themselves live in .os/words.json, where
        somebody can teach it their own."""
        spec = self.os.taxonomy.get("routine") or {}
        cadence = [w.lower() for w in spec.get("cadence", [])]
        procedure = [w.lower() for w in spec.get("procedure", [])]
        if not cadence or not procedure:
            return []

        # Something already automated is not worth nagging about.
        have = {slugify(i.title) for i in items if i.kind in ("skill", "agent")}
        shelf = self.os.bucket_for_role("archive")
        out = []
        for it in items:
            if it.bucket == shelf:
                continue
            if not (it.kind == "note" or (it.kind == "project" and it.status == HOLDING)):
                continue
            if not it.spine or not it.spine.exists():
                continue
            if slugify(it.title) in have:
                continue
            _, body = parse_frontmatter(read_text(it.spine, 40_000))
            hay = (it.title + "\n" + COMMENT_RE.sub(" ", body)).lower()
            said = next((w for w in cadence if w in hay), "")
            if not said:
                continue
            how = next((w for w in procedure if w in hay), "")
            if not how and len(self.STEP_LINE.findall(body)) < 3:
                continue
            out.append({"id": it.ident, "title": it.title,
                        "where": self.os.rel(it.path), "said": said})
        return out[:limit]

    def run(self) -> dict:
        items = self.scanner.scan()
        health = Doctor(self.os).run(items=items)
        stale_days = int(self.os.thresholds.get("stale_project_days", 30))
        dormant_days = int(self.os.thresholds.get("dormant_project_days", 75))

        shelf = self.os.bucket_for_role("archive")
        active = [i for i in items if i.kind == "project" and i.status in (PUSHING, "")]
        holding = [i for i in items if i.kind == "project" and i.status == HOLDING
                   and i.bucket != shelf]
        report = {
            "generated": now_iso(),
            "score": health["score"],
            "counts": {k: v for k, v in Indexer._counts(items).items()},
            "unfiled": [i.title for i in items if Sorter.unmanaged(i)],
            "active": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in
                       sorted(active, key=lambda x: days_since(x.updated))],
            "stale": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in active
                      if stale_days <= days_since(i.updated) < dormant_days],
            "holding": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in
                        sorted(holding, key=lambda x: days_since(x.updated))],
            # Only work being pushed can look abandoned. Something held is quiet
            # by design, so it is never offered up for the archive on age alone.
            "archive_candidates": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in active
                                   if days_since(i.updated) >= dormant_days
                                   and i.bucket != shelf],
            "duplicates": [i for i in health["issues"] if i["code"] in ("duplicate-content", "near-duplicate")],
            "unsorted": [{"id": i.ident, "title": i.title} for i in items
                         if i.domain in ("", "unsorted") and i.kind in ("note", "project")][:20],
            "shipped": [{"id": i.ident, "title": i.title} for i in items
                        if i.status == "done" and i.bucket != shelf],
            # The classifier flags what it could not place confidently. That flag
            # is worthless if nothing ever shows it again — this is where a human
            # is meant to look.
            "unsure": [{"id": i.ident, "title": i.title, "where": i.bucket} for i in items
                       if "needs-review" in (i.flags or [])],
            # Things being kept up by hand that could be handed to an AI. This
            # is the only place the product ever mentions skills unprompted.
            "routines": self.routines(items),
            "issues": health["issues"],
        }
        self.os.config.setdefault("review", {})["last_run"] = today()
        self.os.save_config()
        write_text(self.os.dot / "cache" / "last-review.json", json.dumps(report, indent=2) + "\n")
        return report


# ---------------------------------------------------------------------------
# creating new things from blueprints
# ---------------------------------------------------------------------------

KIND_ALIASES = {
    "work": "project", "project": "project", "proj": "project", "p": "project", "w": "project",
    "ongoing": "project", "area": "project", "a": "project",
    "pushing": "project", "holding": "project", "hold": "project",
    "note": "note", "n": "note", "ref": "note", "reference": "note",
    "skill": "skill", "s": "skill",
    "helper": "agent", "agent": "agent", "subagent": "agent",
}


#: Some of the words above say *which phase* the new thing starts in. The rest
#: start it being pushed, because that is what somebody typing `os new` is
#: nearly always doing.
NEW_STATUS = {"ongoing": HOLDING, "area": HOLDING, "holding": HOLDING, "hold": HOLDING}


class Creator:
    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.templates = os_.dot / "templates"
        self._cache: dict = {}

    #: what a template file is called, when that differs from the internal name
    TEMPLATE_NAMES = {"project": "pushing"}

    def template(self, kind: str, status: str = "") -> str:
        """The blueprint for a kind, and — for work — for the phase it is in.

        Both halves of a piece of work ask *what does good look like here*.
        They differ only below that: pushing wants a next action, holding wants
        a cadence. So there are two blueprints and one kind. Cached: filing a
        big drop asks for the same handful of templates hundreds of times."""
        key = f"{kind}:{status}"
        if key in self._cache:
            return self._cache[key]
        names = []
        if kind == "project" and status:
            names.append(status)
        names += [self.TEMPLATE_NAMES.get(kind, kind), kind]
        for name in names:
            path = self.templates / f"{name}.md"
            if path.exists():
                return self._cache.setdefault(key, read_text(path))
        return ("---\nid: {{ID}}\ntitle: {{TITLE}}\ntype: {{KIND}}\nstatus: {{STATUS}}\n"
                "domain: {{DOMAIN}}\ntags: [{{TAGS}}]\ncreated: {{DATE}}\n"
                "updated: {{DATE}}\n---\n\n# {{TITLE}}\n")

    def render(self, kind: str, title: str, ident: str, domain: str,
               tags: list[str], status: str = "") -> str:
        text = self.template(kind, status)
        for key, value in {
            "{{ID}}": ident, "{{TITLE}}": title, "{{SLUG}}": slugify(title),
            "{{KIND}}": TYPE_ON_DISK.get(kind, kind), "{{STATUS}}": status or "—",
            "{{DATE}}": today(), "{{DOMAIN}}": domain or "unsorted",
            "{{TAGS}}": ", ".join(tags), "{{YEAR}}": today()[:4],
            "{{OWNER}}": str(self.os.config.get("owner") or ""),
            "{{OS_NAME}}": str(self.os.config.get("name", "Zenith")),
        }.items():
            text = text.replace(key, value)
        return text

    def create(self, kind: str, title: str, domain: str = "", tags: list[str] | None = None,
               status: str = "") -> Path:
        asked = kind.lower()
        kind = KIND_ALIASES.get(asked, "")
        if not kind:
            die("that is not a kind — try one of: work, ongoing, note, skill, helper")
        if kind == "project":
            status = status or NEW_STATUS.get(asked, PUSHING)
        else:
            status = ""
        tags = tags or []
        if not domain:
            # Nobody wants to be asked for a subject. Guess it from the title,
            # the same way the sorter would, and leave it blank if we cannot.
            guess, score, _ = Classifier(self.os).score_domain(title, title, "")
            if guess and score >= 3.0:
                domain = guess
        slug = slugify(title)
        root = self.os.root

        if kind == "skill":
            if slug in RESERVED_COMMANDS:
                die(f"'{slug}' is a built-in Claude Code command — pick another name (try os-{slug})")
            folder = root / ".claude" / "skills" / slug
            if folder.exists():
                die(f"skill '{slug}' already exists at {self.os.rel(folder)}")
            self.os.record("mkdir", self.os.rel(folder))
            write_text(folder / "SKILL.md", self.render("skill", title, "", domain, tags))
            self.os.created(folder / "SKILL.md")
            self.os.commit(f"new skill /{slug}")
            return folder / "SKILL.md"

        if kind == "agent":
            path = root / ".claude" / "agents" / f"{slug}.md"
            if path.exists():
                die(f"agent '{slug}' already exists")
            write_text(path, self.render("agent", title, "", domain, tags))
            self.os.created(path)
            self.os.commit(f"new agent {slug}")
            return path

        bucket = self.os.bucket_for_role(kind if kind == "project" else "note")
        ident = self.os.next_id(bucket)
        if kind == "project":
            folder = root / bucket / f"{ident}_{slug}"
            # Record the folders before the files inside them: undo replays the
            # steps backwards, so the contents have to come off first or the
            # directory is never empty enough to remove.
            self.os.record("mkdir", self.os.rel(folder))
            for sub in ("notes", "assets"):
                (folder / sub).mkdir(parents=True, exist_ok=True)
                self.os.record("mkdir", self.os.rel(folder / sub))
            write_text(folder / "README.md",
                       self.render(kind, title, ident, domain, tags, status))
            self.os.created(folder / "README.md")
            for sub in ("notes", "assets"):
                (folder / sub / ".gitkeep").touch()
                self.os.created(folder / sub / ".gitkeep")
            self.os.save_state()
            self.os.commit(f"new {status} {ident} — {title}")
            return folder / "README.md"

        path = root / bucket / f"{ident}_{slug}.md"
        write_text(path, self.render("note", title, ident, domain, tags))
        self.os.created(path)
        self.os.save_state()
        self.os.commit(f"new note {ident} — {title}")
        return path

    def stage(self) -> Path:
        """The staging area a capture passes through on its way to a folder."""
        stage = self.os.dot / "cache" / STAGING
        stage.mkdir(parents=True, exist_ok=True)
        return stage

    def capture(self, text: str, source: str = "") -> Path:
        """Write it down with no questions asked. Anything, any time.

        It lands in the staging area, not in a folder the person has. `os save`
        classifies and moves it in the same breath, so this file exists for the
        length of one command — which is the point: there is no drop folder to
        remember, and so nothing that can sit in one going stale."""
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        first = re.sub(r"\s+", " ", text.strip().split("\n")[0])[:60] or "capture"
        path = self.stage() / f"{stamp}-{slugify(first)}.md"
        header = f"---\nsaved: {now_iso()}\n"
        if source:
            header += f"source: {source}\n"
        header += "---\n\n"
        landed = unique_path(path)
        write_text(landed, header + text.rstrip() + "\n")
        return landed


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------

def wordmark(name: str = "Zenith") -> str:
    """The name, letter-spaced, with a rule exactly as wide as it is."""
    spaced = " ".join(name.upper())
    return "\n  " + spaced + "\n  " + "─" * len(spaced)


WORDMARK = wordmark()

HELP = """
  {name} — {tagline}

  {c1}THE FIVE YOU'LL ACTUALLY USE{c0}
    os                             what's going on right now
    os save "<anything>"           write it down — I file it for you
    os find <words>                search everything you've ever saved
    os show <id>   ·   os W.04       look at one thing: state, next action, log
    os open <id>                   show me where W.04 is on disk
    os undo                        take back the last thing Zenith did

  {c1}STARTING, AND STOPPING{c0}
    os new work "<name>"           start something you're pushing on
    os new ongoing "<name>"        start something you'll just keep up
    os new note "<name>"           write a note yourself
    os new skill "<name>"          teach your AI a job you want done the same way
    os hold <id>                   no next action, just keep it level
    os push <id>                   back on the go
    os close <id>                  no longer live — put it away in Archive/
    os back <id>                   get it out of the archive again

  {c1}HOUSEKEEPING (rarely needed){c0}
    os sort                        file anything you dropped in by hand
    os check                       is anything broken?   --fix repairs it
    os tidy                        what's gone stale, doubled up or unfiled
    os backup                      zip a copy of everything
    os edit <id>                   open it in your text editor
    os demo                        two-minute tour, then puts everything back
    os name "<your name>"          put your name on this folder

  {c1}IF YOU NEED IT{c0}
    os help <command>              more about any one of these
    os test                        prove it still works, on a throwaway copy
    os index / os brief            rebuild the list · what your AI gets told

  Add --json to most commands for output a script can read.
  Nothing is ever deleted, and every change can be undone with  os undo
  If the shell says permission denied, run  bash os  once and it fixes itself.
"""


def _theirs(argv: list[str]) -> list[str]:
    """The arguments that are the person's own words, not options.

    Once a command has taken the flags it knows, what is left is theirs — but
    dropping everything that starts with a dash silently eats `-3 degrees` and
    `-Xf12o4jt4`. A bare `--` says the rest is content whatever it looks like,
    which is the only way to name such a thing on a command line."""
    if "--" in argv:
        return argv[argv.index("--") + 1:]
    return [a for a in argv if not a.startswith("-")]


def _flag(argv: list[str], *names: str) -> bool:
    for n in names:
        if n in argv:
            argv.remove(n)
            return True
    return False


def _opt(argv: list[str], name: str, default: str = "") -> str:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            value = argv[i + 1]
            del argv[i:i + 2]
            return value
        del argv[i]
    for a in list(argv):
        if a.startswith(name + "="):
            argv.remove(a)
            return a.split("=", 1)[1]
    return default


def cmd_status(os_: Zenith, argv: list[str]) -> int:
    """One screen. Plain sentences, not a dashboard."""
    if _flag(argv, "--json"):
        print(json.dumps(Reviewer(os_).run(), indent=2))
        return 0
    if not (os_.dot / "registry.json").exists():
        Indexer(os_).build()
    items = Scanner(os_).scan()
    health = Doctor(os_).run(items=items)

    mark = wordmark(os_.config.get("name", "Zenith"))
    Out.raw(paint(mark, S.GOLD) if S.enabled else mark)
    Out.raw("  " + paint(str(os_.root), S.FAINT))
    Out.raw()

    rows = [(b, len([i for i in items if i.bucket == b]), spec["blurb"])
            for b, spec in os_.buckets().items()]
    width = max(len(r[0]) for r in rows) + 3
    for bucket, n, blurb in rows:
        bar = paint("▍", S.GOLD) if n else " "
        Out.raw("  " + bar + paint(pad(bucket, width - 1), S.INK if n else S.FAINT)
                + paint(pad(str(n) if n else "·", 6), S.B if n else S.FAINT)
                + paint(trunc(blurb, 50), S.FAINT))
    Out.raw()

    waiting = len([i for i in items if Sorter.unmanaged(i)])
    staged = staged_captures(os_.dot)
    active = [i for i in items if i.kind == "project" and i.status == PUSHING]
    held = [i for i in items if i.kind == "project" and i.status == HOLDING
            and i.bucket != os_.bucket_for_role("archive")]
    cold = [i for i in active if days_since(i.updated) >= int(os_.thresholds["stale_project_days"])]
    errors = [i for i in health["issues"] if i["level"] == "error"]

    def plural(n, word):
        return f"{n} {word}" + ("" if n == 1 else "s")

    if active:
        line = plural(len(active), "thing") + " on the go"
        if cold:
            line += f", {len(cold)} you haven't touched in a while"
        Out.raw("  " + paint(line + ".", S.INK))
    elif not held:
        Out.raw("  " + paint("Nothing started yet.", S.FAINT))
    # Held work is listed, never counted as stale: sitting quiet is what it is
    # for. Without this line it would be invisible, which is how the old
    # Ongoing folder became a place things went to be forgotten.
    if held:
        Out.raw("  " + paint(plural(len(held), "thing") + " you're just keeping level.", S.MUTE))
    if waiting:
        Out.raw("  " + paint(plural(waiting, "thing") + " dropped in but not filed.", S.AMBER)
                + paint("   ./os sort", S.FAINT))
    # Said separately from the line above, because it is a different worry: not
    # a file they dragged in and forgot, but words they typed that this program
    # accepted and then failed to put anywhere.
    if staged:
        Out.raw("  " + paint(plural(len(staged), "thing") + " you saved never got filed.", S.AMBER)
                + paint("   ./os sort", S.FAINT))
    if errors:
        Out.raw("  " + paint(plural(len(errors), "thing") + " need fixing.", S.RED)
                + paint("   ./os check --fix", S.FAINT))
    elif health["score"] < 85:
        Out.raw("  " + paint("A few small things could be tidier.", S.AMBER)
                + paint("   ./os tidy", S.FAINT))

    Out.raw()
    Out.raw("  " + paint('./os save "anything on your mind"', S.GOLD)
            + paint("   or open this folder in any AI and just talk", S.FAINT))
    Out.raw("  " + paint("./os help", S.MUTE) + paint("   everything you can type", S.FAINT))
    Out.raw()
    return 0


def cmd_sort(os_: Zenith, argv: list[str]) -> int:
    dry = _flag(argv, "--dry-run", "-n")
    as_json = _flag(argv, "--json")
    if dry:
        result = Sorter(os_, dry=True).run()
    else:
        with Lock(os_, "sort"):
            result = Sorter(os_, dry=False).run()
            os_.commit("sort")
            Indexer(os_).build()
    if as_json:
        print(json.dumps(result, indent=2))
        return 1 if result.get("skipped") else 0

    def report_skipped() -> None:
        for entry in result.get("skipped", [])[:20]:
            Out.warn(f"couldn't file {entry['path']} — {entry['why']}")

    Out.title("filing", "a preview — nothing has moved" if dry else "")
    if not result["moves"]:
        if result.get("skipped"):
            report_skipped()
            Out.note("everything else is filed already")
        else:
            Out.ok("nothing waiting — everything is filed already")
        Out.raw()
        return 1 if result.get("skipped") else 0
    width = max((vlen(m[1]) for m in result["moves"]), default=10)
    width = min(width, 46)
    for what, src, dst in result["moves"][:200]:
        arrow = paint("→", S.FAINT)
        Out.raw("  " + paint(pad(what, 9), S.GOLD) + pad(trunc(src, width), width + 2)
                + arrow + " " + paint(trunc(dst, 52), S.INK))
    if len(result["moves"]) > 200:
        Out.note(f"...and {len(result['moves']) - 200} more")
    Out.raw()
    bits = []
    if result["filed"]:
        bits.append(f"{result['filed']} filed")
    if result["identified"]:
        bits.append(f"{result['identified']} numbered")
    if result["balanced"]:
        bits.append(f"{result['balanced']} tucked into folders")
    Out.ok(" · ".join(bits) or "nothing to do")
    report_skipped()
    if not dry:
        Out.note("didn't like any of that?  ./os undo puts it all back")
    Out.raw()
    return 1 if result.get("skipped") else 0


def cmd_index(os_: Zenith, argv: list[str]) -> int:
    as_json = _flag(argv, "--json")
    notify = _flag(argv, "--notify")
    registry = Indexer(os_).build()
    if notify:
        # Files dropped straight into a folder by hand are otherwise invisible
        # until the person happens to run ./os. The Stop hook rebuilds the index
        # anyway, so noticing costs nothing.
        loose = sorted(os_.rel(i.path) for i in Scanner(os_).scan() if Sorter.unmanaged(i))
        if loose:
            listed = ", ".join(loose[:4]) + (f" and {len(loose) - 4} more" if len(loose) > 4 else "")
            print(json.dumps({"systemMessage":
                f"{len(loose)} file(s) were dropped in by hand and are not filed yet: "
                f"{listed}. Offer to file them — `./os sort` numbers them and gives "
                "them a header, and `./os undo` reverses it. Do not file them "
                "without asking."}))
        return 0
    if as_json:
        print(json.dumps(registry["counts"], indent=2))
        return 0
    Out.title("index")
    Out.ok(f"read {registry['counts'].get('total', 0)} things and rebuilt the list")
    Out.note("INDEX.md · .claude/CATALOG.md")
    Out.raw()
    return 0


def _looks_like_a_path(text: str) -> bool:
    """Did they mean a file that is not there, rather than a sentence?

    Getting this wrong in either direction is bad: silently filing a typo'd
    path as a note loses the file they meant, and rejecting a real sentence is
    infuriating. Spaces settle it — paths people type rarely have them, and
    sentences almost always do."""
    if " " in text or "\n" in text:
        return False
    return (text.startswith(("/", "~", "./", "../"))
            or "/" in text
            or bool(re.search(r"\.[A-Za-z0-9]{1,5}$", text)))


KIND_WORDS = {"project": "work", "note": "a note",
              "asset": "a file", "skill": "a skill", "agent": "a helper"}

#: What each kind is called out loud. "project" and "asset" are internal words.
KIND_LABEL = {"project": "work", "note": "note", "asset": "file",
              "skill": "skill", "agent": "helper",
              "hook": "automatic", "archive": "archived"}


def cmd_save(os_: Zenith, argv: list[str]) -> int:
    """Write something down and put it where it belongs, in one step."""
    as_json = _flag(argv, "--json")
    src = _opt(argv, "--file")
    if argv and argv[0] == "--":           # the "everything after this is text" separator
        argv = argv[1:]

    # A single argument naming something real is a file, not a sentence. The name
    # has to be non-empty: Path("") is the current directory, and copying the
    # folder into itself is not what anybody meant.
    if not src and len(argv) == 1 and argv[0].strip():
        lone = argv[0].strip()
        candidate = Path(lone).expanduser()
        if candidate.name and candidate.exists():
            src, argv = str(candidate), []
        elif _looks_like_a_path(lone):
            die(f"there is no file at  {lone}\n"
                '     (to save those words as a note instead, put them in quotes '
                'with something else:  ./os save "note: ' + lone + '")')

    if src:
        path = Path(src.strip()).expanduser()
        if not path.name or not path.exists():
            die(f"there is no file at {src!r}")
        try:
            resolved = path.resolve()
        except OSError:
            die(f"cannot read {src}")
        if resolved == os_.root or os_.root in resolved.parents:
            die(f"{os_.rel(resolved)} is already in this folder — "
                "nothing to bring in")
        if resolved in os_.root.parents:
            die("that is a folder this one lives inside — pick something smaller")
        landed = unique_path(Creator(os_).stage() / path.name)
        try:
            shutil.copy2(path, landed) if path.is_file() else shutil.copytree(path, landed)
        except (OSError, shutil.Error) as exc:
            die(f"could not bring that in: {exc}")
        what = path.name
    else:
        text = " ".join(argv).strip()
        # Only reach for piped input when nothing at all was typed. `os save ""`
        # is somebody making a mistake, not somebody asking us to block on stdin
        # until the end of time.
        if not argv and not text and not sys.stdin.isatty():
            try:
                text = sys.stdin.read()
            except (OSError, KeyboardInterrupt):
                text = ""
        if not re.search(r"[^\W_]", text, re.UNICODE):
            die('tell me what to save:   ./os save "the thing on your mind"')
        landed = Creator(os_).capture(text)
        what = re.sub(r"\s+", " ", text.strip().split("\n")[0])[:64]

    with Lock(os_, "save"):
        dest, verdict = Sorter(os_).file_one(landed)
        os_.commit("save")
        Indexer(os_).build()

    if dest is None:
        die("I could not work out where that goes — it is safe in " + os_.rel(landed)
            + "\n     ./os sort picks it up from there")
    spine = Scanner(os_).spine_of(dest) or dest
    meta, _ = parse_frontmatter(read_text(spine)) if spine.is_file() else ({}, "")
    ident = str(meta.get("id") or "")
    if as_json:
        print(json.dumps({"saved": os_.rel(dest), "id": ident, "kind": verdict["kind"],
                          "title": verdict["title"], "filed": True}, indent=2))
        return 0

    twin = [i for i in Finder(os_).like(verdict["title"] or what,
                                        kinds=("project", "note"))
            if os_.rel(i.path) != os_.rel(dest)]
    Out.title("saved")
    Out.ok(verdict["title"] or what)
    if twin:
        Out.warn(f"{twin[0].ident} looks like the same thing: \"{trunc(twin[0].title, 44)}\"")
        Out.note(f"keep just one?  ./os undo   ·   compare:  ./os show {twin[0].ident}")
    Out.note(f"that's {KIND_WORDS.get(verdict['kind'], verdict['kind'])} — it's in "
             f"{os_.rel(dest).split('/')[0]}" + (f", numbered {ident}" if ident else ""))
    if "needs-review" in verdict.get("flags", []):
        Out.warn("I wasn't sure what this one was — worth a look")
    Out.note("wrong spot?  ./os undo")
    Out.raw()
    return 0


def cmd_new(os_: Zenith, argv: list[str]) -> int:
    tags = [t for t in _opt(argv, "--tags").split(",") if t.strip()]
    domain = _opt(argv, "--domain")
    anyway = _flag(argv, "--anyway", "--force")   # before the title is read off argv
    if not argv:
        die('what kind?   ./os new work "Ship the redesign"\n'
            "     kinds: work, ongoing, note, skill, helper")
    kind = argv[0]
    title = " ".join(argv[1:]).strip().strip('"')
    if not title:
        die(f'give it a name:   ./os new {kind} "Ship the redesign"')
    resolved_kind = KIND_ALIASES.get(kind.lower(), kind)
    if not anyway and resolved_kind in ("project", "note"):
        clash = Finder(os_).like(title, kinds=("project", "note"))
        if clash:
            first = clash[0]
            die(f"you already have {first.ident} \u2014 \"{first.title}\".\n"
                f"     Look at it:        ./os show {first.ident}\n"
                "     Want both anyway?  add --anyway to this command")
    with Lock(os_, "new"):
        path = Creator(os_).create(kind, title, domain, tags)
        phase = NEW_STATUS.get(kind.lower(), PUSHING)
        Indexer(os_).build()
    resolved = KIND_ALIASES.get(kind.lower(), kind)
    meta, _ = parse_frontmatter(read_text(path))
    Out.title("started")
    Out.ok(title)
    Out.note((f"numbered {meta['id']} · " if meta.get("id") else "") + os_.rel(path))
    if resolved == "skill":
        Out.note("type  /" + Path(path).parent.name + "  to run it")
    elif resolved == "agent":
        Out.note("your AI will call on @" + Path(path).stem + " when a job suits it")
    elif resolved == "project":
        Out.note("open it and write down what good looks like here")
        Out.note("it's " + ("being pushed — give it a next action"
                            if phase == PUSHING else
                            "being held — say how often you tend to it")
                 + paint(f"    ./os {'hold' if phase == PUSHING else 'push'} {meta.get('id','')}"
                         " flips that", S.FAINT))
    Out.raw()
    return 0


def _set_phase(os_: Zenith, argv: list[str], phase: str) -> int:
    """Move one piece of work between being pushed and being held.

    This is the move the old two-folder layout could not make without shuffling
    files around: work stops needing a next action and starts needing a
    standard, or the other way about, and it happens over and over to the same
    item. Here it is one word in the header."""
    other = HOLDING if phase == PUSHING else PUSHING
    if not argv or not argv[0].strip():
        die(f"which one?   ./os {'push' if phase == PUSHING else 'hold'} W.04"
            "      (run ./os to see the numbers)")
    item = Finder(os_).by_id(argv[0])
    if item is None:
        die(f"nothing here is numbered {argv[0]} — try  ./os find {argv[0]}")
    if item.kind != "project":
        die(f"{item.ident} is {KIND_WORDS.get(item.kind, item.kind)}, not work — "
            "only work is pushed or held")
    if not (item.spine and item.spine.exists()):
        die(f"{item.ident} has no header to change — ./os check --fix")
    with Lock(os_, "phase"):
        meta, body = parse_frontmatter(read_text(item.spine))
        was = normalize_status(meta.get("status"), "project")
        meta["status"] = phase
        meta["updated"] = today()
        write_text(item.spine, compose(meta, body))
        os_.commit(f"{item.ident} {was} -> {phase}")
        Indexer(os_).build()
    Out.title("pushing" if phase == PUSHING else "holding")
    Out.ok(f"{item.ident} — {item.title}")
    if phase == HOLDING:
        Out.note("it won't be counted as on the go, and it won't be nagged for "
                 "going quiet — that's what holding means")
        Out.note("say how often you tend to it under ## How often")
    else:
        Out.note("it's on the go again — give it a next action")
    Out.note(f"back the other way?  ./os {'push' if phase == HOLDING else 'hold'} {item.ident}")
    Out.raw()
    return 0


def cmd_hold(os_: Zenith, argv: list[str]) -> int:
    return _set_phase(os_, argv, HOLDING)


def cmd_push(os_: Zenith, argv: list[str]) -> int:
    return _set_phase(os_, argv, PUSHING)


def cmd_find(os_: Zenith, argv: list[str]) -> int:
    as_json = _flag(argv, "--json")
    kind = _opt(argv, "--kind")
    bucket = _opt(argv, "--in")
    limit = int(_opt(argv, "--limit", "20") or 20)
    query = " ".join(argv).strip()
    if not query:
        die("what are you looking for?   ./os find token refresh")
    finder = Finder(os_)
    hits = finder.search(query, limit=limit, kind=kind, bucket=bucket)
    if as_json:
        print(json.dumps([{"score": s, "id": i.ident, "title": i.title, "kind": i.kind,
                           "path": os_.rel(i.path), "snippet": sn} for s, i, sn in hits], indent=2))
        return 0
    Out.title("found", f'"{query}"')
    if finder.corrected:
        swaps = ", ".join(f"{was} → {now}" for was, now in finder.corrected.items())
        Out.note(f"no exact match, so I searched for  {swaps}")
    if not hits:
        Out.warn("nothing matched — try fewer words, or a different one")
        Out.raw()
        return 1
    for score, item, snippet in hits:
        Out.raw("  " + paint(pad(item.ident or "—", 8), S.GOLD) + paint(item.title, S.B)
                + paint(f"  · {KIND_LABEL.get(item.kind, item.kind)}", S.FAINT))
        Out.raw("          " + paint(trunc(os_.rel(item.path), 74), S.MUTE))
        if snippet:
            Out.raw("          " + paint(trunc(snippet, 74), S.FAINT))
    Out.raw()
    return 0


def _reveal(target: Path) -> bool:
    """Show something in the desktop's own file browser. False if we cannot."""
    if sys.platform == "darwin":
        command = ["open", "-R", str(target)]
    elif sys.platform.startswith("win"):
        command = ["explorer", "/select,", str(target)]
    else:
        # Linux: no "reveal this file" standard, so open the folder it is in
        command = ["xdg-open", str(target.parent)]
    try:
        subprocess.run(command, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, FileNotFoundError):
        return False


SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


def _sections(body: str) -> dict:
    """A markdown body split into its `## ` sections, in order."""
    out, current, buf = {}, "", []
    for line in body.split("\n"):
        m = SECTION_RE.match(line)
        if m:
            if current:
                out[current.lower()] = "\n".join(buf).strip()
            current, buf = m.group(1), []
        elif current:
            buf.append(line)
    if current:
        out[current.lower()] = "\n".join(buf).strip()
    return out


def _clean(text: str, limit: int = 3) -> list:
    """Readable lines from a section: no HTML comments, no empty checkboxes."""
    lines = []
    for raw in re.sub(r"<!--.*?-->", "", text, flags=re.S).split("\n"):
        line = raw.strip()
        if not line or line in ("-", "- [ ]", "*") or line.startswith(("#", "```", "|", "---")):
            continue
        lines.append(re.sub(r"^[-*]\s*(\[[ xX]\]\s*)?", "", line))
        if len(lines) >= limit:
            break
    return lines


def cmd_show(os_: Zenith, argv: list[str]) -> int:
    """Everything worth knowing about one item, without opening a file."""
    if not argv or not argv[0].strip():
        die("which one?   ./os show W.04      (run ./os to see the numbers)")
    ident = argv[0]
    item = Finder(os_).by_id(ident)
    if item is None:
        die(f"nothing here is numbered {ident} — try  ./os find {ident}")

    Out.title(item.ident or "item", KIND_LABEL.get(item.kind, item.kind))
    Out.raw("  " + paint(item.title, S.B, S.INK))
    Out.raw()
    Out.kv("where", os_.rel(item.path), 14)
    if item.status and item.status != "—":
        Out.kv("state", item.status, 14)
    age = days_since(item.updated)
    Out.kv("touched", item.updated + paint(
        "   today" if age <= 0 else (f"   {age} days ago" if age > 1 else "   yesterday"),
        S.AMBER if age >= int(os_.thresholds["stale_project_days"]) else S.FAINT), 14)
    if item.tags:
        Out.kv("tags", ", ".join(item.tags[:8]), 14)

    body = ""
    if item.spine and item.spine.exists() and item.spine.suffix.lower() in TEXT_SUFFIXES:
        _, body = parse_frontmatter(read_text(item.spine, 60_000))
    parts = _sections(body)

    def block(heading: str, label: str, limit: int = 3, tone: str = S.INK) -> None:
        text = parts.get(heading.lower(), "")
        lines = _clean(text, limit)
        if not lines:
            return
        Out.raw()
        Out.raw("  " + paint(label.upper(), S.B, S.GOLD))
        for line in lines:
            Out.raw("    " + paint(trunc(line, 70), tone))

    block("what good looks like", "good looks like", 2, S.MUTE)
    block("next action", "next", 3, S.INK)
    block("how often", "how often", 2, S.MUTE)
    block("where it stands", "where it stands", 3, S.MUTE)
    block("keeps coming back", "keeps coming back", 3, S.INK)
    # Headings from files written before Projects and Ongoing merged.
    block("done looks like", "done looks like", 2, S.MUTE)
    block("the standard", "the standard", 3, S.MUTE)
    block("in one line", "in one line", 2, S.MUTE)
    block("open questions", "open questions", 3, S.AMBER)
    block("decisions", "decided", 3, S.MUTE)
    block("log", "lately", 3, S.FAINT)
    if not any(parts.get(h) for h in ("what good looks like", "next action", "how often",
                                      "where it stands", "keeps coming back",
                                      "done looks like", "the standard",
                                      "in one line", "open questions", "decisions", "log")):
        head = item.title.lower().rstrip(".")
        summary = [line for line in _clean(body, 4)
                   if not line.lower().startswith(head[:40])][:3]
        if summary:
            Out.raw()
            for line in summary:
                Out.raw("    " + paint(trunc(line, 70), S.MUTE))

    Out.raw()
    Out.note(f"./os edit {item.ident}  to change it   ·   "
             f"./os close {item.ident}  when it's no longer live")
    Out.raw()
    return 0


def cmd_open(os_: Zenith, argv: list[str]) -> int:
    if not argv or not argv[0].strip():
        die("which one?   ./os open W.04")
    item = Finder(os_).by_id(argv[0])
    if item is None:
        die(f"nothing here is numbered {argv[0]} — try  ./os find {argv[0]}")
    # the thing itself, not the card describing it: `open` answers "where is it",
    # and for a PDF sitting in Notes that means the file, not its card
    target = item.path
    print(str(target))
    if sys.stdout.isatty():
        _reveal(target)
    return 0


def cmd_doctor(os_: Zenith, argv: list[str]) -> int:
    fix = _flag(argv, "--fix")
    as_json = _flag(argv, "--json")
    result = Doctor(os_).run(fix=fix)
    if as_json:
        print(json.dumps(result, indent=2))
        return 0 if not any(i["level"] == "error" for i in result["issues"]) else 1
    Out.title("check", f"{result['items']} things looked at")
    for line in result.get("repaired", []):
        Out.ok("fixed: " + line)
    if not result["issues"]:
        Out.ok("all good — nothing broken, nothing missing")
        Out.raw()
        return 0
    groups: dict[str, list[dict]] = {}
    for issue in result["issues"]:
        groups.setdefault(issue["level"], []).append(issue)
    # Show a few of each KIND of finding rather than the first forty overall.
    # Forty near-duplicates would otherwise push a one-off warning off the end,
    # and the one-off is usually the one worth reading.
    PER_KIND = 4
    for level in ("error", "warn", "hint"):
        emit = {"error": Out.bad, "warn": Out.warn, "hint": Out.info}[level]
        seen: dict = {}
        hidden = 0
        for issue in groups.get(level, []):
            code = issue.get("code", "")
            seen[code] = seen.get(code, 0) + 1
            if seen[code] > PER_KIND:
                hidden += 1
                continue
            emit(issue["message"])
            detail = "  ".join(x for x in (issue.get("path"), issue.get("fix")) if x)
            if detail:
                Out.note(detail)
        for code, n in seen.items():
            if n > PER_KIND:
                Out.note(f"...and {n - PER_KIND} more like '{code}'")
                hidden -= n - PER_KIND
        if hidden > 0:
            Out.note(f"...and {hidden} more {level}s")
    Out.raw()
    if not fix:
        Out.note("./os check --fix   fixes everything that is safe to fix on its own")
    Out.raw()
    return 0 if not groups.get("error") else 1


def cmd_review(os_: Zenith, argv: list[str]) -> int:
    as_json = _flag(argv, "--json")
    report = Reviewer(os_).run()
    if as_json:
        print(json.dumps(report, indent=2))
        return 0
    Out.title("tidy", today())

    def block(label: str, rows: list, render, style=S.INK) -> None:
        if not rows:
            return
        Out.raw("  " + paint(label.upper(), S.B, S.GOLD) + paint(f"  ({len(rows)})", S.FAINT))
        for row in rows[:12]:
            Out.raw("    " + render(row))
        if len(rows) > 12:
            Out.note(f"...and {len(rows) - 12} more")
        Out.raw()

    block("dropped in, not filed yet", report["unfiled"],
          lambda t: paint(trunc(str(t), 70), S.AMBER))
    block("on the go", report["active"][:8],
          lambda r: paint(pad(r["id"], 7), S.GOLD) + pad(trunc(r["title"], 44), 46)
                    + paint(f"{r['age']}d ago", S.FAINT))
    block("not touched in a while", report["stale"],
          lambda r: paint(pad(r["id"], 7), S.GOLD) + pad(trunc(r["title"], 44), 46)
                    + paint(f"{r['age']}d ago", S.AMBER))
    block("keeping level", report.get("holding", [])[:8],
          lambda r: paint(pad(r["id"], 7), S.GOLD) + pad(trunc(r["title"], 44), 46)
                    + paint(f"last tended {r['age']}d ago", S.FAINT))
    block("gone quiet — still pushing these?", report["archive_candidates"],
          lambda r: paint(pad(r["id"], 7), S.GOLD) + pad(trunc(r["title"], 40), 42)
                    + paint(f"./os hold {r['id']}", S.FAINT))
    block("marked done but still sitting in Work", report["shipped"],
          lambda r: paint(pad(r["id"], 7), S.GOLD) + trunc(r["title"], 46))
    block("I wasn't sure where these went", report["unsure"],
          lambda r: paint(pad(r["id"] or "—", 7), S.GOLD) + pad(trunc(r["title"], 40), 42)
                    + paint(f"now in {r['where']}", S.FAINT))
    block("might be the same thing twice", report["duplicates"],
          lambda r: paint(trunc(r["message"], 72), S.MUTE))

    if report.get("routines"):
        Out.raw("  " + paint("YOU DO THESE BY HAND EVERY TIME", S.B, S.GOLD)
                + paint(f"  ({len(report['routines'])})", S.FAINT))
        for row in report["routines"]:
            Out.raw("    " + paint(pad(row["id"] or "—", 7), S.GOLD)
                    + pad(trunc(row["title"], 40), 42)
                    + paint(f'you wrote "{row["said"]}"', S.FAINT))
        Out.note("a skill writes the steps down once, so your AI just does it:")
        Out.note(f'./os new skill "{trunc(report["routines"][0]["title"], 40)}"'
                 + paint("   (or say /make-skill in the chat)", S.FAINT))
        Out.raw()

    if not any([report["unfiled"], report["stale"], report["archive_candidates"],
                report["duplicates"], report["shipped"], report["unsure"],
                report.get("routines")]):
        Out.ok("nothing stale, nothing stuck, nothing doubled up")
    Out.raw()
    return 0


def cmd_close(os_: Zenith, argv: list[str]) -> int:
    """Take something out of the live folder.

    Not "finished" — things leave because you stopped carrying them, and that
    is as true of shipped work as of abandoned work. The word the folder uses
    should not claim more than it knows."""
    if not argv or not argv[0].strip():
        die("which one?   ./os close W.04      (run ./os to see the numbers)")
    with Lock(os_, "archive"):
        dest = Archivist(os_).archive(argv[0])
        Indexer(os_).build()
    Out.title("put away")
    Out.ok(os_.rel(dest))
    Out.note("it still turns up in ./os find — nothing gets deleted here")
    Out.note("still going, just quietly?  ./os back it, then ./os hold it")
    Out.note(f"changed your mind?  ./os back {argv[0]}")
    Out.raw()
    return 0


def cmd_back(os_: Zenith, argv: list[str]) -> int:
    if not argv or not argv[0].strip():
        die("which one?   ./os back W.04")
    with Lock(os_, "restore"):
        dest = Archivist(os_).restore(argv[0])
        Indexer(os_).build()
    Out.title("back out")
    Out.ok(os_.rel(dest))
    Out.raw()
    return 0


def cmd_undo(os_: Zenith, argv: list[str]) -> int:
    undo = Undo(os_)
    entry = undo.peek()
    if entry is None:
        Out.title("undo")
        Out.warn("nothing to undo — I haven't changed anything yet")
        Out.raw()
        return 1
    with Lock(os_, "undo"):
        result = undo.revert()
        Indexer(os_).build()
    Out.title("undone")
    if not result["restored"]:
        Out.warn(f"there was nothing left to reverse in '{result['label']}'")
        Out.note("whatever it made has since been moved or removed by hand")
        Out.raw()
        return 1
    changes = f"{result['restored']} change" + ("" if result["restored"] == 1 else "s")
    Out.ok(f"put back the last '{result['label']}' — {changes} reversed")
    for f in result["failed"][:10]:
        Out.warn("couldn't put this one back: " + str(f))
    Out.raw()
    return 0


def cmd_backup(os_: Zenith, argv: list[str]) -> int:
    out = Backup(os_).snapshot()
    kept = sorted((os_.dot / "backups").glob("*.zip"))
    total = sum(z.stat().st_size for z in kept)
    Out.title("backup")
    Out.ok(os_.rel(out) + paint(f"   {human_size(out.stat().st_size)}", S.FAINT))
    if len(kept) > 1:
        Out.note(f"{len(kept)} kept, {human_size(total)} in all — older ones are "
                 "dropped automatically")
    Out.note("move one somewhere else now and then; a copy on the same disk is "
             "not really a backup")
    Out.raw()
    return 0


PLAIN_SPEECH = (
    "Talk to them in plain English. Never say capture, bucket, taxonomy, front matter, "
    "index, sort or a health score out loud — those words are yours, not theirs. Say "
    '"I will write that down" and "I put it in Notes as N.03". Explain the machinery '
    "only if they ask."
)

FIRST_TIME = """ZENITH — this folder holds everything the person is working on, and keeps itself organised.

They have just opened it for the very first time and almost certainly have no idea what it does.

Your first message: say hello, and in five lines or fewer tell them plainly that whatever they \
tell you, you will write down and put in the right place for them — they never have to pick a \
folder or name a file. Then ask what they are working on at the moment.

Do NOT list commands, folder names or numbers, and do not say how many things are in here. Do \
not use the word "capture". Do not describe the system. They will ask when they want to know."""


def _brief_text(os_: Zenith) -> str:
    items = Scanner(os_).scan()
    theirs = [i for i in items if i.kind in ("project", "note", "asset")]
    loose = [i for i in items if Sorter.unmanaged(i)]
    if not theirs:
        return FIRST_TIME

    stale_days = int(os_.thresholds["stale_project_days"])
    active = sorted([i for i in items if i.kind == "project" and i.status == PUSHING],
                    key=lambda i: days_since(i.updated))
    held = sorted([i for i in items if i.kind == "project" and i.status == HOLDING
                   and i.bucket != os_.bucket_for_role("archive")],
                  key=lambda i: days_since(i.updated))
    errors = [i for i in Doctor(os_).run(items=items)["issues"] if i["level"] == "error"]

    def ago(days: int) -> str:
        return "today" if days <= 0 else ("yesterday" if days == 1 else f"{days} days ago")

    def things(n: int) -> str:
        return f"{n} thing" + ("" if n == 1 else "s")

    out = ["ZENITH — one folder holding this person's work, notes and files. "
           "Work is in one of two phases: pushing (has a next action) or holding "
           "(has a standard, no next action, and is not late for anything). "
           "The full list is INDEX.md; the rules are AGENTS.md.", "", "Right now:"]
    if active:
        shown = "; ".join(
            f"{i.ident} {_shorten(i.title, 48)} (last touched {ago(days_since(i.updated))}"
            + (", going cold)" if days_since(i.updated) >= stale_days else ")")
            for i in active[:4])
        if len(active) > 4:
            shown += f"; and {len(active) - 4} more open — the full list is INDEX.md"
        out.append("- On the go: " + shown)
    else:
        out.append("- Nothing being pushed right now.")
    if held:
        out.append("- Being kept up (no next action wanted): " + "; ".join(
            f"{i.ident} {_shorten(i.title, 40)}" for i in held[:4])
            + (f"; and {len(held) - 4} more" if len(held) > 4 else ""))
    out.append(f"- {things(len(loose))} dropped in but not filed — ./os sort" if loose
               else "- Nothing waiting to be filed.")
    if errors:
        out.append(f"- {things(len(errors))} broken — ./os check --fix")
    out += ["",
            'To act for them: ./os save "<text>" writes something down and files it · '
            "./os find <words> searches everything · ./os open <id> · ./os undo reverses "
            "the last thing you did.",
            "", PLAIN_SPEECH]
    return "\n".join(out)


def cmd_learn(os_: Zenith, argv: list[str]) -> int:
    """Fetch and cache what a source actually says, so an AI can learn from it.

    Deliberately does no thinking. It lists, it fetches, it cleans, it caches —
    which sources are worth reading and what they add up to is the AI's job,
    and the reason this is a command rather than a skill is so every AI can
    reach it, not just the one with the skill file.
    """
    # Don't leave a __pycache__ behind: `./os check` reports it as junk, and a
    # command that dirties the folder every time it runs is worse than a slow one.
    sys.path.insert(0, str(os_.root / ".os"))
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        import learn as L
    finally:
        sys.dont_write_bytecode = was

    as_json = _flag(argv, "--json")
    want_list = _flag(argv, "--list", "-l")
    show_cache = _flag(argv, "--cached")
    force = _flag(argv, "--force")
    limit = int(_opt(argv, "--limit", "0") or L.LIST_LIMIT)
    forget = _flag(argv, "--forget")
    rest = _theirs(argv)      # a YouTube id can begin with a dash

    def no_ytdlp() -> int:
        line = L.install_hint()
        if as_json:
            print(json.dumps({"ok": False, "why": "yt-dlp not installed",
                              "install": line}))
            return 1
        Out.title("learn", "needs one thing first")
        Out.warn("This is the one part of Zenith that goes out to the internet,")
        Out.raw("    and the one part that wants something installed.")
        Out.raw()
        Out.raw("    " + paint(line, S.B))
        Out.raw()
        Out.note("everything else in this folder works without it")
        return 1

    if show_cache:
        rows = L.inventory(os_.root)
        if as_json:
            print(json.dumps({"ok": True, "cached": rows}))
            return 0
        Out.title("cached", f"{len(rows)} source(s)")
        for r in rows:
            Out.item("·", f"{r['id']}  {r['words']:,} words")
        return 0

    if forget:
        gone = L.forget(os_.root, rest)
        if as_json:
            print(json.dumps({"ok": True, "forgotten": gone}))
            return 0
        Out.ok(f"forgot {gone} cached source(s)")
        return 0

    if not rest:
        die('what should I learn from?   ./os learn --list "<channel url>"')

    if not L.ytdlp():
        return no_ytdlp()

    try:
        if want_list:
            rows = L.listing(rest[0], limit)
            if as_json:
                print(json.dumps({"ok": True, "videos": rows}))
                return 0
            Out.title("what's there", f"{len(rows)} video(s)")
            for r in rows:
                views = f"{r['views']:,}" if r["views"] else "—"
                mins = f"{r['minutes']}m" if r["minutes"] else "—"
                Out.item("·", f"{r['id']}  {views:>12} views  {mins:>5}  {r['title'][:60]}")
            Out.raw()
            Out.note("pick the ones worth reading, then:  ./os learn <id> <id> …")
            return 0

        results = L.transcripts(os_.root, rest, force)
    except RuntimeError as exc:
        if "no-ytdlp" in str(exc):
            return no_ytdlp()
        die(str(exc))
    except subprocess.TimeoutExpired:
        die("that took too long — try one video at a time")

    if as_json:
        print(json.dumps({"ok": True, "sources": results}))
        return 0
    got = [r for r in results if r.get("ok")]
    Out.title("learned from", f"{len(got)} of {len(results)}")
    for r in results:
        if r.get("ok"):
            Out.ok(f"{r['id']}  {r['words']:,} words"
                   + ("  (already had it)" if r.get("cached") else ""))
        else:
            Out.warn(f"{r.get('id', r.get('url', '?'))} — {r.get('why')}")
    if got:
        Out.raw()
        Out.note("read them from " + os_.rel(L.cache_dir(os_.root)))
    return 0


def cmd_snag(os_: Zenith, argv: list[str]) -> int:
    """Write down something wrong with *this folder*, as opposed to their work.

    The person using a template is the only one who finds out what is wrong
    with it, and they find out mid-sentence, while doing something else —
    which is exactly when nobody stops to file a bug report. So the AI writes
    it down as it happens, in one command, and says nothing. Later, `--export`
    turns the pile into a page that can be handed to whoever maintains the
    template, with the repeats counted: the same snag hit six times is a
    different priority from one hit once, and that count is the only piece of
    evidence a maintainer cannot get any other way.

    Deliberately not a note. Their `Notes/` is theirs; this is about the
    machinery, it lives in `.os/`, and it never shows up in a search for
    their own work."""
    as_json = _flag(argv, "--json")
    clear = _flag(argv, "--clear")
    # Clearing always writes them out first. Rule 2 of this folder is that it
    # does not delete what somebody wrote, and a snag is something they wrote.
    export = _flag(argv, "--export") or clear
    text = " ".join(_theirs(argv)).strip().strip('"')

    store = os_.dot / "snags.json"
    try:
        snags = json.loads(store.read_text(encoding="utf-8"))
        if not isinstance(snags, list):
            snags = []
    except (OSError, ValueError):
        snags = []

    def key(t: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", t.lower())).strip()

    if text:
        threshold = float(os_.thresholds.get("duplicate_similarity", 0.86))
        mine = key(text)
        for snag in snags:
            if difflib.SequenceMatcher(None, mine, key(snag["text"])).ratio() >= threshold:
                snag["times"] = int(snag.get("times", 1)) + 1
                snag["last"] = today()
                break
        else:
            snags.append({"text": re.sub(r"\s+", " ", text)[:400], "times": 1,
                          "first": today(), "last": today(),
                          "version": ENGINE_VERSION})
        write_text(store, json.dumps(snags, indent=2, ensure_ascii=False) + "\n")
        if as_json:
            print(json.dumps({"ok": True, "snags": len(snags)}))
            return 0
        # Quiet on purpose: this is bookkeeping about the tool, and the person
        # was in the middle of something else when it happened.
        Out.note(f"noted about this folder — {len(snags)} so far, ./os snag to read them")
        return 0

    ranked = sorted(snags, key=lambda x: (-int(x.get("times", 1)), x.get("first", "")))

    if export:
        lines = [f"# What using {os_.config.get('name', 'Zenith')} turned up",
                 "",
                 f"{len(ranked)} thing{'' if len(ranked) == 1 else 's'}, "
                 f"most-repeated first, "
                 f"from engine {ENGINE_VERSION}. Written by `./os snag --export` "
                 f"on {today()}.", ""]
        if not ranked:
            lines.append("Nothing yet. Either it is working, or nobody is writing it down.")
        for snag in ranked:
            times = int(snag.get("times", 1))
            when = (f"{snag.get('first')}" if times == 1
                    else f"{times}x, {snag.get('first')} → {snag.get('last')}")
            lines += [f"## {snag['text']}", "", f"_{when} · engine {snag.get('version', '?')}_", ""]
        out = os_.root / "template-feedback.md"
        write_text(out, "\n".join(lines).rstrip() + "\n")
        if clear:
            write_text(store, "[]\n")     # written out, so safe to put away
        if as_json:
            print(json.dumps({"ok": True, "wrote": os_.rel(out),
                              "snags": len(ranked), "cleared": clear}))
            return 0
        Out.title("snags", f"{len(ranked)} written out")
        Out.ok(os_.rel(out))
        if clear:
            Out.note(f"{len(ranked)} cleared — the file above is the record now")
        Out.raw()
        Out.note("hand that file to whoever maintains this template")
        Out.raw()
        return 0

    if as_json:
        print(json.dumps({"ok": True, "snags": ranked}, indent=2))
        return 0
    if not ranked:
        Out.title("snags", "nothing yet")
        Out.note('./os snag "<what got in the way>"   writes one down')
        Out.raw()
        return 0
    Out.title("snags", f"{len(ranked)} about this folder")
    for snag in ranked:
        times = int(snag.get("times", 1))
        Out.item("·", trunc(snag["text"], 62)
                 + paint(f"   {times}x" if times > 1 else "", S.AMBER))
    Out.raw()
    Out.note("./os snag --export   writes them out as a page to hand over")
    Out.raw()
    return 0


def cmd_words(os_: Zenith, argv: list[str]) -> int:
    """The vocabulary this folder files by, and how to add to it.

    Filing is word matching, so the words somebody actually uses are the single
    biggest lever on where their things land. `.os/words.json` is the one file
    they are invited to edit; this is the same thing without opening an editor,
    and it is how `/learn` hands back what a subject taught it. A command
    rather than a skill for the same reason as `learn`: every AI can reach it,
    not only the one holding the skill file — and `python3 .os/learn.py` is not
    something anybody should have to type."""
    sys.path.insert(0, str(os_.root / ".os"))
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        import learn as L
    finally:
        sys.dont_write_bytecode = was

    as_json = _flag(argv, "--json")
    rest = _theirs(argv)

    if not rest:
        rows = L.domains(os_.root)
        if as_json:
            print(json.dumps({"ok": True, "domains": rows}, indent=2))
            return 0
        Out.title("words", "what this folder files by")
        for row in rows:
            extra = f"+{row['learned']} learned" if row["learned"] else ""
            Out.raw("  " + paint(pad(row["domain"], 14), S.GOLD)
                    + paint(pad(f"{row['keywords']} words", 12), S.INK)
                    + paint(extra, S.JADE))
        Out.raw()
        Out.note('./os words <domain> "<a word you use>" …   teaches it more')
        Out.note("or open .os/words.json and add them to a keywords list yourself")
        Out.raw()
        return 0

    if len(rest) == 1:
        die('give me a domain and at least one word'
            '\n     ./os words marketing "ad set" "learning phase"'
            '\n     ./os words          lists the domains')

    result = L.teach(os_.root, rest[0], rest[1:])
    if as_json:
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if not result["ok"]:
        die(result["why"] + "\n     it knows: " + ", ".join(result["domains"]))

    Out.title("words", result["domain"])
    if result["added"]:
        Out.ok(f"{len(result['added'])} added — " + ", ".join(result["added"]))
    else:
        Out.note("nothing new — it knew all of those already")
    if result["already_known"] and result["added"]:
        Out.note(f"{len(result['already_known'])} it already knew")
    Out.raw()
    if result["added"]:
        Out.note("things you save about this will file themselves from now on")
        Out.raw()
    return 0


def cmd_brief(os_: Zenith, argv: list[str]) -> int:
    """What an AI is handed before the person has said anything."""
    text = _brief_text(os_)
    if _flag(argv, "--json"):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": text}}))
        return 0
    Out.raw()
    for line in text.split("\n"):
        Out.raw("  " + paint(line, S.MUTE if line.startswith("- ") else S.INK))
    Out.raw()
    return 0


def cmd_test(os_: Zenith, argv: list[str]) -> int:
    """Run the suite against a throwaway copy. Never touches this folder."""
    runner = os_.dot / "tests" / "run.py"
    if not runner.exists():
        die("the test suite is not installed (.os/tests/run.py is missing)")
    proc = subprocess.run([sys.executable, str(runner), *argv], cwd=str(os_.root))
    return proc.returncode


def cmd_setup(os_: Zenith, argv: list[str]) -> int:
    """Make a freshly-copied folder belong to whoever just opened it."""
    quiet = _flag(argv, "--quiet-welcome")
    label_opt = _opt(argv, "--name")
    owner = _opt(argv, "--owner") or " ".join(_theirs(argv)).strip()
    with Lock(os_, "setup"):
        result = os_.initialise(owner=owner.strip('"'), name=label_opt)
        Doctor(os_).run(fix=True)
        Indexer(os_).build()
    if quiet:
        return 0
    label = result["name"] or "Zenith"
    Out.raw(paint(f"\n  {' '.join(label.upper())}", S.B, S.GOLD))
    Out.raw("  " + paint("─" * max(10, len(label) * 2 - 1), S.FAINT))
    Out.raw()
    who = f"{result['owner']}'s folder." if result["owner"] else "This folder is yours now."
    Out.raw("  " + paint(who + " Everything you make lives here.", S.INK))
    Out.raw()
    Out.raw("  " + paint("TRY THIS", S.B, S.GOLD))
    for cmd, why in (
        ('./os save "anything on your mind"', "I put it somewhere sensible"),
        ("./os", "see where everything stands"),
        ("claude", "or any AI — then just talk to it normally"),
    ):
        Out.raw("    " + paint(pad(cmd, 36), S.GOLD) + paint(why, S.FAINT))
    Out.raw()
    Out.raw("  " + paint("./os demo", S.MUTE)
            + paint("   two minutes, shows you the whole idea", S.FAINT))
    Out.raw()
    return 0


DEMO_ITEMS = [
    ("The billing service token refresh fails every Friday night. Has to be fixed "
     "before the release on the 14th. First step is reproducing it on staging.",
     "this one has a next action"),
    ("Notes on how the tags work here: the letter says what a thing is and the "
     "rest is a counter, so N.03 is the third thing I wrote down. Nothing to do "
     "— just worth keeping.",
     "this one is just worth keeping"),
    ("Keep the codebase green: no failing tests, no lint errors, checked every "
     "week. This never ends, it is just something I hold to.",
     "this one is just kept level"),
]


def cmd_demo(os_: Zenith, argv: list[str]) -> int:
    """Show the whole idea on three throwaway items, then put the folder back."""
    keep = _flag(argv, "--keep")
    loose = [i for i in Scanner(os_).scan() if Sorter.unmanaged(i)]
    if loose and not keep:
        die(f"you have {len(loose)} thing(s) dropped in but not filed, and the demo "
            "would sweep them up with its own.\n"
            "     Run  ./os sort  to file them first, then try the demo again.")

    def step(n, title):
        Out.raw()
        Out.raw("  " + paint(str(n), S.B, S.GOLD) + paint("  " + title, S.B, S.INK))

    Out.raw()
    Out.raw("  " + paint("TWO MINUTES", S.B, S.GOLD))
    Out.raw("  " + paint("Three things go in. You decide nothing. They all end up "
                         "somewhere sensible.", S.FAINT))
    if not keep:
        Out.raw("  " + paint("Everything the demo makes is removed again at the end.", S.FAINT))

    with Lock(os_, "demo"):
        creator = Creator(os_)
        step(1, "Write three things down. No folder, no title, no tags.")
        for text, why in DEMO_ITEMS:
            creator.capture(text, source="demo")
            Out.raw("    " + paint("→ ", S.GOLD) + paint(trunc(text, 62), S.INK))
            Out.raw("      " + paint(why, S.FAINT))

        step(2, "Watch where they go.")
        result = Sorter(os_).run()
        os_.commit("demo")
        Indexer(os_).build()
        # Work is one kind in two phases, so the label has to read the phase
        # off the filed item — that distinction is the whole point of step 2.
        phase_of = {os_.rel(i.path): i.status for i in Scanner(os_).scan()}
        for kind, _src, dst in result["moves"]:
            word = KIND_WORDS.get(kind, kind)
            if kind == "project":
                word = ("work you're pushing" if phase_of.get(dst) != HOLDING
                        else "work you keep up")
            Out.raw("    " + paint(pad(word, 20), S.GOLD)
                    + paint("→ ", S.FAINT) + paint(trunc(dst, 50), S.INK))

        step(3, "Find one again, without remembering where it went.")
        Out.raw("    " + paint('./os find "token refresh"', S.FAINT))
        for _score, item, _sn in Finder(os_).search("token refresh", limit=1):
            Out.raw("    " + paint(pad(item.ident or "—", 7), S.GOLD)
                    + paint(item.title[:56], S.B))

        step(4, "Change your mind about all of it, at once.")
        if keep:
            Out.raw("    " + paint("skipped — you passed --keep, so the three stay filed", S.AMBER))
        else:
            Out.raw("    " + paint("./os undo", S.FAINT))
            outcome = Undo(os_).revert()
            stage = Creator(os_).stage()
            for leftover in list(stage.iterdir()):
                if not ignored(leftover) and "source: demo" in read_text(leftover, 400):
                    leftover.unlink()
            Indexer(os_).build()
            Out.raw("    " + paint(f"✓ all {outcome['restored']} changes reversed — the "
                                   "three demo items are gone", S.JADE))
            Out.raw("    " + paint("(nothing of yours was touched — the demo only "
                                   "ever handles its own three)", S.FAINT))

    Out.raw()
    Out.raw("  " + paint("THAT IS THE WHOLE THING", S.B, S.GOLD))
    Out.raw("    " + paint("Say it. It gets filed. You find it later. You can always undo.", S.INK))
    Out.raw()
    Out.raw("    " + paint(pad('./os save "..."', 24), S.GOLD)
            + paint("put something real in", S.FAINT))
    Out.raw("    " + paint(pad("claude", 24), S.GOLD)
            + paint("or any AI — just talk to it", S.FAINT))
    Out.raw()
    return 0


def cmd_edit(os_: Zenith, argv: list[str]) -> int:
    """Open an item in $EDITOR, or in whatever the desktop uses."""
    if not argv or not argv[0].strip():
        die("which one?   ./os edit W.04      (run ./os to see the numbers)")
    item = Finder(os_).by_id(argv[0])
    if item is None:
        die(f"nothing here is numbered {argv[0]} — try  ./os find {argv[0]}")
    target = item.spine or item.path
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        return subprocess.run([*editor.split(), str(target)]).returncode
    if sys.platform == "darwin":
        subprocess.run(["open", str(target)], check=False)
        Out.title("edit")
        Out.ok(os_.rel(target))
        Out.note("set $EDITOR to open it in your terminal editor instead")
        Out.raw()
        return 0
    print(str(target))
    return 0


COMMAND_LIST = [
    ("save", "write something down — it gets filed for you"),
    ("find", "search everything you have ever saved"),
    ("show", "look at one thing without opening it"),
    ("open", "show where something is on disk"),
    ("new", "start work, an ongoing thing, a note, a skill"),
    ("hold", "no next action — just keep it level"),
    ("push", "put it back on the go"),
    ("close", "no longer live — put it away in the archive"),
    ("back", "get it out of the archive again"),
    ("undo", "take back the last thing it did"),
    ("sort", "file anything you dropped in by hand"),
    ("check", "is anything broken?"),
    ("tidy", "what has gone stale, doubled up or unfiled"),
    ("backup", "zip a copy of everything"),
    ("edit", "open it in your text editor"),
    ("demo", "two-minute tour, then puts everything back"),
    ("name", "put your name on this folder"),
    ("help", "everything you can type"),
]


def _completion(shell: str) -> str:
    names = " ".join(n for n, _ in COMMAND_LIST)
    if "bash" in shell:
        return """# Zenith tab-completion for bash.  Install with:
#   ./os completion bash >> ~/.bashrc
_os_complete() {
  local cur prev
  cur="${COMP_WORDS[COMP_CWORD]}"; prev="${COMP_WORDS[COMP_CWORD-1]}"
  if [ "$COMP_CWORD" -eq 1 ]; then
    COMPREPLY=( $(compgen -W "%s" -- "$cur") ); return
  fi
  case "$prev" in
    new)   COMPREPLY=( $(compgen -W "work ongoing note skill agent" -- "$cur") ) ;;
    sort)  COMPREPLY=( $(compgen -W "--dry-run --json" -- "$cur") ) ;;
    check) COMPREPLY=( $(compgen -W "--fix --json" -- "$cur") ) ;;
    help)  COMPREPLY=( $(compgen -W "%s" -- "$cur") ) ;;
    *)     COMPREPLY=( $(compgen -f -- "$cur") ) ;;
  esac
}
complete -F _os_complete os
""" % (names, names)
    listed = "\n".join(f"    '{n}:{d}'" for n, d in COMMAND_LIST)
    return """#compdef os
# Zenith tab-completion for zsh.  Install with:
#   ./os completion zsh > ~/.zsh/completions/_os     (then run: compinit)
_os() {
  local -a cmds
  cmds=(
%s
  )
  if (( CURRENT == 2 )); then _describe -t commands 'os' cmds; return; fi
  case ${words[2]} in
    new) _values 'kind' work ongoing note skill agent ;;
    open|edit|done|back) _message 'a number like W.04' ;;
    sort) _values 'flag' --dry-run --json ;;
    check) _values 'flag' --fix --json ;;
    help) _describe -t commands 'os' cmds ;;
    *) _files ;;
  esac
}
_os "$@"
""" % listed


def cmd_completion(os_: Zenith, argv: list[str]) -> int:
    print(_completion((argv[0] if argv else os.environ.get("SHELL", "")).lower()))
    return 0


def cmd_name(os_: Zenith, argv: list[str]) -> int:
    """Put your name on the folder, or call the folder something else."""
    label = _opt(argv, "--name")
    owner = " ".join(_theirs(argv)).strip().strip('"')
    if not owner and not label:
        Out.title("name")
        Out.kv("this folder", os_.config.get("name", "Zenith"), 14)
        Out.kv("belongs to", os_.config.get("owner") or "nobody yet", 14)
        Out.raw()
        Out.note('./os name "Your Name"          put your name on it')
        Out.note('./os name --name "Studio"      call the folder something else')
        Out.raw()
        return 0
    if owner:
        os_.config["owner"] = owner
    if label:
        os_.config["name"] = label
    os_.save_config()
    Indexer(os_).build()
    Out.title("name")
    Out.ok(f"{os_.config.get('name', 'Zenith')} · "
           f"{os_.config.get('owner') or 'no owner set'}")
    Out.raw()
    return 0


DETAIL = {
    "save": ('os save "<anything>"   |   os save <a file on your computer>',
             "Write something down. It works out what it is and puts it in the right "
             "folder straight away — you never pick one.",
             ['os save "the auth token breaks every Friday"',
              "os save ~/Downloads/pricing-deck.pdf"],
             "Wrong place? ./os undo puts it back, every time."),
    "new": ('os new <work|ongoing|note|skill|agent> "<name>"',
            "Start something from a blank template, already numbered. If you "
            "already have something by nearly the same name it stops and says so "
            "— add --anyway if you really want both.",
            ['os new work "Ship the redesign"',
             'os new ongoing "Keep the tests passing"',
             'os new note "How Postgres indexes work"',
             'os new skill "Draft the weekly invoice"'],
            "Work and ongoing are the same kind of thing in two phases: `work` "
            "starts it being pushed, `ongoing` starts it being held. Both live "
            "in Work/, and ./os hold and ./os push move one between them — "
            "which happens all the time, because work that ships becomes work "
            "you maintain. A skill is different: it is not work you are doing, "
            "it is a job you want *done the same way every time*, written down "
            "once so any AI opening this folder can just do it. If you catch "
            "yourself explaining the same steps twice, that's a skill. (A helper "
            "— ./os new helper — is for a big job that deserves its own clean "
            "context, like research or a review.) And ./os save already makes "
            "work when your words read like work, so you rarely need this one."),
    "snag": ('os snag "<what got in the way>"   |   os snag --export',
             "Write down something wrong with this folder itself — a command that "
             "did the surprising thing, a rule that made no sense, a step that "
             "should have been automatic. Not your work: the machinery. Your AI "
             "writes these as it hits them, so you do not have to notice.",
             ['os snag "sort filed a photo as a project"',
              "os snag",
              "os snag --export"],
             "--export writes template-feedback.md at the top of this folder, "
             "most-repeated first, ready to hand to whoever maintains the "
             "template. The count matters: the same snag six times is a "
             "different job from one seen once. --clear writes that page first "
             "and then empties the pile, so nothing you wrote is ever simply "
             "gone. None of it is mixed in with your own notes, and none of it "
             "leaves this folder on its own."),
    "words": ('os words   |   os words <domain> "<a word you use>" …',
              "Show the vocabulary this folder files by, and add to it. Where "
              "something lands is decided by matching words, so the fastest way "
              "to make it better at your work is to give it the words you "
              "actually use — your clients, your projects, the jargon of your "
              "trade.",
              ["os words",
               'os words marketing "ad set" "learning phase"',
               'os words engineering "northwind" "the flimbus service"'],
              "It only ever adds to a `learned` list, so the keywords you wrote "
              "yourself in .os/words.json are never touched and anything added "
              "here can be deleted without disturbing them. /learn writes to it "
              "at the end of studying a subject, which is why filing gets better "
              "at a subject once you have learned one."),
    "hold": ("os hold <id>   |   os push <id>",
             "Say what a piece of work needs from you now. Holding means there "
             "is no next action, only a standard you keep level — it stops being "
             "counted as on the go, and stops being nagged for going quiet. "
             "Pushing puts it back on the go.",
             ["os hold W.04", "os push W.04"],
             "Nothing moves on disk; it is one word in the file's header. That is "
             "the point — the same job flips between the two over and over, and "
             "no filing system should make you shuffle folders for that."),
    "find": ("os find <words>",
             "Search names, numbers, tags and the full text of everything you "
             "have saved, archive included. Typos and plurals are fine — "
             "'meetings' finds 'meeting', and it will tell you when it searched "
             "for something other than what you typed. Skills and helpers are "
             "left out unless you ask for them with --kind skill.",
             ["os find token refresh", "os find billing --kind project",
              "os find weekly --kind skill"],
             "You don't have to remember where you put it, or spell it right. "
             "That is the whole point."),
    "show": ("os show <id>   |   os <id>",
             "Everything worth knowing about one thing — what state it is in, when "
             "you last touched it, its next action, what was decided — without "
             "opening the file. Typing the number on its own does the same.",
             ["os show W.04", "os W.04"],
             "./os edit <id> opens it properly when you want to change something."),
    "open": ("os open <id>", "Print where something lives, and show it to you in "
             "Finder, Explorer or your file manager. To open the file itself for "
             "editing, use ./os edit.",
             ["os open W.04"],
             "Numbers never change, even if you rename or move the file."),
    "edit": ("os edit <id>", "Open it in your text editor.",
             ["os edit W.04"], "Set $EDITOR to stay in the terminal."),
    "close": ("os close <id>   |   os back <id>",
              "Put something away in Archive/, or take it back out. Closed means "
              "no longer live — not necessarily finished. Things leave because you "
              "stopped carrying them, and that is as true of shipped work as of "
              "abandoned work.",
              ["os close W.04", "os back W.04"],
              "Nothing is deleted, and things in the archive still turn up in "
              "./os find. If it is not over, just quiet, ./os hold it instead."),
    "undo": ("os undo", "Reverse the last thing Zenith itself did — a save, a "
             "filing, a close, a new.",
             ["os undo"],
             "It restores where files went AND what they said, twenty steps back. "
             "It cannot undo edits you made by hand in a text editor — for those, "
             "use your editor's own undo."),
    "sort": ("os sort [--dry-run]",
             "Take charge of anything you dropped into a folder by hand: work out "
             "what it is, give it a number and a header, and shelve it. Also "
             "re-groups what is already filed as the folders fill up.",
             ["os sort --dry-run     # show me first, change nothing", "os sort"],
             "./os save files things the moment you say them, so this is for the "
             "times you dragged a pile of files in from Finder instead."),
    "check": ("os check [--fix]",
              "Look for anything broken: missing folders, repeated numbers, "
              "half-written skills, dead links.",
              ["os check", "os check --fix"],
              "--fix only repairs the mechanical. Anything needing a judgement call "
              "is reported, never guessed."),
    "tidy": ("os tidy",
             "What has gone stale, what is finished but still sitting around, and "
             "what looks like the same thing twice.",
             ["os tidy"], "Ten minutes a week is all this system asks of you."),
    "backup": ("os backup", "A dated zip of everything, into .os/backups/.",
               ["os backup"],
               "Keeps the newest few — how many is keep_backups in .os/config.json. "
               "Move one somewhere else now and then; a copy on the same disk is "
               "not really a backup."),
    "name": ('os name "<your name>"', "Put your name on this folder.",
             ['os name "Sam"', 'os name --name "Studio"'], ""),
    "demo": ("os demo [--keep]", "A two-minute tour on three throwaway items.",
             ["os demo"], "It undoes itself at the end unless you pass --keep."),
    "status": ("os   |   os status", "Where everything stands right now.",
               ["os"], "This is the default — just type ./os on its own."),
    "index": ("os index", "Rebuild INDEX.md and the search data.",
              ["os index"], "Happens on its own after a Claude Code session."),
    "learn": ('os learn --list "<channel>"   |   os learn <video> …',
              "Fetch what a source actually says and keep it, so an AI can learn "
              "from the words rather than guessing from the title.",
              ['os learn --list "youtube.com/@channel"', "os learn dQw4w9WgXcQ"],
              "The one command that goes out to the internet, and the one that "
              "wants yt-dlp installed. It fetches and cleans; deciding which "
              "sources are worth anything is the AI's job."),
    "brief": ("os brief", "What an AI is told about this folder before you speak.",
              ["os brief"],
              "Paste it into any AI that can't run the hook itself."),
    "test": ("os test [-v] [-k PATTERN]", "Run the test suite against a throwaway copy.",
             ["os test", "os test -k undo -v"],
             "It never touches the folder you run it from."),
    "completion": ("os completion [zsh|bash]", "Print a tab-completion script.",
                   ["os completion zsh > ~/.zsh/completions/_os"], ""),
}
for _alias, _real in (("back", "close"), ("restore", "close"), ("archive", "close"),
                      ("done", "close"), ("park", "close"),
                      ("push", "hold"), ("pushing", "hold"), ("holding", "hold"),
                      ("pause", "hold"), ("resume", "hold"),
                      ("capture", "save"), ("doctor", "check"), ("review", "tidy"),
                      ("setup", "name"), ("tour", "demo")):
    DETAIL.setdefault(_alias, DETAIL[_real])


def cmd_help(os_: Zenith | None, argv: list[str]) -> int:
    topic = (argv[0].lstrip("/") if argv else "").lower()
    if topic in DETAIL:
        usage, what, examples, note = DETAIL[topic]
        Out.title(topic)
        Out.raw("  " + paint(usage, S.GOLD))
        Out.raw()
        Out.raw("  " + paint(what, S.INK))
        if examples:
            Out.raw()
            for line in examples:
                Out.raw("    " + paint("$ ", S.FAINT) + paint(line, S.MUTE))
        if note:
            Out.raw()
            Out.raw("  " + paint(note, S.FAINT))
        Out.raw()
        return 0
    if topic:
        Out.warn(f"there is nothing called '{topic}'")
        Out.note("./os help   lists everything you can type")
        Out.raw()
        return 1
    name = os_.config.get("name", "Zenith") if os_ else "Zenith"
    tagline = os_.config.get("tagline", "") if os_ else ""
    mark = wordmark(name)
    print(paint(mark, S.GOLD) if S.enabled else mark)
    print(HELP.format(name=paint(name, S.B), tagline=paint(tagline, S.FAINT),
                      c1=(S.B + S.GOLD) if S.enabled else "", c0=S.RESET if S.enabled else ""))
    return 0


COMMANDS = {
    # the ones people actually type
    "": cmd_status, "status": cmd_status, "st": cmd_status,
    "save": cmd_save, "s": cmd_save, "capture": cmd_save, "add": cmd_save,
    "find": cmd_find, "f": cmd_find, "search": cmd_find,
    "open": cmd_open, "o": cmd_open,
    "show": cmd_show, "view": cmd_show, "look": cmd_show,
    "new": cmd_new, "n": cmd_new, "start": cmd_new,
    "close": cmd_close, "done": cmd_close, "archive": cmd_close, "finish": cmd_close,
    "park": cmd_close,
    "hold": cmd_hold, "holding": cmd_hold, "pause": cmd_hold,
    "push": cmd_push, "pushing": cmd_push, "resume": cmd_push,
    "back": cmd_back, "restore": cmd_back, "unarchive": cmd_back,
    "undo": cmd_undo, "oops": cmd_undo,
    # housekeeping
    "sort": cmd_sort, "file": cmd_sort,
    "check": cmd_doctor, "doctor": cmd_doctor, "fix": cmd_doctor,
    "tidy": cmd_review, "review": cmd_review, "cleanup": cmd_review,
    "backup": cmd_backup, "snapshot": cmd_backup,
    "edit": cmd_edit, "e": cmd_edit,
    "demo": cmd_demo, "tour": cmd_demo,
    "name": cmd_name, "setup": cmd_setup, "init": cmd_setup,
    # plumbing
    "index": cmd_index, "reindex": cmd_index,
    "brief": cmd_brief,
    "learn": cmd_learn,
    "words": cmd_words,
    "snag": cmd_snag,
    "test": cmd_test, "selftest": cmd_test,
    "completion": cmd_completion,
    "help": cmd_help, "-h": cmd_help, "--help": cmd_help,
}

#: A near miss should teach, not scold.
NEAR_MISS = {
    "list": "find", "look": "find", "ls": "status", "show": "status",
    "remember": "save", "write": "save", "note": 'new note "..."',
    "delete": "close", "remove": "close", "rm": "close", "trash": "close",
    "done": "close", "finish": "close", "complete": "close",
    "maintain": "hold", "keep": "hold", "park": "close", "unpause": "push",
    "clean": "tidy", "organize": "sort", "organise": "sort", "health": "check",
    "stats": "status", "dash": "status", "dashboard": "status",
    "guide": "help", "manual": "help", "link": "check --fix", "repair": "check --fix",
    "vocab": "words", "vocabulary": "words", "keywords": "words", "taxonomy": "words",
    "bug": "snag", "issue": "snag", "complain": "snag", "annoying": "snag",
}


#: Every option each command understands, by the function that handles it.
#: Anything else stops the run rather than being quietly dropped: `os sort
#: --dry` was somebody asking for a preview and getting the real thing, which
#: is the one mistake a dry-run flag exists to prevent. `None` means the
#: command hands its arguments to something else and cannot vet them. The
#: global options (--root, --quiet, --no-color, --version) are taken in main()
#: before any command sees them. `test_no_option_is_silently_ignored` reads the
#: source and fails if this table drifts out of step with it.
FLAGS: dict[str, set[str] | None] = {
    "cmd_back": set(),
    "cmd_backup": set(),
    "cmd_brief": {"--json"},
    "cmd_close": set(),
    "cmd_completion": set(),
    "cmd_demo": {"--keep"},
    "cmd_doctor": {"--fix", "--json"},
    "cmd_edit": set(),
    "cmd_find": {"--in", "--json", "--kind", "--limit"},
    "cmd_help": set(),
    "cmd_hold": set(),
    "cmd_index": {"--json", "--notify"},
    "cmd_learn": {"--cached", "--force", "--forget", "--json", "--limit", "--list", "-l"},
    "cmd_name": {"--name"},
    "cmd_new": {"--anyway", "--domain", "--force", "--tags"},
    "cmd_open": set(),
    "cmd_push": set(),
    "cmd_review": {"--json"},
    "cmd_save": {"--file", "--json"},
    "cmd_setup": {"--name", "--owner", "--quiet-welcome"},
    "cmd_show": set(),
    "cmd_sort": {"--dry-run", "--json", "-n"},
    "cmd_status": {"--json"},
    "cmd_test": None,      # forwards everything to the suite
    "cmd_undo": set(),
    "cmd_words": {"--json"},
    "cmd_snag": {"--clear", "--export", "--json"},
}

#: Commands whose arguments are the person's own words. Told about an option it
#: does not know, one of these also says how to keep words beginning with a dash.
FREE_TEXT = {"cmd_save", "cmd_new", "cmd_find", "cmd_name"}


def _stray_options(argv: list[str], known: set[str]) -> list[str]:
    """Options a command was handed and does not understand.

    Deliberately narrow, because the arguments to `os save` are whatever
    somebody typed. A double dash is an option — nobody writes a thought
    starting `--`. A single dash only counts when it is one bare letter:
    `-3 degrees and no heating` is the weather, and `-Xf12o4jt4` names a
    video. Everything after a lone `--` is content by definition."""
    out: list[str] = []
    for token in argv:
        if token == "--":
            break
        if " " in token or not token.startswith("-"):
            continue
        if token.startswith("--"):
            name = token.split("=", 1)[0]
            if name not in known:
                out.append(name)
        elif re.fullmatch(r"-[A-Za-z]", token) and token not in known:
            out.append(token)
    return out


def main(argv: list[str] | None = None) -> int:
    # Before anything is printed, including the errors below.
    speak_utf8()
    argv = list(sys.argv[1:] if argv is None else argv)
    if _flag(argv, "--version", "-V"):
        print(f"Zenith {ENGINE_VERSION}")
        return 0
    no_color = _flag(argv, "--no-color", "--plain")
    Out.quiet = _flag(argv, "--quiet", "-q")
    root_opt = _opt(argv, "--root")
    # Settle styling before anything can fail. Finding the root and reading the
    # settings files both happen below and both can `die()` — and those are the
    # messages most likely to be read out of a redirected log, where raw escape
    # codes are exactly what NO_COLOR exists to prevent. The real preference
    # from .os/config.json is applied further down, once it can be read.
    S.setup("never" if no_color else "auto")

    command = argv[0] if argv and not argv[0].startswith("-") else ""
    rest = argv[1:] if command else argv

    if command in ("help", "-h", "--help"):
        try:
            os_ = Zenith(find_root(Path(root_opt) if root_opt else None))
        except SystemExit:
            os_ = None
        S.setup("never" if no_color else "auto")
        return cmd_help(os_, rest)

    handler = COMMANDS.get(command)
    if handler is None and re.fullmatch(r"([A-Za-z]|\d{1,2})\.\d{1,4}", command):
        handler, rest = cmd_show, [command, *rest]   # `./os W.04` shows item W.04
    if handler is None:
        S.setup("never" if no_color else "auto")
        hint = NEAR_MISS.get(command) or next(
            iter(difflib.get_close_matches(
                command, [c for c in COMMANDS if c and not c.startswith("-")], 1, 0.6)), "")
        die(f"there is no './os {command}'."
            + (f"   Did you mean  ./os {hint}  ?" if hint else "")
            + "\n     ./os help   lists everything")

    known = FLAGS.get(handler.__name__, set())
    stray = _stray_options(rest, known) if known is not None else []
    if stray:
        S.setup("never" if no_color else "auto")
        spoken = f"./os {command}" if command else "./os"
        hint = next(iter(difflib.get_close_matches(stray[0], sorted(known), 1, 0.6)), "")
        lines = [f"{spoken} doesn't understand {stray[0]}."
                 + (f"   Did you mean  {hint}  ?" if hint else ""),
                 "     " + (f"it takes {', '.join(sorted(known))}"
                            if known else "it takes no options")]
        if handler.__name__ in FREE_TEXT and not hint:
            lines.append("     words of your own that start with a dash go after a bare --")
        lines.append(f"     ./os help {command}" if command else "     ./os help")
        die("\n".join(lines), 2)

    root = find_root(Path(root_opt).expanduser() if root_opt else None)
    ensure_runnable(root)
    os_ = Zenith(root)
    S.setup("never" if no_color else os_.behaviour.get("colour", "auto"))

    # A template is built on one day and opened on another. The first command in
    # a fresh copy quietly re-dates it, so nothing arrives looking stale.
    if os_.is_fresh() and handler not in (cmd_setup, cmd_help):
        try:
            os_.initialise()
            Indexer(os_).build()
            Out.raw()
            Out.raw("  " + paint("Welcome — this folder is yours now.", S.GOLD)
                    + paint("   ./os demo", S.FAINT)
                    + paint(" shows you the whole idea in two minutes.", S.FAINT))
        except OSError:
            pass

    try:
        return handler(os_, rest)
    except KeyboardInterrupt:
        Out.raw()
        Out.warn("stopped — nothing was left half-moved, and ./os undo still works")
        return 130
    except BrokenPipeError:
        return 0
    except OSError as exc:
        # The disk saying no is not a bug in this program, and a Python
        # traceback is the least useful way to say "that folder is read-only".
        # Anything that is *not* an OSError still raises: a real defect should
        # stay loud, and the trace is what makes it reportable.
        where = getattr(exc, "filename", "") or ""
        Out.raw()
        Out.bad(f"the disk would not let me finish: {exc.strerror or exc}")
        if where:
            Out.note(relative_to_root(Path(where), root))
        Out.note("nothing was left half-moved — ./os undo reverses whatever did happen")
        Out.raw()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
