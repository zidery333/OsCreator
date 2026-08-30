#!/usr/bin/env python3
"""
Zenith — test suite
========================
Builds a throwaway copy of the real folder in a temp directory, throws a
realistic mess at it, and checks that every promise the product makes holds.

    python3 .os/tests/run.py            run everything
    python3 .os/tests/run.py -v         show each assertion
    python3 .os/tests/run.py -k scale   run matching tests only
    python3 .os/tests/run.py --keep     leave the sandbox on disk for inspection

Nothing here touches the folder it is run from.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

# The suite imports the engine out of the template itself. Left alone that drops
# __pycache__/ into the folder being shipped, so every `./os test` dirties it.
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SOURCE / ".os"))

import fixtures  # noqa: E402


def pathlib_stem(name: str) -> str:
    return Path(name).stem
import engine  # noqa: E402
import learn  # noqa: E402

VERBOSE = False
G, R, Y, B, D, X = "\033[38;5;72m", "\033[38;5;167m", "\033[38;5;215m", "\033[1m", "\033[38;5;240m", "\033[0m"
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    G = R = Y = B = D = X = ""


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

class Failure(AssertionError):
    pass


class Case:
    def __init__(self, box: "Sandbox"):
        self.box = box
        self.checks = 0

    def ok(self, condition, message: str) -> None:
        self.checks += 1
        if not condition:
            raise Failure(message)
        if VERBOSE:
            print(f"      {G}·{X} {D}{message}{X}")

    def eq(self, got, want, message: str) -> None:
        self.ok(got == want, f"{message} (got {got!r}, want {want!r})")

    def gte(self, got, want, message: str) -> None:
        self.ok(got >= want, f"{message} (got {got!r}, need >= {want!r})")

    def lte(self, got, want, message: str) -> None:
        self.ok(got <= want, f"{message} (got {got!r}, need <= {want!r})")


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


class Sandbox:
    """A complete, isolated copy of the OS."""

    SKIP = {"Work", "Notes", "Archive",
            "backups", "cache", "transcripts", "__pycache__",
            "registry.json", "state.json", "INDEX.md", ".git"}

    def __init__(self, name: str = "zenith-test"):
        self.tmp = Path(tempfile.mkdtemp(prefix=name + "-"))
        self.root = self.tmp / "Zenith"
        self._build()

    def _build(self) -> None:
        def ignore(directory, names):
            drop = set()
            for n in names:
                if n in self.SKIP or n.endswith(".zip") or n.endswith(".tmp~"):
                    drop.add(n)
            return drop

        shutil.copytree(SOURCE, self.root, ignore=ignore, symlinks=True)
        for bucket in ("Work", "Notes", "Archive"):
            (self.root / bucket).mkdir(parents=True, exist_ok=True)
        (self.root / ".os" / "backups").mkdir(parents=True, exist_ok=True)
        (self.root / ".os" / "cache").mkdir(parents=True, exist_ok=True)
        state = self.root / ".os" / "state.json"
        state.write_text(json.dumps(
            {"counters": {}, "undo": [], "history": [], "created": "test"}, indent=2))

    # -- driving the real CLI ------------------------------------------------

    def run(self, *args: str, expect: int | None = 0) -> subprocess.CompletedProcess:
        env = dict(os.environ, ZENITH_HOME=str(self.root), NO_COLOR="1")
        proc = subprocess.run(
            [str(self.root / "os"), *args],
            capture_output=True, text=True, cwd=str(self.root), env=env, timeout=180,
        )
        if expect is not None and proc.returncode != expect:
            raise Failure(
                f"`os {' '.join(args)}` exited {proc.returncode}, expected {expect}\n"
                f"--- stdout ---\n{proc.stdout[-2500:]}\n--- stderr ---\n{proc.stderr[-2500:]}"
            )
        return proc

    def json(self, *args: str, expect: int | None = 0) -> object:
        proc = self.run(*args, "--json", expect=expect)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise Failure(f"`os {' '.join(args)} --json` did not emit JSON: {exc}\n{proc.stdout[:800]}")

    # -- inspection ----------------------------------------------------------

    GENERATED_FILES = {"INDEX.md", "_index.md", "CATALOG.md"}

    def fill_inbox(self, n: int) -> list[tuple[str, str, str]]:
        """Drop n unfiled items straight into the folders, the way a person
        dragging a pile in from Finder would. Returns (marker, content, bucket).

        There is no inbox any more: `os save` files as it writes, and anything
        put in by hand is adopted where it lies by `os sort`. Everything lands
        in Notes here — deciding it is work is the sorter's job, not the
        fixture's, and that decision is what half these tests are checking."""
        items = fixtures.bulk(n)
        for marker, body, _ in items:
            name = marker.split("::", 1)[1] if "::" in marker else f"{marker.lower()}.md"
            (self.root / "Notes" / name).write_text(body, encoding="utf-8")
        return items

    def locate(self, marker: str) -> Path | None:
        """Where did the item carrying this marker end up?"""
        needle = marker.split("::", 1)[1] if "::" in marker else marker
        for p in self.root.rglob("*"):
            if not p.is_file() or p.is_symlink():
                continue
            if "::" in marker:
                if engine.slugify(pathlib_stem(needle)) in engine.slugify(p.name):
                    return p
                continue
            if p.suffix.lower() not in engine.TEXT_SUFFIXES:
                continue
            try:
                if needle in p.read_text(encoding="utf-8", errors="replace"):
                    return p
            except OSError:
                continue
        return None

    def bucket_of(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).parts[0]
        except (ValueError, IndexError):
            return ""

    def registry(self) -> dict:
        return json.loads((self.root / ".os" / "registry.json").read_text())

    def items(self) -> list[dict]:
        return self.registry()["items"]

    def tree(self) -> dict[str, str]:
        """path -> content hash, for real content only.

        Generated maps (INDEX.md, _index.md, CATALOG.md) are derived state and
        are rebuilt on every index, so they are excluded — undo restores where
        things live, not files the OS regenerates from what it finds."""
        out = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(self.root)
            if rel.parts and rel.parts[0] == ".os":
                continue
            if p.name in self.GENERATED_FILES:
                continue
            out[str(rel)] = engine.digest(p)
        return out

    def dirs(self) -> set:
        """Every directory holding real content. `tree()` hashes files only, so
        an empty folder left behind by a bad undo is invisible to it."""
        out = set()
        for p in self.root.rglob("*"):
            if not p.is_dir() or p.is_symlink():
                continue
            rel = p.relative_to(self.root)
            if rel.parts and rel.parts[0] in (".os", ".git"):
                continue
            out.add(str(rel))
        return out

    def inbox_count(self) -> int:
        """How many things are sitting in a folder that the OS has not filed."""
        os_ = engine.Zenith(self.root)
        return len([i for i in engine.Scanner(os_).scan()
                    if engine.Sorter.unmanaged(i)])

    def destroy(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# the tests
# ---------------------------------------------------------------------------


@test
def test_fresh_folder_is_healthy(t: Case) -> None:
    """A brand new Zenith passes its own doctor."""
    result = t.box.json("doctor")
    errors = [i for i in result["issues"] if i["level"] == "error"]
    t.eq(errors, [], "a fresh folder reports no errors")
    t.gte(result["score"], 90, "fresh health score")
    t.ok((t.box.root / "AGENTS.md").exists(), "AGENTS.md ships with the folder")
    claude_md = t.box.root / "CLAUDE.md"
    t.ok(claude_md.exists(), "CLAUDE.md sits alongside AGENTS.md, where Claude Code looks")
    t.ok("@AGENTS.md" in claude_md.read_text(),
         "it imports AGENTS.md rather than repeating the rules")
    t.lte(len(claude_md.read_text().split("\n")), 20,
          "and stays a pointer, not a second copy of the rules")
    t.lte(len((t.box.root / "AGENTS.md").read_text().split("\n")), 160,
          "AGENTS.md stays short enough to load every turn")


@test
def test_shipped_extras_are_valid(t: Case) -> None:
    """Every skill and agent that ships is well-formed and safely named."""
    t.box.run("index")
    items = t.box.items()
    skills = [i for i in items if i["kind"] == "skill"]
    agents = [i for i in items if i["kind"] == "agent"]
    t.gte(len(skills), 5, "shipped skills")
    t.gte(len(agents), 3, "shipped helpers")
    t.lte(len(skills), 8, "shipped skills stay few enough to remember")

    for skill in skills:
        path = t.box.root / skill["path"] / "SKILL.md"
        t.ok(path.exists(), f"{skill['path']} has a SKILL.md")
        meta, body = engine.parse_frontmatter(path.read_text())
        t.ok(bool(meta.get("description")), f"{skill['path']} declares a description")
        t.gte(len(str(meta["description"])), 40, f"{skill['path']} description is substantive")
        name = Path(skill["path"]).name
        t.ok(name not in engine.RESERVED_COMMANDS,
             f"skill '{name}' does not collide with a built-in command")
        t.ok(name == engine.slugify(name), f"skill '{name}' is lowercase-with-hyphens")
        t.ok(len(body.split("\n")) <= 120, f"skill '{name}' body stays under 120 lines")

    for agent in agents:
        meta, _ = engine.parse_frontmatter((t.box.root / agent["path"]).read_text())
        for field in ("name", "description"):
            t.ok(bool(meta.get(field)), f"{agent['path']} declares {field}")
        t.eq(engine.slugify(str(meta["name"])), Path(agent["path"]).stem,
             f"{agent['path']} name matches its filename")
        if meta.get("model"):
            t.ok(str(meta["model"]) in ("sonnet", "opus", "haiku", "fable", "inherit"),
                 f"{agent['path']} declares a real model")


@test
def test_settings_and_hooks_are_wired(t: Case) -> None:
    """settings.json is valid, points at hooks that exist and behave."""
    settings = json.loads((t.box.root / ".claude" / "settings.json").read_text())
    events = settings["hooks"]
    for event in ("SessionStart", "PostToolUse", "Stop"):
        t.ok(event in events, f"{event} hook is configured")

    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(t.box.root))
    hooks = t.box.root / ".claude" / "hooks"
    for spec in events.values():
        for group in spec:
            for hook in group["hooks"]:
                path = Path(hook["command"].replace("${CLAUDE_PROJECT_DIR}", str(t.box.root)))
                t.ok(path.exists(), f"hook {path.name} exists")
                t.ok(os.access(path, os.X_OK), f"hook {path.name} is executable")

    payloads = {
        "session-start.sh": '{"hook_event_name":"SessionStart"}',
        "mark-dirty.sh": "{}",
        "settle.sh": "{}",
    }
    for name, payload in payloads.items():
        proc = subprocess.run([str(hooks / name)], input=payload, capture_output=True,
                              text=True, env=env, timeout=60)
        t.eq(proc.returncode, 0, f"hook {name} exits 0")
        if proc.stdout.strip():
            json.loads(proc.stdout)
            t.ok(True, f"hook {name} emits valid JSON")

    # a hook must survive garbage without failing the session
    for name in payloads:
        proc = subprocess.run([str(hooks / name)], input="}{ not json", capture_output=True,
                              text=True, env=env, timeout=60)
        t.eq(proc.returncode, 0, f"hook {name} survives malformed input")

    brief = json.loads(subprocess.run([str(hooks / "session-start.sh")], input="{}",
                                      capture_output=True, text=True, env=env).stdout)
    t.ok("additionalContext" in brief["hookSpecificOutput"],
         "the session hook hands the AI something to read")


@test
def test_save_files_in_one_step(t: Case) -> None:
    """`os save` never asks a question, and never leaves anything pending.

    There is no drop folder to leave it in: saving and filing are one step, so
    "I saved it" and "it is filed" cannot come apart."""
    result = t.box.json("save", "Fix the auth token refresh bug, it bites every Friday "
                                "and has to be sorted before the release")
    t.ok(result["filed"], "save filed it rather than parking it")
    t.eq(t.box.inbox_count(), 0, "nothing was left unfiled")
    t.ok(bool(result["id"]), "it came back with a number")
    t.ok((t.box.root / result["saved"]).exists(), "the reported path is real")

    # nothing is left lying in the staging area either
    stage = t.box.root / ".os" / "cache" / engine.STAGING
    t.eq([p.name for p in stage.iterdir()] if stage.exists() else [], [],
         "and the staging file it wrote through is gone")

    # --hold went with the inbox: there is nowhere to hold something any more,
    # and an option that no longer exists says so rather than being swallowed
    held = t.box.run("save", "--hold", "deal with this later", expect=2)
    t.ok("--hold" not in held.stdout, "--hold is not offered back to the user")
    t.ok("doesn't understand" in held.stderr, "it is refused in words")
    t.eq(t.box.inbox_count(), 0, "and nothing was half-saved on the way out")

    # undo takes the save back out of the folders without destroying the words
    before = len([i for i in t.box.items() if i["kind"] in ("project", "note")])
    t.box.run("undo")
    after = len([i for i in t.box.items() if i["kind"] in ("project", "note")])
    t.lte(after, before, "undo removes what the save filed")


@test
def test_nothing_written_down_goes_unnoticed(t: Case) -> None:
    """A capture that never reached a folder has to be visible and has to stay.

    `os save` stages and files in one breath, so anything left in the staging
    area is a run that died in between. It used to be invisible — `os` said
    "nothing started yet", `check` said "all good" — and then deleted on a
    seven-day timer, which is the one thing this folder promises never to do."""
    stage = t.box.root / ".os" / "cache" / engine.STAGING
    stage.mkdir(parents=True, exist_ok=True)
    orphan = stage / "20200101-000000-ring-the-accountant.md"
    orphan.write_text("---\nsaved: 2020-01-01T00:00:00\n---\n\n"
                      "ring the accountant about the VAT thing\n", encoding="utf-8")
    old = time.time() - (engine.STALE_STAGE_DAYS + 30) * 86_400
    os.utime(orphan, (old, old))

    seen = t.box.run("status")
    t.ok("never got filed" in seen.stdout, "the first screen says it is there")
    t.ok("./os sort" in seen.stdout, "and what to type about it")

    health = t.box.json("check", expect=None)
    found = [i for i in health["issues"] if i["code"] == "unfiled-capture"]
    t.eq(len(found), 1, "check reports it as a finding of its own")
    t.eq(found[0]["level"], "error", "one left this long is an error, not a nag")
    t.ok("accountant" in found[0]["message"], "quoting the words, so it is recognisable")
    t.eq(found[0]["fix"], "./os sort", "with the command that rescues it")

    # nothing sweeps it, however old it is: another save must leave it alone
    t.box.run("save", "something else entirely")
    t.ok(orphan.exists(), "a later save does not delete what an earlier one left")

    t.box.run("sort")
    t.ok(not orphan.exists(), "and sort is what actually files it")
    filed = [i for i in t.box.items() if "accountant" in (i["title"] or "").lower()]
    t.eq(len(filed), 1, "landing as a real numbered thing")
    t.ok(bool(filed[0]["id"]), "with a number of its own")

    after = t.box.json("check", expect=None)
    t.eq([i for i in after["issues"] if i["code"] == "unfiled-capture"], [],
         "and the finding clears once it is filed")


@test
def test_no_option_is_silently_ignored(t: Case) -> None:
    """An option the code does not know stops the run instead of being dropped.

    `os sort --dry` is somebody asking for a preview. Silently ignoring the
    misspelling and doing the real thing is the exact mistake a dry-run flag
    exists to prevent, and it is worse for being invisible."""
    refused = t.box.run("sort", "--dry", expect=2)
    t.ok("--dry" in refused.stderr, "it names the option it did not understand")
    t.ok("--dry-run" in refused.stderr, "and suggests the one that was meant")
    t.eq(refused.stdout, "", "nothing is reported as though it had run")

    none_taken = t.box.run("undo", "--oops", expect=2)
    t.ok("no options" in none_taken.stderr, "a command with none says so plainly")

    # words of theirs are not options, whatever they start with
    t.box.run("save", "-3 degrees and the heating is off")
    t.ok(any("heating" in (i["title"] or "").lower() for i in t.box.items()),
         "a thought starting with a dash is still a thought")
    t.box.run("save", "--", "--sorting out the garage")
    t.ok(any("garage" in (i["title"] or "").lower() for i in t.box.items()),
         "and a bare -- keeps anything at all")

    # the table has to stay in step with the code, or it starts refusing
    # options that work and waving through ones that do not
    source = (t.box.root / ".os" / "engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name, declared in engine.FLAGS.items():
        if declared is None:
            continue
        node = funcs.get(name)
        t.ok(node is not None, f"{name} in the option table is a real command")
        used = set()
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            which = getattr(call.func, "id", "")
            if which not in ("_flag", "_opt"):
                continue
            for arg in (call.args[1:] if which == "_flag" else call.args[1:2]):
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    used.add(arg.value)
        t.eq(sorted(declared), sorted(used),
             f"{name} declares exactly the options it reads")
    handlers = {h.__name__ for h in engine.COMMANDS.values()}
    t.eq(sorted(handlers - set(engine.FLAGS)), [], "every command is in the table")


@test
def test_new_creates_every_kind(t: Case) -> None:
    """Each blueprint produces a valid, identified, indexed item."""
    made = {
        "project": t.box.run("new", "project", "Ship the redesign", "--domain", "design"),
        "ongoing": t.box.run("new", "ongoing", "Codebase health", "--domain", "engineering"),
        "note": t.box.run("new", "note", "Postgres index types", "--domain", "engineering"),
        "skill": t.box.run("new", "skill", "Weekly Report"),
        "agent": t.box.run("new", "agent", "Proofreader"),
    }
    t.eq(len(made), 5, "five kinds created")
    t.box.run("index")
    items = t.box.items()
    kinds = {i["kind"] for i in items}
    for kind in ("project", "note", "skill", "agent"):
        t.ok(kind in kinds, f"a {kind} exists after creation")
    # There is no journal kind any more: what happened goes in the item it
    # happened to. Asking for one has to be refused, not quietly filed.
    t.box.run("new", "log", "Kickoff meeting", expect=1)
    phases = {i["title"]: i.get("status") for i in items}
    t.eq(phases.get("Ship the redesign"), "pushing", "`new work` starts it being pushed")
    t.eq(phases.get("Codebase health"), "holding", "`new ongoing` starts it being held")

    for item in items:
        if item["kind"] in ("project", "note"):
            t.ok(bool(item["id"]), f"{item['title']} got an ID")
            t.ok(item["id"] in item["path"], f"{item['title']} carries its ID in the path")

    # a reserved name must be refused, not silently accepted
    proc = t.box.run("new", "skill", "doctor", expect=1)
    t.ok("built-in" in (proc.stderr + proc.stdout), "reserved skill names are refused")


@test
def test_sorts_one_hundred_and_thirty_items(t: Case) -> None:
    """The headline promise: throw 130 mixed things in, get a sorted folder."""
    planted = t.box.fill_inbox(130)
    t.eq(t.box.inbox_count(), 130, "130 items planted in the inbox")

    started = time.time()
    t.box.run("sort")
    elapsed = time.time() - started

    t.eq(t.box.inbox_count(), 0, "the inbox is empty afterwards")
    t.ok(elapsed < 120, f"sorting 130 items took {elapsed:.1f}s")

    items = t.box.items()
    filed = [i for i in items if i["bucket"] != engine.TOOLKIT]
    t.gte(len(filed), 130, "every planted item is in the index")

    # no data loss
    t.gte(len(list((t.box.root).rglob("*.md"))), 130, "no markdown was lost")

    # categories appeared
    categorised = [i for i in filed if i["trail"]]
    t.gte(len(categorised), 60, "most items ended up inside a category")

    # nothing is buried
    for item in filed:
        t.ok(len(item["trail"]) <= 2, f"{item['path']} is at most 2 levels below its bucket")

    # every category is a real, marked category
    for bucket in ("Work", "Notes"):
        base = t.box.root / bucket
        for child in base.iterdir():
            if child.is_dir() and not engine.ID_RE.match(child.name) and not engine.ignored(child):
                t.ok((child / ".category").exists(),
                     f"{bucket}/{child.name} is a marked category, not a stray folder")

    # every planted item is traceable, and lands where its label says
    hits, lost, wrong = 0, [], []
    for marker, _, expected in planted:
        where = t.box.locate(marker)
        if where is None:
            lost.append(marker)
            continue
        if t.box.bucket_of(where) == expected:
            hits += 1
        else:
            wrong.append((marker, expected, t.box.bucket_of(where)))
    t.eq(lost, [], f"every planted item is still findable ({len(lost)} lost)")
    accuracy = hits / len(planted)
    t.gte(round(accuracy, 3), 0.80,
          f"classification accuracy {accuracy:.0%} against labelled fixtures; "
          f"misplaced: {wrong[:6]}")


@test
def test_ids_are_unique_and_permanent(t: Case) -> None:
    """No two things ever share an ID, even across many sorts."""
    t.box.fill_inbox(60)
    t.box.run("sort")
    first = {i["id"]: i["title"] for i in t.box.items() if i["id"]}
    t.eq(len(first), len([i for i in t.box.items() if i["id"]]), "IDs are unique")

    t.box.fill_inbox(40)
    t.box.run("sort")
    second = {i["id"]: i["title"] for i in t.box.items() if i["id"]}
    ids = [i["id"] for i in t.box.items() if i["id"]]
    t.eq(len(set(ids)), len(ids), "IDs stay unique after a second batch")

    for ident, title in first.items():
        t.ok(ident in second, f"ID {ident} survived the second sort")
        t.eq(second[ident], title, f"ID {ident} still points at the same thing")

    # and they survive losing the state file entirely
    (t.box.root / ".os" / "state.json").write_text(
        json.dumps({"counters": {}, "undo": [], "history": []}))
    t.box.fill_inbox(10)
    t.box.run("sort")
    ids = [i["id"] for i in t.box.items() if i["id"]]
    t.eq(len(set(ids)), len(ids), "IDs stay unique after state.json is destroyed")


@test
def test_sorting_is_idempotent(t: Case) -> None:
    """Running sort twice changes nothing the second time."""
    t.box.fill_inbox(80)
    t.box.run("sort")
    before = t.box.tree()
    result = t.box.json("sort")
    t.eq(result["moves"], [], "a second sort moves nothing")
    after = t.box.tree()
    t.eq(set(after) - set(before), set(), "no files appeared")
    t.eq(set(before) - set(after), set(), "no files vanished")


@test
def test_categories_appear_and_collapse(t: Case) -> None:
    """Structure is earned, not imposed — and it is given back."""
    for i in range(6):
        (t.box.root / "Notes" / f"note-{i}.md").write_text(
            f"# Postgres index note {i}\n\nReference cheat sheet on database schema and indexes.\n")
    t.box.run("sort")
    flat = [i for i in t.box.items() if i["bucket"] == "Notes"]
    t.ok(all(not i["trail"] for i in flat), "6 items stay flat — no premature folders")

    items = fixtures.bulk(60)
    for name, body, expected in items:
        if expected == "Notes":
            (t.box.root / "Notes" / name).write_text(body)
    t.box.run("sort")
    library = [i for i in t.box.items() if i["bucket"] == "Notes"]
    t.gte(len(library), 13, "the library is now crowded")
    t.ok(any(i["trail"] for i in library), "categories appeared once it was crowded")

    # take almost everything away again
    keep = 3
    survivors = sorted(library, key=lambda i: i["id"])[:keep]
    keep_paths = {i["path"] for i in survivors}
    for item in library:
        if item["path"] not in keep_paths:
            target = t.box.root / item["path"]
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
    t.box.run("sort")
    library = [i for i in t.box.items() if i["bucket"] == "Notes"]
    t.ok(all(not i["trail"] for i in library),
         "categories collapsed back once they were no longer needed")
    leftovers = [p for p in (t.box.root / "Notes").iterdir()
                 if p.is_dir() and (p / ".category").exists()]
    t.eq(leftovers, [], "empty category folders were removed")


@test
def test_undo_restores_the_tree_exactly(t: Case) -> None:
    """Every sort is reversible, byte for byte."""
    t.box.fill_inbox(45)
    before = t.box.tree()
    t.box.run("sort")
    mid = t.box.tree()
    t.ok(mid != before, "the sort actually changed the tree")

    t.box.run("undo")
    after = t.box.tree()

    lost = {p for p in before if p not in after}
    t.eq(lost, set(), "undo lost no files")
    for path, checksum in before.items():
        t.eq(after.get(path), checksum, f"{path} came back unchanged")

    t.eq(t.box.inbox_count(), 45, "all 45 items are unfiled again")


@test
def test_undo_leaves_nothing_behind(t: Case) -> None:
    """Undo removes what the run created, not only what it moved."""
    inbox = t.box.root / "Notes"
    (inbox / "numbers.csv").write_text("date,revenue\n2026-01-01,12000\n")
    (inbox / "diagram.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (inbox / "ship-it.md").write_text(
        "# Ship the rewrite\n\nDeadline Friday. Migrate the service.\n\n- [ ] cut over\n")
    before = t.box.tree()

    t.box.run("sort")
    cards = list(t.box.root.rglob("*.card.md"))
    t.gte(len(cards), 2, "asset cards were generated")

    t.box.run("undo")
    after = t.box.tree()

    t.eq(list(t.box.root.rglob("*.card.md")), [], "no orphan asset cards survived the undo")
    t.eq(set(after) - set(before), set(), "undo left no file behind")
    t.eq(set(before) - set(after), set(), "undo lost no file")
    t.eq(t.box.inbox_count(), 3, "all three items are back in the inbox")

    # The machine's own record lives in .os/state.json, where it cannot bury
    # the one thing a person would go back for.
    history = json.loads((t.box.root / ".os" / "state.json").read_text())["history"]
    t.gte(len(history), 1, "but every operation is still recorded, in state.json")
    t.ok(any("sort" in h["label"] for h in history), "including the sort")


@test
def test_undo_walks_back_several_runs(t: Case) -> None:
    """The undo stack is a stack, not a single slot."""
    t.box.fill_inbox(12)
    t.box.run("sort")
    first = t.box.inbox_count()
    t.box.fill_inbox(8)
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "inbox clear after two sorts")
    t.box.run("undo")
    t.eq(t.box.inbox_count(), 8, "one undo returns the second batch only")
    t.box.run("undo")
    t.eq(t.box.inbox_count(), 20, "a second undo returns the first batch too")


@test
def test_archive_and_restore_round_trip(t: Case) -> None:
    """Archiving is about attention, not deletion."""
    t.box.run("new", "project", "Retire this thing", "--domain", "engineering")
    t.box.run("index")
    project = next(i for i in t.box.items() if i["kind"] == "project")
    ident = project["id"]

    t.box.run("archive", ident)
    items = t.box.items()
    archived = next(i for i in items if i["id"] == ident)
    t.eq(archived["bucket"], "Archive", "the project moved to the archive")
    t.ok((t.box.root / archived["path"]).exists(), "the files are really there")

    hits = t.box.json("find", "Retire this thing")
    t.gte(len(hits), 1, "archived items are still findable")

    t.box.run("restore", ident)
    restored = next(i for i in t.box.items() if i["id"] == ident)
    t.eq(restored["bucket"], "Work", "restore puts it back where it came from")
    t.eq(restored["id"], ident, "the ID never changed")


# ---------------------------------------------------------------------------
# learning from source material  (.os/learn.py — the one part that goes online)
# ---------------------------------------------------------------------------

#: YouTube's automatic captions roll: every cue repeats the tail of the one
#: before it so the words appear to scroll up the screen. Read literally, this
#: says "the first thing" three times and "he said" twice.
ROLLING_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
the first thing

00:00:02.000 --> 00:00:04.000
the first thing
you do is flatten

00:00:04.000 --> 00:00:06.500
you do is flatten
<c>the back</c> &amp; hone it

00:00:35.000 --> 00:00:38.000
he said

00:00:38.000 --> 00:00:41.000
he said
that's the whole trick
"""


@test
def test_captions_become_something_worth_reading(t: Case) -> None:
    """Rolling captions must collapse, and keep a timestamp you can cite.

    Left rolling, a transcript triples in size and every line appears twice —
    unquotable, and three times the context for whatever reads it. The
    timestamps are the other half: a claim in a note is only checkable if you
    can say where in the video it came from."""
    vtt = t.box.tmp / "rolling.vtt"
    vtt.write_text(ROLLING_VTT, encoding="utf-8")
    text = learn.clean_vtt(vtt)

    t.eq(text.count("the first thing"), 1, "a repeated cue line appears once")
    t.eq(text.count("he said"), 1, "and so does the second one")
    t.ok("you do is flatten" in text and "that's the whole trick" in text,
         "while every new line survives")
    t.ok("<c>" not in text and "</c>" not in text, "caption markup is stripped")
    t.ok("&amp;" not in text and "& hone it" in text, "and entities are decoded")
    for junk in ("WEBVTT", "Kind:", "Language:", "-->"):
        t.ok(junk not in text, f"the file's own scaffolding is dropped ({junk})")

    stamps = re.findall(r"\[(\d\d):(\d\d)\]", text)
    t.gte(len(stamps), 2, "there is a timestamp to cite")
    t.eq(stamps[0], ("00", "00"), "the first is where the video starts")
    t.eq(stamps[1], ("00", "35"), "and the next follows the cue it belongs to")

    # order is meaning: a method read out of sequence is not a method
    t.ok(text.index("the first thing") < text.index("that's the whole trick"),
         "and the words stay in the order they were said")


@test
def test_a_link_is_read_however_it_was_pasted(t: Case) -> None:
    """People paste whatever the share button gave them."""
    for link in ("https://www.youtube.com/watch?v=wt4p2oalmRY",
                 "https://youtu.be/wt4p2oalmRY",
                 "https://www.youtube.com/shorts/wt4p2oalmRY",
                 "https://www.youtube.com/embed/wt4p2oalmRY?start=90",
                 "https://m.youtube.com/watch?v=wt4p2oalmRY&t=42s",
                 "wt4p2oalmRY"):
        t.eq(learn.video_id(link), "wt4p2oalmRY", f"{link} names the video")
    for not_a_video in ("https://example.com/article", "", "watch?v=too-short"):
        t.eq(learn.video_id(not_a_video), None, f"{not_a_video!r} is not a video")

    # a channel link has to be pointed at the uploads, not the trailer
    t.eq(learn.as_listing("https://www.youtube.com/@someone"),
         "https://www.youtube.com/@someone/videos", "a channel means its videos")
    t.eq(learn.as_listing("https://www.youtube.com/@someone/streams"),
         "https://www.youtube.com/@someone/videos", "and so does its streams tab")
    for left_alone in ("https://www.youtube.com/playlist?list=PL1",
                       "https://www.youtube.com/@someone/videos"):
        t.eq(learn.as_listing(left_alone), left_alone, f"{left_alone} is already right")


@test
def test_learning_says_what_went_wrong_in_words(t: Case) -> None:
    """It is driven by an AI, so every answer — including failure — is JSON.

    A traceback, or a bare non-zero exit, tells whatever is reading it nothing
    it can act on or repeat to a person."""
    def run(*args, expect: int = 0):
        proc = subprocess.run([sys.executable, ".os/learn.py", *args],
                              cwd=str(t.box.root), capture_output=True, text=True)
        t.eq(proc.returncode, expect, f"`learn.py {' '.join(args)}` exits {expect}")
        t.ok("Traceback" not in proc.stderr,
             f"and never with a traceback:\n{proc.stderr[-400:]}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            t.ok(False, f"`learn.py {' '.join(args)}` did not print JSON: {proc.stdout[:200]!r}")
            return {}

    t.ok("commands" in run("help"), "help lists what it can do")
    t.ok("list" in run("help")["commands"] or
         any(k.startswith("list") for k in run("help")["commands"]),
         "including how to see what a channel has")

    unknown = run("teleport")
    t.eq(unknown["ok"], False, "an unknown command is a refusal, not a crash")
    t.ok("teleport" in unknown["why"], "and it says which word it did not know")

    t.eq(run("list")["ok"], False, "asking for a listing with no link is refused")
    t.eq(run("get")["ok"], False, "and so is asking for a transcript with no link")

    bad = run("get", "https://example.com/not-a-video")
    t.eq(bad["results"][0]["ok"], False, "a link that is not a video is reported per link")
    t.ok("video" in bad["results"][0]["why"], "in words a person could act on")

    empty = run("have")
    t.eq(empty["cached"], [], "nothing is cached in a fresh folder")

    # the cache is real, keyed by video, and forgettable
    learn.cache_dir(t.box.root).joinpath("wt4p2oalmRY.txt").write_text(
        "[00:00] flatten the back first\n", encoding="utf-8")
    t.eq(len(run("have")["cached"]), 1, "a cached transcript is listed")
    t.eq(run("have")["cached"][0]["words"], 5, "with how much is in it")
    t.eq(run("forget", "wt4p2oalmRY")["removed"], 1, "and can be dropped again")
    t.eq(run("have")["cached"], [], "leaving nothing behind")


@test
def test_a_subject_teaches_the_folder_its_words(t: Case) -> None:
    """What `/learn` studies has to change where the next thought lands.

    A subject arrives with vocabulary the folder has never seen — "roas", "ad
    set" — and filing is done by matching words. Learning something and then
    still misfiling every passing note about it is the loop left open."""
    def run(*args: str) -> dict:
        proc = subprocess.run([sys.executable, ".os/learn.py", "words", *args],
                              cwd=str(t.box.root), capture_output=True, text=True)
        t.eq(proc.returncode, 0, f"`learn.py words {' '.join(args)}` exits 0")
        return json.loads(proc.stdout)

    listed = run()
    t.ok(listed["ok"], "with no arguments it says what domains exist")
    t.ok(any(d["domain"] == "marketing" for d in listed["domains"]),
         "naming each one, so nothing has to be guessed at")

    before = t.box.root / ".os" / "words.json"
    mine = json.loads(before.read_text())["domains"]["marketing"]["keywords"]

    # invented words, so the assertion holds whatever vocabulary ships
    added = run("marketing", "zorblat", "flimbus rate", "quibbing phase")
    t.eq(added["added"], ["zorblat", "flimbus rate", "quibbing phase"], "all three taken")
    again = run("marketing", "zorblat", "ZORBLAT ", "grelling")
    t.eq(again["added"], ["grelling"], "only what is genuinely new is added")
    t.ok("zorblat" in again["already_known"], "a repeat is reported, not duplicated")

    tax = json.loads(before.read_text())
    t.eq(tax["domains"]["marketing"]["keywords"], mine,
         "their own keywords are never written to")
    t.eq(tax["domains"]["marketing"]["learned"][-4:],
         ["zorblat", "flimbus rate", "quibbing phase", "grelling"],
         "what was learned is kept separate, and in order")

    # the payoff: the classifier scores learned words exactly like their own
    box = engine.Zenith(t.box.root)
    dom, _, _ = engine.Classifier(box).score_domain(
        "zorblat fell off once the quibbing phase reset", "", "")
    t.eq(dom, "marketing", "so a passing thought about it now files itself")

    missing = run("nosuchdomain", "roas")
    t.eq(missing["ok"], False, "an unknown domain is refused")
    t.ok("marketing" in missing["domains"], "and the real ones are offered back")


@test
def test_the_vocabulary_is_a_command_like_any_other(t: Case) -> None:
    """Teaching the folder words has to be reachable without invoking python.

    `python3 .os/learn.py` is not a thing to be typing on Windows, and every
    other capability here is a plain `os` verb. The classifier is only as good
    as the words it has, so this is the one that compounds."""
    listed = t.box.json("words")
    t.ok(listed["ok"], "with no arguments it lists the domains")
    t.ok(any(d["domain"] == "marketing" for d in listed["domains"]), "by name")

    added = t.box.json("words", "marketing", "zorblat", "flimbus rate")
    t.eq(added["added"], ["zorblat", "flimbus rate"], "and takes new ones")
    plain = t.box.run("words", "marketing", "zorblat", "grelling")
    t.ok("1 added" in plain.stdout, "saying in words what it did")
    t.ok("zorblat" not in plain.stdout.split("added")[1][:40],
         "and not re-listing what it already knew as new")

    t.box.run("words", "nosuchdomain", "zorblat", expect=1)
    t.box.run("words", "marketing", expect=1)

    dom, _, _ = engine.Classifier(engine.Zenith(t.box.root)).score_domain(
        "the zorblat flimbus rate is falling", "", "")
    t.eq(dom, "marketing", "and what it learned is what filing then uses")

    # a video id that starts with a dash is an argument, not a mistyped option
    t.box.run("learn", "--cached")
    fetched = t.box.run("learn", "--", "-Xf12o4jt4x", expect=None)
    t.ok("doesn't understand" not in fetched.stderr,
         "a bare -- hands the id through untouched")


@test
def test_a_missing_downloader_is_not_a_dead_end(t: Case) -> None:
    """yt-dlp is the one thing Zenith wants installed, and it is optional.

    Everything else works without it, so a missing one has to say the single
    line to type on *this* machine rather than failing at somebody."""
    stripped = dict(os.environ, PATH="/nonexistent")
    proc = subprocess.run(
        [sys.executable, ".os/learn.py", "list", "https://www.youtube.com/@someone"],
        cwd=str(t.box.root), capture_output=True, text=True, env=stripped)
    t.eq(proc.returncode, 0, "a missing downloader is reported, not raised")
    t.ok("Traceback" not in proc.stderr, "with no traceback")
    payload = json.loads(proc.stdout)
    t.eq(payload["ok"], False, "it says plainly that it could not do it")
    t.ok("yt-dlp" in payload["why"], "and names what is missing")
    t.ok(payload.get("fix"), "with the one line to type")
    t.ok("yt-dlp" in payload["fix"], "which installs the thing it just named")
    t.ok("without it" in payload.get("note", ""),
         "and says the rest of the folder still works")

    # transcripts live in .os/, never in a bucket the sorter would adopt
    t.ok(learn.cache_dir(t.box.root).is_relative_to(t.box.root / ".os"),
         "the cache sits under .os/, out of the sorter's way")
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "so six transcripts never look like unfiled work")


@test
def test_a_tag_says_what_a_thing_is_not_where_it_sits(t: Case) -> None:
    """`W.04` keeps its W in the Archive, because W is not a folder pointer.

    The prefix used to be the folder's number, which was already a lie the
    moment anything was closed — 2.01 sat in 9_Archive still calling itself 2.
    Only Work and Notes hand tags out; Archive has no letter of its own."""
    t.box.run("new", "work", "Ship the rewrite")
    t.box.run("new", "note", "Postgres index types")
    t.box.run("index")
    by_title = {i["title"]: i for i in t.box.items()}
    work, note = by_title["Ship the rewrite"], by_title["Postgres index types"]
    t.ok(work["id"].startswith("W."), f"work is tagged W (got {work['id']!r})")
    t.ok(note["id"].startswith("N."), f"a note is tagged N (got {note['id']!r})")
    t.ok(work["id"] in work["path"], "and the tag is on the folder name too")

    t.box.run("close", work["id"])
    closed = next(i for i in t.box.items() if i["id"] == work["id"])
    t.eq(closed["bucket"], "Archive", "closing moves it to the Archive")
    t.ok(closed["id"].startswith("W."), "and it is still tagged W in there")

    # the archive hands out no tags, so it needs no letter and keeps no counter
    config = json.loads((t.box.root / ".os" / "config.json").read_text())
    t.ok("code" not in config["buckets"]["Archive"],
         "the Archive has no letter of its own")
    counters = json.loads((t.box.root / ".os" / "state.json").read_text())["counters"]
    t.eq([k for k in counters if k not in ("W", "N")], [],
         f"and no counter was ever opened for it (got {counters!r})")

    # a tag is text, so it can never round-trip through a float and lose a digit
    for path in (t.box.root / "Notes").rglob("*.md"):
        header = path.read_text().split("\n---", 1)[0]
        t.ok('"' not in header.split("id:")[1].split("\n")[0] if "id:" in header else True,
             f"{path.name} needs no quotes around its tag to survive")

    t.box.run("W.99", expect=1)          # a tag that isn't there is still an error
    shown = t.box.run("show", note["id"])
    t.ok("Postgres" in shown.stdout, "and ./os <tag> still opens the right thing")


@test
def test_undo_of_a_sort_does_not_duplicate(t: Case) -> None:
    """Undoing a sort must leave one copy of each thing, not two.

    A single sort moves the same item twice — the inbox pass files it, the
    balance pass tucks it into a category — and each move snapshots its content
    under the path it had at the time. Writing every snapshot back after the
    moves were reversed re-created the file at the intermediate path, so the
    item ended up sitting in the inbox *and* numbered in Notes."""
    inbox = t.box.root / "Notes"
    for i in range(16):
        (inbox / f"n{i}.md").write_text(
            f"Reference note {i} on postgres index types. B-tree is the default.\n"
            "Cheat sheet, for reference. Overview of when each applies.\n")
    before = t.box.tree()

    t.box.run("sort")
    t.ok(any(t.box.root.joinpath("Notes").rglob("N.*.md")), "the sort filed them")
    t.ok(any(p.is_dir() and (p / ".category").exists()
             for p in (t.box.root / "Notes").iterdir()),
         "and enough of them to force a category folder, which moves them twice")

    t.box.run("undo")
    t.eq(t.box.inbox_count(), 16, "all sixteen are unfiled again")
    survivors = sorted(p.name for p in (t.box.root / "Notes").rglob("*")
                       if p.is_file() and not p.name.startswith("."))
    t.eq(survivors, sorted(f"n{i}.md" for i in range(16)),
         "under their original names, with not one numbered copy left beside them")
    t.eq(set(t.box.tree()) - set(before), set(), "the tree is exactly as it was")
    t.eq(set(before) - set(t.box.tree()), set(), "with nothing lost either")


@test
def test_sorting_the_same_thing_twice_settles(t: Case) -> None:
    """A second sort must find nothing left to do, whatever was dropped in.

    Anything the sorter cannot write a header into has to get a card instead,
    or it carries no id anywhere and every later run adopts it again: a file
    with no extension came out as `4.10_4.04_no-extension`, having burnt a
    second number on the way. A shortcut hit the same wall for the opposite
    reason — it has no spine on purpose, so its card has to be what says it
    is ours."""
    notes = t.box.root / "Notes"
    (notes / "no-extension").write_text("# No extension\n\nStill prose, though.\n")
    (notes / "plain.md").write_text("# Plain\n\nReference notes on index types.\n")
    outside = t.box.tmp / "elsewhere.md"
    outside.write_text("# Outside\n\nBelongs to somebody else.\n")
    (notes / "shortcut.md").symlink_to(outside)

    t.box.run("sort")
    first = sorted(p.name for p in notes.iterdir() if p.name != ".gitkeep")
    t.eq(t.box.inbox_count(), 0, "one sort files everything, headers and all")

    for run in range(2, 4):
        proc = t.box.run("sort")
        t.ok("nothing waiting" in proc.stdout, f"sort #{run} finds nothing left to do")
        t.eq(sorted(p.name for p in notes.iterdir() if p.name != ".gitkeep"), first,
             f"sort #{run} renamed nothing and burnt no numbers")

    doubled = [n for n in first if len(engine.ID_RE.findall(n)) > 1
               or n.count("_4.") or n.count("_2.")]
    t.eq(doubled, [], "and nothing ended up numbered twice")


@test
def test_undo_follows_a_file_it_had_to_rename(t: Case) -> None:
    """Undo two runs whose sources shared filenames, and both must come back.

    Putting something back can find its old name taken — by the batch the
    previous undo already restored — so it lands beside it instead. Its saved
    contents have to follow it there. They did not: they were written to the
    old name, overwriting the other file, and leaving this one still carrying
    the header the sort had stamped on it."""
    notes = t.box.root / "Notes"
    body = ("Reference note on postgres index types. B-tree is the default.\n"
            "Cheat sheet, for reference. Overview of when each applies.\n")
    for batch in range(2):
        for i in range(6):
            (notes / f"same-{i}.md").write_text(f"{body}\nBatch {batch}.\n")
        t.box.run("sort")
        t.eq(t.box.inbox_count(), 0, f"batch {batch} filed")

    t.box.run("undo")
    t.eq(t.box.inbox_count(), 6, "one undo brings back the second batch")
    t.box.run("undo")
    t.eq(t.box.inbox_count(), 12,
         "and the second brings back the first, renamed around the collision")

    for path in notes.rglob("*.md"):
        if path.name.startswith("."):
            continue
        meta, _ = engine.parse_frontmatter(path.read_text())
        t.ok(not str(meta.get("id") or "").strip(),
             f"{path.name} came back as it was written, with no stamped id left on it")


@test
def test_restore_does_not_turn_a_note_into_work(t: Case) -> None:
    """Coming out of the archive gives something its own status back.

    Everything used to come back as `pushing`, so a filed PDF or a note would
    reappear as work with a next action — and then start being nagged for going
    quiet, which is a thing it never had."""
    (t.box.root / "Notes" / "figures.csv").write_text("quarter,total\nQ1,100\n")
    t.box.run("save", "Notes on Postgres index types: btree is the default")
    t.box.run("sort")

    for kind in ("asset", "note"):
        item = next((i for i in t.box.items() if i["kind"] == kind), None)
        if item is None:
            continue
        t.box.run("close", item["id"])
        t.box.run("back", item["id"])
        again = next(i for i in t.box.items() if i["id"] == item["id"])
        t.ok(again["status"] in ("", "—"),
             f"a {kind} comes back as itself, not as work ({again['status']!r})")
        meta, _ = engine.parse_frontmatter(
            (t.box.root / (again["path"] + ".card.md")).read_text()
            if kind == "asset" else (t.box.root / again["path"]).read_text())
        t.ok("archived" not in meta, f"the archive stamp is cleared off the {kind}")
        t.ok("origin" not in meta, f"and so is the note of where it used to live")

    # work, on the other hand, does go back to being pushed
    t.box.run("new", "project", "Ship the rewrite")
    work = next(i for i in t.box.items() if i["title"] == "Ship the rewrite")
    t.box.run("close", work["id"])
    t.box.run("back", work["id"])
    back = next(i for i in t.box.items() if i["id"] == work["id"])
    t.eq(back["status"], "pushing", "work comes back on the go")


@test
def test_a_backup_never_silently_skips_your_writing(t: Case) -> None:
    """The zip excluded any folder *named* cache or backups, at any depth.

    Somebody's own `Notes/cache/` was quietly left out of every backup — the
    worst possible failure for the one command whose whole job is not losing
    things."""
    import zipfile
    for folder in ("cache", "backups"):
        (t.box.root / "Notes" / folder).mkdir(parents=True, exist_ok=True)
        (t.box.root / "Notes" / folder / "mine.md").write_text(
            f"# Mine\n\nA real note that happens to sit in a folder called {folder}.\n")
    t.box.run("backup")

    zips = sorted((t.box.root / ".os" / "backups").glob("*.zip"))
    t.eq(len(zips), 1, "a backup was written")
    inside = set(zipfile.ZipFile(zips[0]).namelist())
    for folder in ("cache", "backups"):
        t.ok(f"Notes/{folder}/mine.md" in inside,
             f"a note in a folder called {folder} is in the backup")
    t.ok(not any(n.startswith(".os/backups/") for n in inside),
         "while .os/backups is still left out, so backups do not nest")
    t.ok(not any(n.startswith(".os/cache/") for n in inside),
         "and .os/cache too, since it is all rebuildable")


@test
def test_search_finds_things_by_every_handle(t: Case) -> None:
    """ID, title, tag, body — all of them work."""
    t.box.fill_inbox(50)
    t.box.run("sort")
    items = [i for i in t.box.items() if i["kind"] in ("project", "note")]
    sample = items[len(items) // 2]

    by_title = t.box.json("find", sample["title"][:28])
    t.gte(len(by_title), 1, f"found '{sample['title'][:28]}' by title")
    t.ok(any(h["id"] == sample["id"] for h in by_title), "the right item ranks in the results")

    by_id = t.box.json("find", sample["id"])
    t.gte(len(by_id), 1, "found it by ID")

    proc = t.box.run("open", sample["id"])
    t.ok(sample["id"] in proc.stdout or Path(proc.stdout.strip()).exists(),
         "`os open <id>` resolves to a real path")

    empty = t.box.json("find", "zzzz-nothing-matches-this-zzzz")
    t.eq(empty, [], "a query with no matches returns nothing, cleanly")


@test
def test_front_matter_survives_a_round_trip(t: Case) -> None:
    """The parser and the writer agree, including the awkward cases."""
    cases = [
        {"id": "N.01", "title": "Plain", "tags": ["a", "b"], "status": "active"},
        # the old numeric shape: quoted, or it round-trips as the float 10.1
        {"id": "10.01", "title": "Legacy tag", "tags": [], "status": "active"},
        {"title": "Colons: everywhere: here", "tags": [], "status": "—"},
        {"title": 'Quotes "inside" it', "tags": ["one-tag"], "count": 42},
        {"title": "Unicode — em dash, curly ‘quotes’, emoji 🎯", "tags": ["ünïcode"]},
        {"title": "Hash # not a comment", "flag": True, "other": False, "nothing": None},
        {"title": "Leading spaces preserved", "tags": ["a", "b", "c", "d", "e"]},
    ]
    for meta in cases:
        text = engine.compose(dict(meta), "# Body\n\nSome content.\n")
        parsed, body = engine.parse_frontmatter(text)
        for key, value in meta.items():
            t.eq(parsed.get(key), value, f"'{key}' survived the round trip")
        t.ok("Some content." in body, "the body survived")

    # malformed front matter must never throw
    for broken in ("---\nnot: [closed\n---\nbody", "---\n\n\n---\n", "---", "no front matter at all",
                   "---\n: bad key\n---\nbody"):
        meta, body = engine.parse_frontmatter(broken)
        t.ok(isinstance(meta, dict), "malformed front matter degrades to a dict")


@test
def test_check_finds_real_problems(t: Case) -> None:
    """Every check the doctor advertises actually fires."""
    t.box.fill_inbox(20)
    t.box.run("sort")

    # plant a duplicate ID
    library = t.box.root / "Notes"
    victims = sorted(p for p in library.rglob("N.*.md"))
    t.gte(len(victims), 2, "enough library notes to break")
    meta, body = engine.parse_frontmatter(victims[1].read_text())
    stolen = engine.parse_frontmatter(victims[0].read_text())[0]["id"]
    meta["id"] = stolen
    victims[1].write_text(engine.compose(meta, body))
    victims[1].rename(victims[1].with_name(f"{stolen}_stolen-id.md"))

    # plant a colliding skill name and a skill with no description
    (t.box.root / ".claude" / "skills" / "review").mkdir(parents=True, exist_ok=True)
    (t.box.root / ".claude" / "skills" / "review" / "SKILL.md").write_text(
        "---\nname: review\n---\n\nThis collides with a built-in and has no description.\n")

    codes = {i["code"] for i in t.box.json("check", expect=1)["issues"]}
    for expected in ("duplicate-id", "skill-name-clash", "skill-no-description"):
        t.ok(expected in codes, f"check detects {expected}")

    # --fix repairs the mechanical things without touching the judgement calls
    (t.box.root / "Notes").rename(t.box.root / "Notes_moved")
    for hook in (t.box.root / ".claude" / "hooks").iterdir():
        hook.chmod(0o644)

    t.box.run("check", "--fix", expect=1)   # still 1: the planted errors need judgement
    t.ok((t.box.root / "Notes").is_dir(), "--fix recreated the missing folder")
    for hook in (t.box.root / ".claude" / "hooks").iterdir():
        t.ok(os.access(hook, os.X_OK), f"--fix made {hook.name} executable again")


@test
def test_index_matches_the_disk(t: Case) -> None:
    """The map is the territory."""
    t.box.fill_inbox(70)
    t.box.run("sort")
    registry = t.box.registry()

    for item in registry["items"]:
        t.ok((t.box.root / item["path"]).exists(),
             f"indexed item {item['path']} exists on disk")

    index_md = (t.box.root / "INDEX.md").read_text()
    sample = [i for i in registry["items"] if i["id"]][:25]
    for item in sample:
        t.ok(item["id"] in index_md, f"INDEX.md lists {item['id']}")

    catalog = (t.box.root / ".claude" / "CATALOG.md").read_text()
    for skill in [i for i in registry["items"] if i["kind"] == "skill"]:
        t.ok(f"/{Path(skill['path']).name}" in catalog,
             f"CATALOG.md lists /{Path(skill['path']).name}")

    t.eq(list(t.box.root.rglob("_index.md")), [],
         "no per-folder index files clutter the buckets")


@test
def test_the_surface_stays_small(t: Case) -> None:
    """Things stripped for the user's sake stay stripped."""
    for gone in ("dash", "stats", "guide", "link"):
        proc = t.box.run(gone, expect=1)
        t.ok("there is no" in proc.stderr, f"`os {gone}` is gone and says so plainly")
        t.ok("Did you mean" in proc.stderr or "lists everything" in proc.stderr,
             f"`os {gone}` points somewhere useful")
    for gone in ("GUIDE.pdf", ".os/guide.py", ".os/dashboard.html", "50_Toolkit",
                 "1_Inbox", "2_Work", "3_Areas", "4_Notes", "5_Files", "6_Journal",
                 "9_Archive", ".os/templates/log.md"):
        t.ok(not (t.box.root / gone).exists(), f"{gone} does not ship")
    for bucket in ("Work", "Notes", "Archive"):
        t.ok(not (t.box.root / bucket / "CLAUDE.md").exists(),
             f"{bucket} has no rules file of its own — they all live in AGENTS.md")


@test
def test_every_json_output_is_valid(t: Case) -> None:
    """Anything a script might parse, parses."""
    t.box.fill_inbox(15)
    t.box.run("sort")
    for args in (["tidy"], ["status"], ["index"], ["sort"], ["find", "note"],
                 ["brief"], ["check"], ["save", "a passing thought"]):
        payload = t.box.json(*args)
        t.ok(payload is not None, f"`os {' '.join(args)} --json` is valid JSON")
    # doctor exits 1 when it finds errors, so accept either outcome and just
    # insist the payload parses
    t.ok(t.box.json("doctor", expect=None) is not None, "`os doctor --json` is valid JSON")
    brief = t.box.json("brief")
    t.ok("hookSpecificOutput" in brief, "brief emits a hook-shaped payload")
    t.eq(brief["hookSpecificOutput"]["hookEventName"], "SessionStart",
         "brief declares the right hook event")


@test
def test_backup_captures_everything(t: Case) -> None:
    """A snapshot you could actually restore from."""
    import zipfile
    t.box.fill_inbox(20)
    t.box.run("sort")
    t.box.run("backup")
    zips = sorted((t.box.root / ".os" / "backups").glob("zenith-*.zip"))
    t.eq(len(zips), 1, "one snapshot was written")
    with zipfile.ZipFile(zips[0]) as zf:
        names = set(zf.namelist())
        t.ok("AGENTS.md" in names, "the rules are in the backup")
        t.ok("CLAUDE.md" in names, "so is Claude Code's pointer to them")
        t.ok(any(n.startswith("Notes/") for n in names), "content is in the backup")
        t.ok(any(n.startswith(".claude/skills/") for n in names), "the toolkit is in the backup")
        t.eq(zf.testzip(), None, "the archive is not corrupt")


@test
def test_the_folder_can_be_renamed_and_moved(t: Case) -> None:
    """Rename-safety: the engine finds its own root."""
    moved = t.box.tmp / "Somewhere Else"
    shutil.move(str(t.box.root), str(moved))
    env = dict(os.environ, NO_COLOR="1")
    env.pop("ZENITH_HOME", None)
    proc = subprocess.run([str(moved / "os"), "status"], capture_output=True, text=True,
                          cwd=str(moved), env=env, timeout=120)
    t.eq(proc.returncode, 0, "the CLI still runs after the folder is renamed")
    t.ok(str(moved) in proc.stdout, "it reports its new location")

    deep = moved / "Notes"
    proc = subprocess.run([str(moved / "os"), "status"], capture_output=True, text=True,
                          cwd=str(deep), env=env, timeout=120)
    t.eq(proc.returncode, 0, "it works from a subdirectory too")
    shutil.move(str(moved), str(t.box.root))


@test
def test_it_refuses_to_lose_data(t: Case) -> None:
    """Name collisions, weird filenames, and unreadable bytes."""
    inbox = t.box.root / "Notes"
    for i in range(4):
        (inbox / f"same-{i}.md").write_text("# Identical Title\n\nSame reference note every time.\n")
    (inbox / "spaces and (parens) & symbols!.md").write_text("# Odd name\n\nA note.\n")
    (inbox / "no-extension").write_text("# No extension\n\nStill a note.\n")
    (inbox / "binary.bin").write_bytes(bytes(range(256)))
    (inbox / "empty.md").write_text("")
    nested = inbox / "a folder" / "deeper"
    nested.mkdir(parents=True)
    (nested / "buried.md").write_text("# Buried note\n\nInside two folders.\n")

    planted = t.box.inbox_count()
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "everything got filed")

    def files_containing(needle: str) -> list[Path]:
        found = []
        for p in t.box.root.rglob("*"):
            if not p.is_file() or p.is_symlink() or p.name in t.box.GENERATED_FILES:
                continue
            if p.suffix.lower() not in engine.TEXT_SUFFIXES:
                continue
            try:
                if needle in p.read_text(encoding="utf-8", errors="replace"):
                    found.append(p)
            except OSError:
                pass
        return found

    same = files_containing("Same reference note every time.")
    t.eq(len(same), 4, "all four identical notes survived, as four distinct files")
    t.eq(len({p.name for p in same}), 4, "each got a distinct filename")
    t.gte(len(files_containing("Buried note")), 1, "a nested folder was kept whole")
    t.ok(any(p.name.endswith(".bin") for p in t.box.root.rglob("*.bin")),
         "binary files are preserved, not parsed")


@test
def test_tidy_reports_decay(t: Case) -> None:
    """The anti-decay pass sees what is rotting."""
    t.box.run("new", "project", "Ancient forgotten project", "--domain", "engineering")
    t.box.run("index")
    project = next(i for i in t.box.items() if i["kind"] == "project")
    spine = t.box.root / project["path"] / "README.md"
    meta, body = engine.parse_frontmatter(spine.read_text())
    meta["updated"] = "2020-01-01"
    meta["created"] = "2020-01-01"
    spine.write_text(engine.compose(meta, body))

    (t.box.root / "Notes" / "waiting.md").write_text("# Something waiting\n\nUnfiled.\n")

    report = t.box.json("review")
    t.gte(len(report["archive_candidates"]), 1, "a long-dead project is proposed for archiving")
    t.ok(any(c["id"] == project["id"] for c in report["archive_candidates"]),
         "the right project was flagged")
    t.gte(len(report["unfiled"]), 1, "the unfiled item dropped in by hand is reported")
    t.ok("score" in report, "the review carries a health score")


@test
def test_it_repairs_itself_after_a_bad_unzip(t: Case) -> None:
    """A zip extracted on Windows arrives with no executable bit.

    `./os` then fails with a bare "permission denied" and the hooks die
    silently, which is the worst possible first five seconds. Running it by any
    other route has to put that right permanently."""
    launcher = t.box.root / "os"
    hooks = sorted((t.box.root / ".claude" / "hooks").iterdir())
    for f in [launcher, *hooks]:
        f.chmod(0o644)
    t.ok(not os.access(launcher, os.X_OK), "the executable bit really is gone")

    def direct(*args: str) -> subprocess.CompletedProcess:
        """Run it the way somebody would have to: through the interpreter."""
        return subprocess.run([sys.executable, str(t.box.root / ".os" / "engine.py"), *args],
                              capture_output=True, text=True, cwd=str(t.box.root),
                              env=dict(os.environ, ZENITH_HOME=str(t.box.root), NO_COLOR="1"))

    t.eq(direct("status").returncode, 0, "it still runs when it cannot be executed directly")

    t.ok(os.access(launcher, os.X_OK), "and it put ./os back to runnable")
    for hook in hooks:
        t.ok(os.access(hook, os.X_OK), f"and {hook.name} too, so the AI still gets its brief")

    t.box.run("status")   # the normal path works again

    # every route in repairs it, including `bash os`
    for route in (["bash", str(launcher), "status"],
                  [sys.executable, str(t.box.root / ".os" / "engine.py"), "status"]):
        for f in [launcher, *hooks]:
            f.chmod(0o644)
        subprocess.run(route, capture_output=True, text=True, cwd=str(t.box.root),
                       env=dict(os.environ, ZENITH_HOME=str(t.box.root), NO_COLOR="1"))
        t.ok(os.access(launcher, os.X_OK), f"`{route[0].split('/')[-1]} …` repairs it too")

    # and the health check still carries the finding, for a copy it cannot write to
    doctor_codes = set(engine.Doctor.__dict__)   # the check exists as a named finding
    t.ok("run" in doctor_codes, "the health check is intact")
    t.ok("os-not-executable" in (t.box.root / ".os" / "engine.py").read_text(),
         "and still reports the case where the bit cannot be restored")

    # and the README tells someone who cannot run anything at all
    readme = (t.box.root / "README.md").read_text()
    t.ok("permission denied" in readme and "bash os" in readme,
         "the README gives a way out that needs no executable bit")


@test
def test_a_shortcut_cannot_break_the_folder(t: Case) -> None:
    """Someone drags an alias in. Two ways that used to end badly.

    Following a link back into the folder it sat in had the sorter write a
    README.md *through* it, leaving a file the scanner then ignored forever.
    And a link pointing outside the folder crashed every command that rebuilt
    the index, because resolving it produced a path that is not under the root."""
    inbox = t.box.root / "Notes"

    # 1. a link that points back at the folder it lives in
    (inbox / "loop").symlink_to("../Notes")
    t.box.run("sort")
    link = next((p for p in inbox.iterdir() if p.is_symlink()), None)
    t.ok(link is not None, "the link is still a link, not a copy of what it points at")
    t.ok(engine.ID_RE.match(link.name) is not None if link else False,
         "and it was filed like anything else — numbered, with a card")
    t.ok(not (inbox / "README.md").exists(),
         "no stray README.md is written back through it")

    # 2. a link that points somewhere outside the folder entirely
    outside = t.box.tmp / "somewhere-else.md"
    outside.write_text("# Outside\n\nContent about the deadline.\n")
    (inbox / "shortcut.md").symlink_to(outside)
    t.box.run("sort")

    for command in (["status"], ["index"], ["check"], ["find", "shortcut"], ["tidy"]):
        t.box.run(*command, expect=None)     # any crash fails the run outright
    t.box.run("index")
    for item in t.box.items():
        t.ok(not item["path"].startswith("/"),
             f"every recorded path stays inside the folder ({item['path']})")

    t.ok(outside.exists() and "Outside" in outside.read_text(),
         "and the thing the link points at is left alone")

    # filing is still settled: a second pass moves nothing
    t.ok("nothing waiting" in t.box.run("sort").stdout, "and sorting is stable")


@test
def test_a_shortcut_is_never_walked_through(t: Case) -> None:
    """A link is one item. What it points at belongs to whoever put it there.

    `is_dir()` and `is_file()` both follow a link, so the walker used to step
    straight through one: a shortcut to a folder carrying a `.category` marker
    was descended into, and `os sort` then *moved the files out of the person's
    own directory* into this one. A shortcut to a file was read as if it were
    that file, so the item showed up as a duplicate of itself — and the next
    sort would have rewritten the original's front matter through the link."""
    outside = t.box.tmp / "not-ours"
    (outside / "deep").mkdir(parents=True)
    (outside / ".category").write_text("")                 # looks like a group folder
    (outside / "invoice.md").write_text("# Invoice\n\nBudget and revenue for Q3.\n")

    (t.box.root / "Notes" / "linked").symlink_to(outside)
    t.box.run("sort")

    t.ok((outside / "invoice.md").exists(),
         "a file behind a shortcut is left in the folder it actually lives in")
    t.ok(not any(p.name == "invoice.md" for p in (t.box.root / "Notes").rglob("*")),
         "and is never adopted as an item of ours")

    # 2. a shortcut that points at a sibling is not a copy of it
    inbox = t.box.root / "Notes"
    (inbox / "real.md").write_text("# Real\n\nNotes on the database schema.\n")
    (inbox / "alias").symlink_to("../Notes")
    report = t.box.json("check")
    dupes = [i for i in report["issues"] if i["code"] == "duplicate-content"]
    t.eq(dupes, [], "a shortcut is not reported as a duplicate of what it points at")


@test
def test_a_blank_number_matches_nothing(t: Case) -> None:
    """`./os done ""` must not archive whatever the scanner reached first.

    Skills, helpers and anything not yet sorted carry no number, so an empty
    id compared equal to the first of them — and the argument guard only
    checked that *an* argument was there, not that it said anything."""
    t.box.run("save", "Ship the billing rewrite by Friday, migrate the schema")
    before = sorted(p.name for p in (t.box.root / ".claude" / "skills").iterdir())

    for command in ("show", "open", "edit", "done", "back"):
        proc = t.box.run(command, "", expect=1)
        t.ok("which one" in (proc.stdout + proc.stderr).lower(),
             f"`os {command} \"\"` asks which one instead of picking for you")
        proc = t.box.run(command, "   ", expect=1)
        t.ok("which one" in (proc.stdout + proc.stderr).lower(),
             f"`os {command} \"   \"` too")

    t.eq(sorted(p.name for p in (t.box.root / ".claude" / "skills").iterdir()), before,
         "and nothing was archived out from under the toolkit")


@test
def test_a_name_collision_keeps_its_number(t: Case) -> None:
    """Item folders are called `2.01_ship-the-rewrite`. That dot is not an
    extension, and splitting on it turned a collision into
    `2-2.01_ship-the-rewrite` — a folder that no longer starts with its own
    number, so it stops announcing what it is at a glance."""
    t.box.run("new", "project", "Ship the billing rewrite")
    t.box.run("sort")
    item = next(i for i in t.box.items() if i["id"] == "W.01")
    folder = t.box.root / item["path"]
    t.ok(folder.is_dir(), "the project is a folder")

    # something is already sitting where the archive wants to put it
    landing = t.box.root / "Archive" / engine.today()[:4] / "Work" / folder.name
    landing.mkdir(parents=True)
    (landing / "keep.txt").write_text("someone else's")

    t.box.run("done", "W.01")
    moved = [p for p in landing.parent.iterdir() if p.name != landing.name]
    t.eq(len(moved), 1, "the archived copy lands beside it rather than on top of it")
    t.ok(moved[0].name.startswith("W.01_"),
         f"and still starts with its own number (got {moved[0].name!r})")
    t.ok((landing / "keep.txt").exists(), "nothing was overwritten")


@test
def test_undo_puts_back_a_shortcut_that_dangles(t: Case) -> None:
    """`exists()` follows a link, so a shortcut whose target is gone reads as
    "not there" — and undo used to leave it behind in whichever bucket the sort
    had put it in, reporting that it could not be put back."""
    inbox = t.box.root / "Notes"
    (inbox / "old-alias").symlink_to(t.box.tmp / "deleted-long-ago")
    t.ok(not (inbox / "old-alias").exists() and (inbox / "old-alias").is_symlink(),
         "the shortcut dangles, as an old alias does")

    t.box.run("sort")
    t.ok(not (inbox / "old-alias").is_symlink(), "sort files it")

    proc = t.box.run("undo")
    t.ok((inbox / "old-alias").is_symlink(), "undo puts the shortcut back in the inbox")
    t.ok("couldn't put this one back" not in proc.stdout,
         "and does not report it as lost")


@test
def test_a_guess_it_was_unsure_about_comes_back(t: Case) -> None:
    """The sorter flags what it could not place confidently.

    That flag was only ever written onto folder-shaped items, and nothing
    surfaced it afterwards — so "I wasn't sure about this" was said once and
    then lost. It has to survive into the file and reappear in `os tidy`."""
    t.box.run("save", "qwerty zxcvb")                      # nothing to go on
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")

    unsure = [i for i in t.box.items() if "needs-review" in (i["flags"] or [])]
    t.gte(len(unsure), 1, "the low-confidence item carries the flag on disk")
    confident = [i for i in t.box.items()
                 if i["kind"] == "project" and "needs-review" not in (i["flags"] or [])]
    t.gte(len(confident), 1, "and a clear one does not")

    report = t.box.json("tidy")
    t.gte(len(report["unsure"]), 1, "`os tidy` reports what it was unsure about")
    t.ok(any(u["id"] == unsure[0]["id"] for u in report["unsure"]),
         "naming the same item")
    t.ok("sure" in t.box.run("tidy").stdout.lower(),
         "and says so in the report a person reads")

    # the capture timestamp is scaffolding and must not survive filing
    for item in t.box.items():
        path = t.box.root / item["path"]
        spine = path / "README.md" if path.is_dir() else path
        if spine.exists() and spine.suffix == ".md":
            meta, _ = engine.parse_frontmatter(spine.read_text())
            t.ok("saved" not in meta, f"{item['path']} kept a `saved:` timestamp")


@test
def test_it_does_not_quietly_eat_the_disk(t: Case) -> None:
    """Three things here grow on their own. All three have to stay bounded."""
    # 1. the undo cache keeps content aside so undo can restore what a file said
    t.box.fill_inbox(40)
    t.box.run("sort")
    for i in range(25):
        t.box.run("save", f"a passing thought number {i} about the deadline")
    runs = list((t.box.root / ".os" / "cache" / "undo").glob("*"))
    keep = json.loads((t.box.root / ".os" / "config.json").read_text())["behaviour"]["keep_undo_steps"]
    t.ok(len(runs) <= keep + 1, f"the undo cache is pruned to ~{keep} runs (found {len(runs)})")

    # 2. backups are full copies, so a low ceiling matters more than a long history
    behaviour = json.loads((t.box.root / ".os" / "config.json").read_text())["behaviour"]
    t.ok(behaviour["keep_backups"] <= 3,
         f"backups keep a short history by default (found {behaviour['keep_backups']})")
    for _ in range(6):
        t.box.run("backup")     # fast enough that the second-resolution stamps collide
    zips = list((t.box.root / ".os" / "backups").glob("*.zip"))
    t.eq(len(zips), behaviour["keep_backups"], "and older ones really are dropped")
    t.ok(len({z.name for z in zips}) == len(zips),
         "backups made in the same second do not overwrite each other")
    newest = max(z.stat().st_mtime for z in zips)
    t.ok(all(newest - z.stat().st_mtime < 60 for z in zips),
         "the ones kept are the newest, whatever they are named")
    said = t.box.run("backup").stdout
    t.ok("in all" in said, "`os backup` says what the whole set costs, not just the new one")

    # 3. the index is derived and rebuilt, so what matters is that its cost per
    #    item is small and flat — not that it is smaller than the content, which
    #    it never will be for a folder full of one-line notes
    items = len([i for i in t.box.items() if i["bucket"] != engine.TOOLKIT])
    index = ((t.box.root / "INDEX.md").stat().st_size
             + (t.box.root / ".os" / "registry.json").stat().st_size)
    per_item = index / max(items, 1)
    t.ok(per_item < 1_500,
         f"the index costs {per_item:.0f} bytes an item, so 1,000 items is under 1.5 MB")


@test
def test_every_kind_of_finding_stays_visible(t: Case) -> None:
    """Forty near-duplicates must not push a one-off warning off the screen."""
    for i in range(30):
        (t.box.root / "Notes" / f"same-{i}.md").write_text(
            "# Client relationships\n\nReference material about the client.\n")
    t.box.run("sort")
    (t.box.root / "Notes" / ".DS_Store").write_text("")

    shown = t.box.run("check", expect=None).stdout
    codes = {i["code"] for i in t.box.json("check", expect=None)["issues"]}
    t.gte(len(codes), 2, "the folder has several different kinds of finding")
    for code in codes:
        sample = next(i for i in t.box.json("check", expect=None)["issues"]
                      if i["code"] == code)
        t.ok(sample["message"][:40] in shown,
             f"'{code}' is visible in the report, not crowded out")
    repeated = [c for c in codes
                if len([i for i in t.box.json("check", expect=None)["issues"]
                        if i["code"] == c]) > 4]
    if repeated:
        t.ok("more like" in shown, f"repeats of {repeated[0]} are rolled up by kind")


@test
def test_bookkeeping_never_becomes_content(t: Case) -> None:
    """A folder full of "save — 1 change" buries the work it is supposed to hold.

    Every operation is already in state.json. None of it may leak into anything
    a person opens — not into a note, not into a project's README."""
    for _ in range(3):
        t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    t.box.run("new", "project", "Ship the mobile app")
    t.box.run("sort")
    t.box.run("undo")

    history = json.loads((t.box.root / ".os" / "state.json").read_text())["history"]
    t.gte(len(history), 4, "state.json keeps the full operational record")

    written = [p for bucket in ("Work", "Notes")
               for p in (t.box.root / bucket).rglob("*.md")]
    t.ok(written, "and the person's own files are there to check")
    for path in written:
        text = path.read_text()
        for machine in ("change(s)", "step(s)", "— 1 change", "reverted"):
            t.ok(machine not in text,
                 f"no machine chatter reached {path.name} ({machine!r})")


@test
def test_everything_new_can_be_taken_back(t: Case) -> None:
    """`os undo` promises to reverse the last thing it did. `os new` did not.

    Worse, undo reported "0 change(s) reversed" as a success, so the folder
    claimed to have undone something while the thing sat there untouched."""
    cases = [("work", "Ship the mobile app", "Work"),
             ("ongoing", "Keep the tests green", "Work"),
             ("note", "A hand made note", "Notes"),
             ("skill", "Weekly digest", ".claude/skills/weekly-digest"),
             ("agent", "Summariser", ".claude/agents/summariser.md")]

    def contents(where: str):
        target = t.box.root / where
        if where.startswith(".claude"):
            return [where] if target.exists() else []
        return [p.name for p in target.iterdir() if p.name != ".gitkeep"]

    for kind, title, where in cases:
        t.box.run("new", kind, title)
        t.ok(contents(where), f"`os new {kind}` made something")
        t.box.run("undo")
        t.eq(contents(where), [], f"`os undo` takes back `os new {kind}`")

    t.eq(list((t.box.root / "Work").glob("*/")), [],
         "no empty folder is left behind")

    # and when there is genuinely nothing left, it must not claim success
    t.box.run("new", "project", "Temporary thing")
    made = next(i for i in t.box.items() if i["kind"] == "project")
    shutil.rmtree(t.box.root / made["path"])
    proc = t.box.run("undo", expect=1)
    t.ok("nothing left to reverse" in proc.stdout,
         "undo says so instead of reporting a success it did not have")


@test
def test_a_folder_of_photos_is_not_a_note(t: Case) -> None:
    """What a folder holds decides what it is, not the fact it is a folder."""
    photos = t.box.tmp / "holiday-photos" / "2026"
    photos.mkdir(parents=True, exist_ok=True)
    (photos / "a.jpg").write_bytes(b"\xff\xd8\xff")
    (photos.parent / "b.jpg").write_bytes(b"\xff\xd8\xff")

    docs = t.box.tmp / "reference-docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "a.md").write_text("Reference material on typography and layout. A cheat sheet.\n")

    mixed = t.box.tmp / "mixed-bag"
    mixed.mkdir(parents=True, exist_ok=True)
    (mixed / "chart.png").write_bytes(b"\x89PNG")
    (mixed / "notes.md").write_text("Notes about the quarter and the revenue numbers.\n")

    for folder in (photos.parent, docs, mixed):
        t.box.run("save", str(folder))

    placed = {i["title"]: i for i in t.box.items() if i["kind"] in ("asset", "note")}
    photos_item = next(i for k, i in placed.items() if "holiday" in k.lower())
    t.eq(photos_item["kind"], "asset", "a folder with nothing readable in it is files")
    t.eq(photos_item["bucket"], "Notes",
         "and it shelves with everything else you look up later")
    for key in ("reference", "notes about", "mixed"):
        item = next((i for k, i in placed.items() if key in k.lower()), None)
        if item:
            t.eq(item["kind"], "note", f"a folder with prose in it is still a note ({key})")


@test
def test_two_things_cannot_keep_one_number(t: Case) -> None:
    """A number is only permanent if it is unique.

    Copies, restores from a backup and hand edits can all produce two items
    claiming the same one. Sorting has to settle it: older keeps the number."""
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    t.box.run("save", "Reference notes on postgres indexes")
    original = next(i for i in t.box.items() if i["kind"] == "project")

    clash = t.box.root / "Work" / f"{original['id']}_a-hand-made-clash"
    clash.mkdir(parents=True, exist_ok=True)
    shutil.copy2(t.box.root / original["path"] / "README.md", clash / "README.md")
    t.box.run("index")
    codes = {i["code"] for i in t.box.json("check", expect=1)["issues"]}
    t.ok("duplicate-id" in codes, "the clash is reported")

    t.box.run("sort")
    ids = [i["id"] for i in t.box.items() if i["id"]]
    t.eq(len(ids), len(set(ids)), f"sorting settles it: {sorted(ids)}")

    # the renumbered one's own front matter has to agree with its new name
    for item in t.box.items():
        if not item["id"]:
            continue
        path = t.box.root / item["path"]
        spine = path / "README.md" if path.is_dir() else path
        if not spine.exists() or spine.suffix != ".md":
            continue
        meta, _ = engine.parse_frontmatter(spine.read_text())
        t.eq(str(meta.get("id", "")), item["id"],
             f"{item['path']} says id {meta.get('id')!r} but is filed as {item['id']}")
    t.eq([i for i in t.box.json("check", expect=None)["issues"]
          if i["code"] == "duplicate-id"], [], "and the report is clean afterwards")


@test
def test_a_dropped_in_skill_keeps_its_name(t: Case) -> None:
    """Skills and helpers are found by name, so the name must survive filing."""
    folder = t.box.root / "Notes" / "some-folder-name"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        "---\nname: weekly-thing\ndescription: Does the weekly thing. Use when the "
        "user says do the weekly thing.\n---\n\n# Weekly thing\n\n## Do this\n1. it\n")
    (t.box.root / "Notes" / "whatever.md").write_text(
        "---\nname: summariser\ndescription: Summarises long documents. Use when the "
        "user asks for a summary.\ntools: Read, Grep\n---\n\nYou summarise things.\n")
    t.box.run("sort")

    t.ok((t.box.root / ".claude" / "agents" / "summariser.md").exists(),
         "a helper is filed under its `name:`, not its opening sentence")
    skills = [p.name for p in (t.box.root / ".claude" / "skills").iterdir() if p.is_dir()]
    t.ok("weekly-thing" in skills,
         f"a skill keeps the name people will type ({skills})")
    t.eq([i for i in t.box.json("check", expect=None)["issues"]
          if i["level"] == "error"], [], "and both are valid")


@test
def test_search_returns_your_work_not_the_scaffolding(t: Case) -> None:
    """Two ways search used to hand back things nobody was looking for."""
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    t.box.run("new", "project", "Ship the mobile app")

    def numbered(*args: str) -> list:
        return [h for h in t.box.json("find", *args) if h["id"]]

    # 1. every project carries the same blueprint prompts, in HTML comments.
    #    Searching their words matched every project in the folder.
    for phrase in ("specific", "argue", "blueprint"):
        for hit in numbered(phrase):
            body = (t.box.root / hit["path"]).read_text() if (t.box.root / hit["path"]).is_file() \
                else (t.box.root / hit["path"] / "README.md").read_text()
            visible = re.sub(r"<!--.*?-->", "", body, flags=re.S)
            t.ok(phrase in visible.lower(),
                 f"'{phrase}' matched {hit['id']} only inside a template comment")

    # 2. skills and helpers are the machinery, not the person's work
    for hit in numbered("the"):
        t.ok(hit["kind"] not in ("skill", "agent", "hook"),
             f"search returned {hit['kind']} '{hit['title']}' unasked")
    everything = t.box.json("find", "the")
    t.ok(not any(h["kind"] in ("skill", "agent", "hook") for h in everything),
         "no skills or helpers in a plain search")
    # ...but they are reachable on purpose
    skills = t.box.json("find", "tidy", "--kind", "skill")
    t.ok(any(h["kind"] == "skill" for h in skills), "--kind skill still finds them")

    # 3. and a snippet never shows template scaffolding
    for hit in t.box.json("find", "pricing"):
        t.ok("<!--" not in hit["snippet"] and "-->" not in hit["snippet"],
             f"snippet leaked a comment: {hit['snippet'][:50]!r}")


@test
def test_numbers_stay_in_order_past_ninety_nine(t: Case) -> None:
    """`N.100` sorts before `N.11` as text, which is not the order anyone means."""
    order = [engine.id_order(f"N.{n:02d}") for n in (1, 2, 9, 10, 11, 99, 100, 101, 110)]
    t.eq(order, sorted(order), "the sort key orders 1, 2, 9, 10, 11, 99, 100, 101, 110")
    t.ok(engine.id_order("W.03") > engine.id_order("N.99"), "the letter wins first")
    t.ok(engine.id_order("") > engine.id_order("W.99"), "anything without a tag sorts last")
    # a folder written before the letters still sorts, and sorts numerically
    legacy = [engine.id_order(f"2.{n:02d}") for n in (1, 2, 9, 10, 99, 100)]
    t.eq(legacy, sorted(legacy), "an older numeric tag still orders 1, 2, 9, 10, 99, 100")
    t.ok(engine.id_order("2.01") < engine.id_order("10.01"),
         "and 2 still sorts before 10, not after it as text would")
    for junk in ("—", "", "abc", "2.", ".4", None):
        engine.id_order(junk)   # must never raise

    # and the index really is in order. Past a dozen per folder the sorter makes
    # category sections, and each section is its own table — so check each table,
    # not the file as a whole.
    t.box.fill_inbox(140)
    t.box.run("sort")

    tables, current = [], []
    for line in (t.box.root / "INDEX.md").read_text().split("\n"):
        row = re.match(r"^\| `([A-Za-z0-9]+\.\d+)`", line)
        if row:
            current.append(row.group(1))
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)

    t.gte(sum(len(x) for x in tables), 100, "enough items listed to be worth checking")
    t.gte(len(tables), 1, "INDEX.md has tables in it")
    out_of_order = []
    for table in tables:
        keys = [engine.id_order(i) for i in table]
        if keys != sorted(keys):
            out_of_order.append(table[:6])
    t.eq(out_of_order, [], f"every table in INDEX.md ascends numerically")


@test
def test_one_thing_gets_one_number(t: Case) -> None:
    """The reported bug: `save` files it as 2.01, then `new project` makes 2.02.

    `save` decides for itself that a sentence reading like work is a project.
    Anything that would create a second copy has to say so first."""
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    projects = [i for i in t.box.items() if i["kind"] == "project"]
    t.eq(len(projects), 1, "the save made exactly one project")

    # the shorter title sits inside the longer one — lengths differ a lot, and a
    # plain similarity ratio would never catch it
    refused = t.box.run("new", "project", "Redesign the pricing page", expect=1)
    t.ok("you already have" in refused.stderr, "`os new` refuses the near-duplicate")
    t.ok(projects[0]["id"] in refused.stderr, "and names the one that already exists")
    t.ok("--anyway" in refused.stderr, "and says how to override it")
    t.eq(len([i for i in t.box.items() if i["kind"] == "project"]), 1, "nothing was created")

    # saying you mean it works, and does not eat the title
    t.box.run("new", "project", "Redesign the pricing page", "--anyway")
    made = [i for i in t.box.items() if i["kind"] == "project"]
    t.eq(len(made), 2, "--anyway lets a deliberate second one through")
    t.ok(not any("anyway" in i["title"].lower() for i in made),
         f"and --anyway is not swallowed into the title ({[i['title'] for i in made]})")

    # different work is never blocked
    for title in ("Ship the mobile app", "Hire a designer", "Fix the CI pipeline"):
        t.box.run("new", "project", title)
    t.eq(len([i for i in t.box.items() if i["kind"] == "project"]), 5,
         "genuinely different things go straight through")

    # save warns but must never refuse — losing a thought is worse than a double
    saved = t.box.run("save", "Ship the mobile app soon")
    t.ok("looks like the same thing" in saved.stdout, "a near-duplicate save warns")
    t.ok("Ship the mobile app" in saved.stdout, "and names what it clashes with")
    t.eq(t.box.inbox_count(), 0, "but it still saved it — capture never refuses")


@test
def test_a_saved_project_has_a_shape(t: Case) -> None:
    """Most projects are born from `save`, not `new`. They need the same bones.

    A project with no Next action and no Log is one the rest of the system —
    and any AI following AGENTS.md — cannot actually help with."""
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    project = next(i for i in t.box.items() if i["kind"] == "project")
    text = (t.box.root / project["path"] / "README.md").read_text()
    for heading in ("## What good looks like", "## Next action", "## Decisions", "## Log"):
        t.ok(heading in text, f"saved work has {heading}")
    t.ok("Redesign the pricing page" in text, "and every word the person wrote survives")
    t.eq(project.get("status"), "pushing", "work with a next action is filed as pushing")

    t.box.run("save", "Keep the tests green, checked every week, this never ends")
    ongoing = next(i for i in t.box.items()
                   if i["kind"] == "project" and i.get("status") == "holding")
    text = (t.box.root / ongoing["path"] / "README.md").read_text()
    t.ok("## How often" in text, "something held gets the cadence half of the blueprint")
    t.ok("## What good looks like" in text, "and asks the same first question")

    # something that already has a shape is never rewritten
    folder = t.box.root / "Notes" / "mine"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text(
        "---\ntype: project\n---\n\n# My own project\n\n"
        "## My own heading\nThis must survive exactly.\n")
    t.box.run("sort")
    mine = next(i for i in t.box.items() if i["title"] == "My own project")
    kept = (t.box.root / mine["path"] / "README.md").read_text()
    t.eq(mine["bucket"], "Work", "a folder declaring type: project lands in Projects")
    t.ok("## My own heading" in kept, "its own headings survive")
    t.ok("## What good looks like" not in kept, "and no blueprint is forced on top of them")

    # undo still puts everything back
    before = t.box.inbox_count()
    t.box.run("undo")
    t.gte(t.box.inbox_count(), before, "undo returns what it filed")


@test
def test_a_dropped_folder_is_read_not_guessed(t: Case) -> None:
    """README.md is hidden from the scanner; whoever classifies must still read it."""
    notes = t.box.root / "Notes" / "design-notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "README.md").write_text(
        "# Typography reference\n\nNotes on the modular scale. Reference material "
        "about spacing, typography and layout defaults. Cheat sheet only.\n")
    code = t.box.root / "Notes" / "codebase"
    code.mkdir(parents=True, exist_ok=True)
    (code / "package.json").write_text("{}")
    t.box.run("sort")

    typo = next((i for i in t.box.items() if "ypography" in i["title"]), None)
    t.ok(typo is not None, "the folder was titled from its README, not its folder name")
    t.eq(typo["bucket"], "Notes", "and filed on what the README says")
    repo = next((i for i in t.box.items() if i["title"].lower() == "codebase"), None)
    t.ok(repo is not None and repo["bucket"] == "Work",
         "a folder that looks like code is a project")


@test
def test_one_item_can_be_looked_at(t: Case) -> None:
    """`os show 2.04` — and `os 2.04` — without opening a file."""
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    project = next(i for i in t.box.items() if i["kind"] == "project")
    ident = project["id"]

    readme = t.box.root / project["path"] / "README.md"
    text = readme.read_text()
    text = text.replace("## Next action\n- [ ] ", "## Next action\n- [ ] Get the layout in front of Sam")
    text = text.replace("## Decisions", "## Decisions\n- 2026-08-20 - three tiers, not four")
    readme.write_text(text)
    t.box.run("index")

    for args in ([ident], ["show", ident]):
        out = t.box.run(*args).stdout
        t.ok(ident in out, f"`os {' '.join(args)}` names the number")
        t.ok("Redesign the pricing page" in out, f"`os {' '.join(args)}` names the thing")
        t.ok("Get the layout in front of Sam" in out, f"`os {' '.join(args)}` shows the next action")
        t.ok("three tiers" in out, f"`os {' '.join(args)}` shows what was decided")
        for jargon in ("front matter", "taxonomy", "spine", "bucket"):
            t.ok(jargon not in out.lower(), f"`os show` never says '{jargon}'")

    t.box.run("show", "W.99", expect=1)
    t.box.run("W.99", expect=1)


@test
def test_files_dropped_in_by_hand_get_noticed(t: Case) -> None:
    """Drag files into the inbox and nothing should silently swallow them."""
    quiet = t.box.run("index", "--notify")
    t.eq(quiet.stdout.strip(), "", "an empty inbox says nothing")

    (t.box.root / "Notes" / "budget.csv").write_text("q,total\nQ3,1\n")
    (t.box.root / "Notes" / "scratch.md").write_text("# scratch\n")
    said = json.loads(t.box.run("index", "--notify").stdout)
    message = said["systemMessage"]
    t.ok("budget.csv" in message and "scratch.md" in message, "it names the files")
    t.ok("./os sort" in message, "it says how to file them")
    t.ok("without asking" in message, "it tells the AI to ask first")
    t.eq(t.box.inbox_count(), 2, "and noticing them does not move them")


@test
def test_tags_are_earned_not_invented(t: Case) -> None:
    """Junk tags on every short note make the whole folder look stupid."""
    t.box.run("save", "The billing token refresh dies every Friday night, before the release")
    short = next(i for i in t.box.items() if i["kind"] == "project")
    t.eq(short["tags"], [], "a one-line thought gets no invented tags")

    (t.box.root / "Notes" / "long.md").write_text(
        "# Migration plan\n\nThe billing service runs on the legacy schema. Migrating "
        "the billing tables is the work. Each billing record needs backfilling, and the "
        "billing job keeps running through the migration. Migration order: billing first.\n")
    t.box.run("sort")
    long_one = next(i for i in t.box.items()
                    if i["kind"] in ("project", "note") and "igration" in i["title"])
    t.ok("billing" in long_one["tags"],
         f"a word used over and over does earn a tag (got {long_one['tags']})")


@test
def test_the_name_is_the_same_everywhere(t: Case) -> None:
    """A half-finished rename is the easiest way to ship something embarrassing."""
    name = json.loads((t.box.root / ".os" / "config.json").read_text())["name"]
    t.ok(name.strip() and name[0].isupper(), f"the folder declares a real name ({name!r})")

    # the suite itself has to name old names in order to check for them
    skip = {".git", "backups", "cache", "__pycache__", ".DS_Store", "tests"}
    stale = []
    for path in sorted(t.box.root.rglob("*")):
        if not path.is_file() or any(part in skip for part in path.parts):
            continue
        if path.suffix in (".zip", ".pyc"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for other in ("meridian",):          # every name this has ever had
            if other in text.lower() and other != name.lower():
                stale.append(f"{path.relative_to(t.box.root)} still says {other!r}")
    t.eq(stale, [], f"no file mentions an old name: {stale[:3]}")

    # and the name reaches the places a person actually looks
    t.ok(name in t.box.run("help").stdout, "`os help` shows the name")
    t.ok(name in (t.box.root / "README.md").read_text(), "the README shows the name")
    t.ok(name in (t.box.root / "AGENTS.md").read_text(), "AGENTS.md shows the name")
    t.box.run("index")
    t.ok(name in (t.box.root / "INDEX.md").read_text(), "INDEX.md shows the name")
    t.box.run("backup")
    zips = list((t.box.root / ".os" / "backups").glob("*.zip"))
    t.ok(zips and zips[0].name.startswith(name.lower()),
         f"backups are named after it ({[z.name for z in zips]})")


@test
def test_it_survives_being_used_wrongly(t: Case) -> None:
    """Every one of these was a real bug found by trying to break it."""
    # `Path("")` is the current directory: an empty argument once tried to copy
    # the whole folder into its own inbox, and hung
    for junk in ("", "     ", ".", "...!!!"):
        proc = t.box.run("save", junk, expect=1)
        t.ok("tell me what to save" in proc.stderr, f"`save {junk!r}` is refused, kindly")
    t.eq(t.box.inbox_count(), 0, "nothing junk reached the inbox")

    # a path that is not there must not be filed as if it were prose
    for missing in ("/no/such/file.txt", "./notes/thing.md", "report.pdf"):
        proc = t.box.run("save", missing, expect=1)
        t.ok("there is no file at" in proc.stderr, f"`save {missing}` says the file is missing")
    # ...but a real sentence containing a dot is still a sentence
    t.box.run("save", "Ship v2.0 before Friday")
    t.gte(len([i for i in t.box.items() if i["kind"] == "project"]), 1,
          "a sentence with a version number is still a sentence")

    # the folder must refuse to swallow itself
    for suicidal in (str(t.box.root), str(t.box.root / "Notes"), ".."):
        t.box.run("save", suicidal, expect=1)
    t.eq(t.box.inbox_count(), 0, "no self-copy reached the inbox")


@test
def test_files_never_lose_their_card(t: Case) -> None:
    """A file on the Notes shelf carries a card; separating them orphans both."""
    for i in range(16):
        (t.box.root / "Notes" / f"figures-{i}.csv").write_text(f"quarter,total\nQ{i},{i}00\n")
    t.box.run("sort")   # enough of them to force category folders, which moves them

    def orphans() -> tuple:
        root = t.box.root / "Notes"
        cards, lonely = [], []
        for card in root.rglob("*.card.md"):
            if not card.with_name(card.name[: -len(".card.md")]).exists():
                cards.append(str(card))
        for f in root.rglob("*"):
            if f.is_file() and not f.name.endswith(".card.md") and not f.name.startswith("."):
                if not f.with_name(f.name + ".card.md").exists():
                    lonely.append(str(f))
        return cards, lonely

    t.eq(orphans(), ([], []), "re-shelving keeps every file with its card")

    asset = next(i for i in t.box.items() if i["kind"] == "asset")
    t.box.run("done", asset["id"])
    t.eq(orphans(), ([], []), "archiving takes the card along")
    t.box.run("back", asset["id"])
    t.eq(orphans(), ([], []), "restoring brings the card back")
    t.box.run("undo")
    t.eq(orphans(), ([], []), "undo keeps them together too")

    # and `open` points at the file, not at the card describing it
    path = t.box.run("open", asset["id"]).stdout.strip().split("\n")[0]
    t.ok(not path.endswith(".card.md"), "`os open` gives the file itself")


@test
def test_a_broken_settings_file_says_which_one(t: Case) -> None:
    """These are files people hand-edit, so a typo must name itself."""
    for name in ("words.json", "config.json"):
        path = t.box.root / ".os" / name
        good = path.read_text()
        path.write_text('{ "oops": ')
        proc = t.box.run("status", expect=1)
        t.ok(f".os/{name}" in proc.stderr, f"a broken {name} names {name}")
        t.ok("comma" in proc.stderr or "quote" in proc.stderr,
             f"a broken {name} suggests what to look for")
        path.write_text(good)

    # a missing state file is rebuilt rather than fatal
    (t.box.root / ".os" / "state.json").unlink()
    t.box.run("sort")
    t.ok((t.box.root / ".os" / "state.json").exists(), "a lost state file is rebuilt")


@test
def test_two_things_can_share_a_title(t: Case) -> None:
    """Identical titles once crashed the check that exists to find them."""
    for name in ("one.md", "two.md", "three.md"):
        (t.box.root / "Notes" / name).write_text("# Same Title Here\n\nReference material.\n")
    t.box.run("sort")
    result = t.box.json("check", expect=None)
    t.ok(any(i["code"] == "near-duplicate" for i in result["issues"]),
         "three items with one title are reported, not fatal")


@test
def test_data_named_as_prose_is_still_data(t: Case) -> None:
    """A .md file full of bytes must not be read as prose."""
    (t.box.root / "Notes" / "screenshot.md").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(400))
    (t.box.root / "Notes" / "real.md").write_text("# Real note\n\nAbout the billing deadline.\n")
    t.box.run("sort")
    placed = {Path(i["path"]).name: i for i in t.box.items() if i["kind"] in ("asset", "note")}
    binary = next((i for n, i in placed.items() if "screenshot" in n), None)
    t.ok(binary is not None, "the binary file was filed somewhere")
    t.eq(binary["kind"], "asset", "a .md full of bytes is kept as a file, not read as text")
    t.ok("screenshot" in binary["title"].lower(),
         f"it keeps a sane name instead of noise (got {binary['title']!r})")


@test
def test_search_forgives_how_people_type(t: Case) -> None:
    """"I know I wrote it down somewhere" is exactly when you misspell it."""
    for text in (
        "The billing token refresh dies every Friday night, before the release on the 14th",
        "Notes on Postgres index types: btree is the default, gin is for arrays",
        "Meeting with the accountant about quarterly filing",
        "How to set up a mobile dev environment on a new laptop",
        "Debugging the jubilee campaign landing page",
    ):
        t.box.run("save", text)

    def found(*args: str) -> list:
        return t.box.json("find", *args)

    # the word as typed
    t.ok(any("billing" in h["title"].lower() for h in found("billing")),
         "an exact word is found")
    # plurals and other endings, both directions
    for query, want in (("refreshes", "refresh"), ("meetings", "meeting"),
                        ("postgress", "postgres"), ("types", "type")):
        hits = found(query)
        t.ok(any(want in (h["title"] + h["snippet"]).lower() for h in hits),
             f"'{query}' finds '{want}'")
    # a plain typo
    hits = found("accountnt")
    t.ok(any("accountant" in (h["title"] + h["snippet"]).lower() for h in hits),
         "a misspelling still finds the note")
    # ...but a stem must not match in the middle of an unrelated word
    for hit in found("biling"):
        blob = (hit["title"] + hit["snippet"]).lower()
        t.ok("mobile" not in blob and "jubilee" not in blob,
             f"'biling' must not drag in {hit['title']!r}")
    # exact matching is unchanged: a substring anywhere still counts
    t.ok(any("debugging" in h["title"].lower() for h in found("bug")),
         "an exact substring still matches inside a longer word")
    # and nonsense is still nonsense
    t.eq(found("zzzzqqqxx"), [], "a word that is in nothing finds nothing")


@test
def test_it_stays_quick_as_it_grows(t: Case) -> None:
    """Speed is a feature here: `brief` runs on every single session start.

    The checks are shaped so cost grows with the folder, not with the square of
    it. A regression shows up as one of these blowing past its budget."""
    t.box.fill_inbox(220)
    t.box.run("sort")

    for command, budget in (("brief", 6.0), ("status", 6.0), ("check", 6.0)):
        start = time.time()
        t.box.run(command)
        spent = time.time() - start
        t.ok(spent < budget, f"`os {command}` took {spent:.1f}s on 220 items (budget {budget}s)")

    # duplicates are reported as groups, not as every pair inside a group
    dupes = [i for i in t.box.json("check", expect=None)["issues"]
             if i["code"] == "near-duplicate"]
    t.ok(len(dupes) < 40, f"duplicates come back grouped, not pair by pair ({len(dupes)})")


@test
def test_it_reads_ordinary_english(t: Case) -> None:
    """The filing has to work on how people actually write, not on keywords.

    This is the whole promise: you type a sentence, it lands somewhere sensible.
    If this test sags, the system is guessing and the user has to file by hand."""
    cases = [
        ("pushing", "Fix the login page, it 500s on Safari. Needs to be out before Monday."),
        ("pushing", "Write the grant application. Due the 30th."),
        ("pushing", "Plan Mia's birthday party for the 12th"),
        ("pushing", "Set up the new laptop this weekend"),
        ("pushing", "We need to move the database off Heroku before the bill renews"),
        ("pushing", "I need to write the Q3 report by Friday."),
        ("pushing", "Redesign the onboarding flow so new users get their first win fast"),
        ("pushing", "The billing token refresh dies every Friday night"),
        ("pushing", "The export button is broken on mobile"),
        ("pushing", "Login keeps failing for new accounts"),
        ("holding", "Ongoing: reply to every customer email within a day. That's the standard."),
        ("holding", "Go for a run every morning. No end date, just something I hold to."),
        ("holding", "Keep on top of the invoices. Check it every Friday."),
        ("holding", "Keep the codebase green: no failing tests, no lint errors, every week."),
        ("note", "TIL: you can use --json on every command here"),
        ("note", "The difference between GIN and GiST indexes, for reference"),
        ("note", "Idea for a podcast about small software businesses"),
        ("note", "Quote worth keeping: 'the plural of anecdote is not data'"),
        ("note", "Notes on Postgres index types: btree is the default, gin is for arrays"),
        ("note", "Standup notes: blocked on the API key, Sam is chasing it"),
        ("note", "Met with the accountant today. We agreed to switch to quarterly filing."),
    ]
    classifier = engine.Classifier(engine.Zenith(t.box.root))
    wrong = []
    for want, text in cases:
        got, _score, _all = classifier.score_intent(text[:60], text)
        if got != want:
            wrong.append(f"{text[:44]!r} read as {got}, not {want}")
    t.eq(wrong, [], f"{len(wrong)} of {len(cases)} ordinary sentences were misread")

    # short keywords must match whole words: "ci" must not fire inside "pricing"
    for text, forbidden in (("Redesign the pricing page", "engineering"),
                            ("Already read the header", "marketing"),
                            ("I had a bad idea", "engineering")):
        got, _s, scores = classifier.score_domain(text, text, "")
        t.ok(forbidden not in scores,
             f"{text!r} must not score as {forbidden} on a fragment match")


@test
def test_taxonomy_is_teachable(t: Case) -> None:
    """Adding your own vocabulary changes where things land."""
    tax_path = t.box.root / ".os" / "words.json"
    tax = json.loads(tax_path.read_text())
    tax["domains"]["northwind"] = {
        "label": "Northwind",
        "keywords": ["northwind", "zorblat", "flimbus"],
        "extensions": [],
    }
    tax_path.write_text(json.dumps(tax, indent=2))

    (t.box.root / "Notes" / "mystery.md").write_text(
        "# Zorblat rollout\n\nNotes on the flimbus configuration for northwind. Reference material.\n")
    t.box.run("sort")
    item = next(i for i in t.box.items() if "zorblat" in i["path"].lower()
                or "Zorblat" in (i["title"] or ""))
    t.eq(item["domain"], "northwind", "the new domain was learned and applied")


@test
def test_holds_up_at_three_hundred_items(t: Case) -> None:
    """Well past the promise: 300 mixed items, still shallow, still stable."""
    planted = t.box.fill_inbox(300)
    started = time.time()
    t.box.run("sort")
    elapsed = time.time() - started
    t.ok(elapsed < 180, f"300 items sorted in {elapsed:.1f}s")
    t.eq(t.box.inbox_count(), 0, "the inbox emptied completely")

    filed = [i for i in t.box.items() if i["bucket"] != engine.TOOLKIT]
    t.gte(len(filed), 300, "every item is individually indexed, none swallowed by a folder")

    # nothing lost
    lost = [m for m, _, _ in planted if t.box.locate(m) is None]
    t.eq(lost, [], f"nothing was lost ({len(lost)} missing)")

    # every category at every level is properly marked
    for bucket in ("Work", "Notes"):
        base = t.box.root / bucket
        if not base.exists():
            continue
        for node in base.rglob("*"):
            if not node.is_dir() or engine.ignored(node):
                continue
            rel = node.relative_to(base)
            # anything at or below an ID'd folder is item content, not structure
            if any(engine.ID_RE.match(part) for part in rel.parts):
                continue
            t.ok((node / ".category").exists(),
                 f"{bucket}/{rel} is a marked category at every level")

    # shallow, always
    t.ok(max(len(i["trail"]) for i in filed) <= 2, "nothing is more than 2 levels below its bucket")

    # unique IDs at scale
    ids = [i["id"] for i in filed if i["id"]]
    t.eq(len(set(ids)), len(ids), "every ID is unique at 300 items")

    # stable
    t.eq(t.box.json("sort")["moves"], [], "a second sort at scale moves nothing")
    t.eq(t.box.json("sort")["moves"], [], "and a third moves nothing either")

    # a big healthy folder is not reported as a sick one
    health = t.box.json("doctor", expect=None)
    t.eq([i for i in health["issues"] if i["level"] == "error"], [], "no errors at scale")
    t.gte(health["score"], 70, "a large, healthy folder still scores well")
    t.ok(len(health["issues"]) < 200, "the report stays readable — findings are rolled up")

    # search stays fast
    t0 = time.time()
    t.box.json("find", "postgres index")
    t.ok(time.time() - t0 < 15, "search stays responsive at 300 items")


@test
def test_a_fresh_copy_dates_itself(t: Case) -> None:
    """A template built one day and opened another must not arrive pre-dated."""
    import datetime
    today = datetime.date.today().strftime("%Y-%m-%d")

    # pretend this is a freshly downloaded copy carrying last year's dates
    state = t.box.root / ".os" / "state.json"
    data = json.loads(state.read_text())
    data["fresh"] = True
    state.write_text(json.dumps(data, indent=2))

    seeded = t.box.root / "Work" / "W.90_seeded-area"
    seeded.mkdir(parents=True, exist_ok=True)
    (seeded / "README.md").write_text(engine.compose(
        {"id": "W.90", "title": "Seeded area", "type": "area", "status": "active",
         "domain": "operations", "tags": ["seed"], "created": "2020-01-01",
         "updated": "2020-01-01"}, "# Seeded area\n\nShipped with the template.\n"))
    seeded_note = t.box.root / "Notes" / "N.90_seeded-note.md"
    seeded_note.write_text(engine.compose(
        {"id": "N.90", "title": "Seeded note", "type": "note", "status": "—",
         "domain": "operations", "tags": ["seed"], "created": "2020-01-01",
         "updated": "2020-01-01"}, "# Seeded note\n\nShipped with the template.\n"))

    proc = t.box.run("status")
    t.ok("dated today" in proc.stdout or "Welcome" in proc.stdout,
         "the first command announces that the copy was initialised")

    seeded_item = next(i for i in t.box.items() if i["id"] == "W.90")
    t.eq(seeded_item["status"], "holding",
         "a pre-merge `type: area` item is read as held, not put on the go")

    meta, _ = engine.parse_frontmatter((seeded / "README.md").read_text())
    t.eq(meta["created"], today, "seeded content is re-dated to today")
    t.eq(meta["updated"], today, "and its updated stamp too")
    note_meta, _ = engine.parse_frontmatter(seeded_note.read_text())
    t.eq(note_meta["created"], today, "a seeded note is re-dated too")
    t.ok(not json.loads(state.read_text()).get("fresh"), "the fresh flag was cleared")

    # and it does not fire twice
    second = t.box.run("status")
    t.ok("dated today" not in second.stdout, "initialisation happens exactly once")

    # setup is explicit, idempotent, and can name an owner
    t.box.run("setup", "--owner", "Sam", "--name", "Studio")
    config = json.loads((t.box.root / ".os" / "config.json").read_text())
    t.eq(config["owner"], "Sam", "setup records the owner")
    t.eq(config["name"], "Studio", "setup renames the system")
    t.box.run("setup")
    t.eq(json.loads((t.box.root / ".os" / "config.json").read_text())["owner"], "Sam",
         "running setup again keeps what was already set")


@test
def test_the_demo_leaves_no_trace(t: Case) -> None:
    """The demo must be safe to run on a folder that already has real work in it."""
    before, before_dirs = t.box.tree(), t.box.dirs()
    proc = t.box.run("demo")
    for beat in ("Write three things down", "Watch where they go",
                 "Find one again", "Change your mind"):
        t.ok(beat in proc.stdout, f"the demo shows the '{beat}' step")
    t.ok("work you're pushing" in proc.stdout and "a note" in proc.stdout
         and "work you keep up" in proc.stdout,
         "the demo tells the two phases of work apart, and a note from both")
    for jargon in ("capture", "taxonomy", "front matter", "bucket"):
        t.ok(jargon not in proc.stdout.lower(),
             f"the demo never says '{jargon}' at the user")

    after = t.box.tree()
    t.eq(set(after) - set(before), set(), "the demo added nothing")
    t.eq(set(before) - set(after), set(), "the demo removed nothing")
    t.eq(t.box.dirs() - before_dirs, set(),
         "the demo left no empty folders behind either")
    t.eq(t.box.inbox_count(), 0, "the inbox is empty again")

    # --keep leaves the three items filed
    t.box.run("demo", "--keep")
    filed = [i for i in t.box.items() if i["bucket"] in ("Work", "Notes")]
    t.gte(len(filed), 3, "--keep leaves the demo items in place")

    # and it refuses to sweep up real work
    (t.box.root / "Notes" / "mine.md").write_text("# Something real\n\nDo not touch.\n")
    refused = t.box.run("demo", expect=1)
    said = refused.stderr + refused.stdout
    t.ok("not filed" in said and "./os sort" in said,
         "the demo refuses to run over unfiled work and says exactly how to fix it")


@test
def test_help_exists_for_every_command(t: Case) -> None:
    """No command is undocumented, and no documented command is missing."""
    listed = t.box.run("help").stdout
    for command in ("save", "find", "open", "undo", "new", "close", "hold", "back",
                    "sort", "check", "tidy", "backup", "edit", "demo", "name"):
        t.ok(command in listed, f"`os {command}` appears in the main help")
    t.lte(len(listed.strip().split("\n")), 42,
          "the main help fits on one screen")
    for jargon in ("taxonomy", "front matter", "idempotent", "bucket", "anti-decay"):
        t.ok(jargon not in listed.lower(), f"the help never says '{jargon}'")

    for topic in sorted(engine.DETAIL):
        proc = t.box.run("help", topic)
        t.ok(len(proc.stdout.strip()) > 60, f"`os help {topic}` explains something")

    real = {name for name in engine.COMMANDS if name and not name.startswith("-")}
    documented = set(engine.DETAIL) | {"help"}
    aliases = {"st", "s", "add", "n", "start", "f", "o", "e", "file", "reindex",
               "search", "snapshot", "selftest", "init", "oops", "finish",
               "unarchive", "fix", "cleanup", "view", "look"}
    missing = real - documented - aliases
    t.eq(missing, set(), f"every command has help: missing {missing}")

    unknown = t.box.run("help", "definitely-not-a-command", expect=1)
    t.ok("nothing called" in unknown.stdout, "an unknown topic says so cleanly")

    # a near miss teaches instead of scolding
    for typo, want in (("delete", "close"), ("remember", "save"), ("organise", "sort")):
        proc = t.box.run(typo, expect=1)
        t.ok(want in proc.stderr, f"`os {typo}` points at `os {want}`")


@test
def test_a_list_survives_being_written_back(t: Case) -> None:
    """Front matter is read and rewritten on every filing pass, so anything it
    cannot round-trip is lost a little more each time.

    Lists were written bare: a tag reading "billing, urgent" came back as two
    tags, and one holding a `]` truncated every tag after it."""
    awkward = ["billing, urgent", 'a"b', "c]d", "plain", " lead", "trail ", "[x]"]
    for value in awkward:
        rendered = engine.render_frontmatter({"tags": [value, "after"]})
        back, _ = engine.parse_frontmatter(rendered + "\n\nbody\n")
        t.eq(back["tags"], [value, "after"],
             f"a tag of {value!r} survives being written and read again")

    # and through the real thing, twice, since filing rewrites the header
    (t.box.root / "Notes" / "tagged.md").write_text(
        '---\ntitle: Tag torture\ntype: note\ndomain: engineering\n'
        'tags: ["billing, urgent", "postgres"]\n---\n\n'
        '# Tag torture\n\nNotes on the database schema and index types.\n')
    t.box.run("sort")
    t.box.run("sort")
    item = next(i for i in t.box.items() if i["title"] == "Tag torture")
    # tags are stored as slugs, so the text is normalised — but "billing,
    # urgent" is still *one* tag, and the tag written after it is still there
    t.eq(len(item["tags"]), 2, f"two tags in, two tags out (got {item['tags']!r})")
    t.ok("postgres" in item["tags"], "and nothing after the awkward one was lost")
    t.ok("urgent" not in item["tags"], "the comma inside a tag did not split it")


@test
def test_what_could_not_be_filed_is_said_out_loud(t: Case) -> None:
    """"Nothing waiting" while something is still waiting is a lie.

    One item the sorter cannot move must not stop the other forty-nine — and
    must not be quietly counted as filed either."""
    # Dropped into Work, but they are a file and a note, so the sorter has to
    # move them to Notes — which is exactly the folder it cannot write to.
    work = t.box.root / "Work"
    (work / "blocked.csv").write_text("quarter,total\nQ3,1200\n")
    (work / "ship-it.md").write_text(
        "# Ship the rewrite\n\nDeadline Friday. Migrate the service.\n\n- [ ] cut over\n")

    notes = t.box.root / "Notes"
    notes.chmod(0o555)                       # nothing new may be written there
    try:
        proc = t.box.run("sort", expect=None)
        said = proc.stdout + proc.stderr
        t.ok("Traceback" not in said, f"a destination it cannot write to is not a crash:\n{said[-600:]}")
        t.ok("nothing waiting" not in said,
             "and it never claims everything is filed while something is stuck")
        t.ok("couldn't file" in said, f"it names what it could not move:\n{said[-600:]}")
        t.eq(proc.returncode, 1, "and says so with its exit code")
        report = t.box.json("sort", expect=None)
        t.gte(len(report["skipped"]), 1, "the JSON report carries it too")
    finally:
        notes.chmod(0o755)

    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "once the folder is writable again, everything files")


@test
def test_the_disk_saying_no_is_not_a_crash(t: Case) -> None:
    """A read-only folder is an ordinary thing to run into, and a Python
    traceback is the least useful way to describe one. Only OSError is caught
    here — a real defect still raises, because the trace is what makes it
    reportable."""
    t.box.run("new", "project", "Ship the billing rewrite")
    t.box.run("sort")
    archive = t.box.root / "Archive"
    archive.chmod(0o555)
    try:
        proc = t.box.run("done", "W.01", expect=1)
        said = proc.stdout + proc.stderr
        t.ok("Traceback" not in said, f"no traceback:\n{said[-600:]}")
        t.ok("would not let me finish" in said, f"it says what happened:\n{said[-400:]}")
        t.ok("Archive" in said, "and names the folder it could not write to")
    finally:
        archive.chmod(0o755)

    t.ok(any(p.name.startswith("W.01") for p in (t.box.root / "Work").rglob("W.01*")),
         "the project is still where it was")
    t.box.run("done", "W.01")          # and the same command works once it can


@test
def test_a_failure_before_setup_still_respects_no_color(t: Case) -> None:
    """Finding the root and reading the settings both happen before styling was
    switched on, so the two errors most likely to be read out of a redirected
    log were the two that dumped raw escape codes into it."""
    config = t.box.root / ".os" / "config.json"
    keep = config.read_text()
    config.write_text("{ not json")
    try:
        proc = t.box.run("status", expect=1)
        t.ok("\033[" not in (proc.stdout + proc.stderr),
             f"no escape codes when NO_COLOR is set: {(proc.stdout + proc.stderr)!r}")
        t.ok("config.json" in proc.stderr, "and the message still names the file")
    finally:
        config.write_text(keep)


@test
def test_a_routine_done_by_hand_gets_noticed(t: Case) -> None:
    """Nothing in the terminal ever mentioned skills, so somebody who never
    opens the chat could use this folder for a year without learning they
    exist. `os tidy` now says so — but only when the person has written down,
    in their own words, a job they do the same way every time.

    The bar has to stay high: a nudge that fires on the wrong note is worse
    than one that never fires."""
    inbox = t.box.root / "Notes"
    # a cadence AND a fixed procedure — this is a skill waiting to happen
    (inbox / "invoice.md").write_text(
        "# Draft my weekly invoice\n\nEvery Friday I do the same steps: open the "
        "timesheet, total the hours, apply the rate, draft the email.\n"
        "Ongoing, recurring, no end date.\n")
    # a cadence and three steps counts too
    (inbox / "standup.md").write_text(
        "# Monday standup prep\n\nEvery monday, ongoing recurring responsibility, "
        "no deadline.\n- pull the open PRs\n- check what shipped\n- write three bullets\n")
    # bait: a cadence with no procedure is just an ordinary ongoing thing
    (inbox / "gym.md").write_text(
        "# Fitness\n\nOngoing. Three workouts every week, sleep before midnight. "
        "Recurring, no end date.\n")
    # bait: a procedure with no cadence is a runbook, not a routine
    (inbox / "restore.md").write_text(
        "# Restoring from backup\n\nReference runbook. Checklist for the on-call "
        "engineer.\n- stop the writer\n- restore the snapshot\n- verify checksums\n")
    t.box.run("sort")

    flagged = {r["title"] for r in t.box.json("tidy")["routines"]}
    t.ok("Draft my weekly invoice" in flagged, f"a cadence plus fixed steps is spotted ({flagged})")
    t.ok("Monday standup prep" in flagged, f"a cadence plus a step list too ({flagged})")
    t.ok("Fitness" not in flagged, "a cadence on its own is not a routine")
    t.ok("Restoring from backup" not in flagged, "and neither is a checklist that runs once")

    said = t.box.run("tidy").stdout
    t.ok("by hand" in said.lower(), "the report a person reads says so")
    t.ok("os new skill" in said, "and shows the command that fixes it")

    # once it is automated, it must stop nagging
    t.box.run("new", "skill", "Draft my weekly invoice")
    after = {r["title"] for r in t.box.json("tidy")["routines"]}
    t.ok("Draft my weekly invoice" not in after,
         "something already made into a skill is never mentioned again")

    # and the vocabulary is the user's to change, like the rest of words.json
    words = t.box.root / ".os" / "words.json"
    spec = json.loads(words.read_text())
    t.ok("routine" in spec, "the phrases live in the file people are told to edit")
    spec["routine"]["cadence"] = []
    words.write_text(json.dumps(spec, indent=2))
    t.eq(t.box.json("tidy")["routines"], [], "emptying it turns the nudge off entirely")


@test
def test_the_terminal_says_skills_exist(t: Case) -> None:
    """`os help` listed project, ongoing and note and stopped there, so the
    whole idea of a skill was reachable only from the chat."""
    top = t.box.run("help").stdout
    t.ok("os new skill" in top, "the main help lists it beside project and ongoing")

    detail = t.box.run("help", "new").stdout
    t.ok("skill" in detail and "same way every time" in detail,
         "and `os help new` says what a skill actually is, not just its syntax")
    t.ok("helper" in detail, "and points at helpers for the other kind of job")

    readme = (t.box.root / "README.md").read_text()
    t.ok("./os new skill" in readme, "the README explains it too, not only AGENTS.md")


@test
def test_one_run_at_a_time(t: Case) -> None:
    """Two sessions in one folder must not re-shelve it simultaneously."""
    lock = t.box.root / ".os" / ".lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "at": time.time(), "label": "sort"}))

    t.box.fill_inbox(4)
    blocked = t.box.run("sort", expect=1)
    t.ok("already working here" in (blocked.stderr + blocked.stdout),
         "a live lock blocks a second sort")
    t.eq(t.box.inbox_count(), 4, "and nothing was moved while blocked")

    # a read-only command is never blocked
    t.box.run("status")
    t.box.run("find", "note")

    # a stale lock is stepped over rather than wedging the folder forever
    lock.write_text(json.dumps({"pid": os.getpid(), "at": time.time() - 4000, "label": "sort"}))
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "a stale lock does not block work")
    t.ok(not lock.exists(), "the lock is released afterwards")

    # a lock nobody can read is debris, not a holder
    lock.write_text("{{{ not json")
    t.box.fill_inbox(2)
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "an unreadable lock does not wedge the folder either")

    # And the case the check-then-write version could not cover: runs that
    # start together. Both used to look, both saw no lock, and both went
    # ahead — after which one moved a file the other was midway through
    # moving, and died with a Python traceback instead of the message above.
    # Losing this race needs both runs past the check before either writes,
    # which is a narrow window — so try it several times rather than once.
    env = dict(os.environ, ZENITH_HOME=str(t.box.root), NO_COLOR="1")
    for burst in range(4):
        t.box.fill_inbox(16)
        racers = [subprocess.Popen([str(t.box.root / "os"), "sort"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, cwd=str(t.box.root), env=env)
                  for _ in range(6)]
        said = [proc.communicate(timeout=180)[0] for proc in racers]
        for out in said:
            t.ok("Traceback" not in out,
                 f"no run crashes (burst {burst}); one said:\n{out[-600:]}")
        t.eq(sum("already working here" in out for out in said), len(said) - 1,
             f"exactly one run works, the rest are told who has the folder (burst {burst})")
        t.eq(t.box.inbox_count(), 0, f"and the winner still files everything (burst {burst})")


@test
def test_names_in_any_language_survive(t: Case) -> None:
    """Transliteration must never silently delete somebody's filename."""
    cases = {
        "設計ノート.md": "# 設計ノート\n\nReference notes on design.\n",
        "проект-2026.md": "# Проект 2026\n\nReference material for the year.\n",
        "café-résumé.md": "# Café résumé\n\nReference notes.\n",
        "emoji-🎯-target.md": "# Target notes\n\nReference material.\n",
    }
    for name, body in cases.items():
        (t.box.root / "Notes" / name).write_text(body, encoding="utf-8")
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "every name was filed")

    names = " ".join(p.name for p in t.box.root.rglob("*") if p.is_file())
    t.ok("設計" in names, "Japanese characters were kept in the filename")
    t.ok("проект" in names or "2026" in names, "Cyrillic was kept or sensibly transliterated")
    t.ok("cafe-resume" in names, "accents transliterate to clean ASCII")
    t.eq([p for p in t.box.root.rglob("*untitled*")], [], "nothing collapsed to 'untitled'")
    t.box.run("check", expect=None)


@test
def test_the_template_is_shippable(t: Case) -> None:
    """The things a stranger downloading this folder needs to find."""
    root = t.box.root
    for name in ("README.md", "AGENTS.md", "CLAUDE.md", "LICENSE.md", ".gitignore", "os"):
        t.ok((root / name).exists(), f"{name} ships with the template")
    visible = sorted(p.name for p in root.iterdir() if not p.name.startswith("."))
    t.lte(len([v for v in visible if v.endswith(".md")]), 5,
          f"the top level stays uncluttered (found {visible})")
    t.lte(len((root / "README.md").read_text().split("\n")), 140,
          "the README stays short enough to actually read")
    t.ok(os.access(root / "os", os.X_OK), "./os is executable")

    settings = json.loads((root / ".claude" / "settings.json").read_text())
    for rule in settings["permissions"]["allow"]:
        t.ok("//Users" not in rule and "~" not in rule,
             f"no permission grant reaches outside the folder: {rule}")
        t.ok("${CLAUDE_PROJECT_DIR}" in rule or rule.startswith("Bash(./os"),
             f"grant is scoped to the project: {rule}")

    ignored_paths = (root / ".gitignore").read_text()
    for pattern in (".os/backups/", ".os/cache/", ".DS_Store", "registry.json"):
        t.ok(pattern in ignored_paths, f".gitignore covers {pattern}")

    # Finder recreates .DS_Store constantly, so the guarantee that matters is
    # that the doctor sweeps it, not that it never appears
    (root / ".claude" / ".DS_Store").write_text("")
    (root / "Notes" / ".DS_Store").write_text("")
    codes = {i["code"] for i in t.box.json("check", expect=None)["issues"]}
    t.ok("clutter" in codes, "check notices desktop litter")
    t.box.run("check", "--fix", expect=None)
    t.eq(list(root.rglob(".DS_Store")), [], "--fix sweeps macOS clutter")
    t.eq(list(root.rglob("__pycache__")), [], "--fix sweeps bytecode")
    t.eq(list(root.rglob("*.tmp~")), [], "no temp files survive")

    # the command reference lives in `./os help` and in the manual, not in a
    # separate file that can drift out of sync with the code
    listed = t.box.run("help").stdout
    for command in ("save", "sort", "undo", "check", "demo"):
        t.ok(command in listed, f"`os help` lists {command}")
    for gone in ("CHEATSHEET.md", "GUIDE.pdf", "GUIDE.md"):
        t.ok(not (root / gone).exists(), f"no {gone} to fall out of date")


@test
def test_shell_completion_is_usable(t: Case) -> None:
    """Tab completion is how most people discover the rest of a CLI."""
    zsh = t.box.run("completion", "zsh").stdout
    t.ok(zsh.startswith("#compdef os"), "the zsh script declares itself")
    for command in ("save", "sort", "check", "demo", "undo", "find", "close", "hold"):
        t.ok(f"'{command}:" in zsh, f"zsh completion offers {command}")

    bash = t.box.run("completion", "bash").stdout
    t.ok("complete -F _os_complete os" in bash, "the bash script registers itself")
    script = t.box.tmp / "completion.bash"
    script.write_text(bash)
    check = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    t.eq(check.returncode, 0, f"the bash completion parses: {check.stderr}")


@test
def test_empty_buckets_stay_quiet(t: Case) -> None:
    """An empty folder should look empty, not broken."""
    t.box.run("index")
    for bucket in ("Work", "Notes"):
        visible = [p.name for p in (t.box.root / bucket).iterdir()
                   if not p.name.startswith(".")]
        t.eq(visible, [], f"{bucket} is genuinely empty, not full of scaffolding")

    plain = t.box.run("status").stdout
    for jargon in ("taxonomy", "front matter", "idempotent", "/100"):
        t.ok(jargon not in plain.lower(), f"an empty folder never says '{jargon}'")
    t.ok("save" in plain, "an empty folder tells you the one thing to try")

    brief = t.box.run("brief").stdout
    t.ok("first time" in brief.lower() or "never" in brief.lower(),
         "a brand-new folder tells the AI this person has never seen it")
    t.ok("Do NOT list commands" in brief,
         "and tells the AI not to open with a wall of commands")


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    global VERBOSE
    VERBOSE = "-v" in argv or "--verbose" in argv
    keep = "--keep" in argv
    only = ""
    if "-k" in argv:
        idx = argv.index("-k")
        only = argv[idx + 1] if idx + 1 < len(argv) else ""

    selected = [t for t in TESTS if not only or only in t.__name__]
    print()
    print(f"  {B}Zenith — test suite{X}")
    print(f"  {D}engine {engine.ENGINE_VERSION} · python {sys.version.split()[0]} · "
          f"{len(selected)} tests{X}")
    print(f"  {D}{'─' * 64}{X}")

    passed, failed, checks = 0, [], 0
    started = time.time()

    for fn in selected:
        label = fn.__name__.replace("test_", "").replace("_", " ")
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        print(f"  {D}▸{X} {label:<44}", end="", flush=True)
        box = Sandbox()
        case = Case(box)
        t0 = time.time()
        try:
            fn(case)
            checks += case.checks
            passed += 1
            print(f" {G}pass{X} {D}{case.checks:>4} checks  {time.time()-t0:>5.1f}s{X}")
            if VERBOSE and doc:
                print(f"      {D}{doc}{X}")
        except Exception as exc:
            checks += case.checks
            failed.append((fn.__name__, exc, traceback.format_exc()))
            print(f" {R}FAIL{X} {D}{case.checks:>4} checks  {time.time()-t0:>5.1f}s{X}")
        finally:
            if keep:
                print(f"      {D}sandbox kept: {box.root}{X}")
            else:
                box.destroy()

    elapsed = time.time() - started
    print(f"  {D}{'─' * 64}{X}")
    if failed:
        print()
        for name, exc, tb in failed:
            print(f"  {R}{B}FAILED{X} {B}{name}{X}")
            first = str(exc).split("\n")
            for line in first[:14]:
                print(f"    {R}{line}{X}")
            if VERBOSE:
                print(f"{D}{tb}{X}")
            print()
    verdict = f"{G}{passed}/{len(selected)} passed{X}" if not failed else \
              f"{R}{len(failed)} failed{X}, {passed} passed"
    print(f"  {verdict}  {D}· {checks} assertions · {elapsed:.1f}s{X}")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
