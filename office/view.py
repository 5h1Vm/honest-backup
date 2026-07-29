#!/usr/bin/env python3
"""Look inside the backups held on the office drive.

pull.py brings the archives down. This reads them: what backups are here,
what is inside one, and the contents of a single file — without unpacking
a whole archive to disk. A 6 GB backup gives up one file in about a
second, because the tarball is streamed and everything but the wanted
member is discarded.

Like pull.py this is self-contained. Copy it onto the drive with the
tools it needs and it runs on a machine that has never heard of
HonestBackup — a Mac in the office, a laptop at home, a rescue machine
after a fire.

    python3 view.py                  the menu
    python3 view.py --list           what backups are on the drive
    python3 view.py --tree ID        what is inside one
    python3 view.py --cat ID PATH    print one file
    python3 view.py --get ID PATH    write one file out beside this script

Reading anything needs the private key, because the archives are
encrypted. See KEY, below.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ARCHIVE_SUFFIX = ".tar.zst.age"
SCRIPT_DIR = Path(__file__).resolve().parent

BOLD, DIM, RED, GREEN, YELLOW, OFF = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m",
)
if os.name == "nt" or not sys.stdout.isatty():
    BOLD = DIM = RED = GREEN = YELLOW = OFF = ""


def die(message: str, code: int = 1):
    print(f"\n  {RED}Stopped:{OFF} {message}\n")
    sys.exit(code)


# ---------------------------------------------------------------------------
# The tools. Ones carried on the drive win, so the office machine needs
# nothing installed and nobody needs an administrator.
# ---------------------------------------------------------------------------
def platform_tag() -> str:
    """e.g. darwin-arm64 — how get-tools.sh labels the binaries it fetches."""
    system = {"Darwin": "darwin", "Linux": "linux"}.get(
        platform.system(), platform.system().lower())
    machine = {"x86_64": "amd64", "amd64": "amd64",
               "arm64": "arm64", "aarch64": "arm64"}.get(
                   platform.machine().lower(), platform.machine().lower())
    return f"{system}-{machine}"


def tool(name: str) -> str:
    """The binary to use, preferring one carried on the drive.

    A drive prepared with --all holds binaries for several machines, so
    the one tagged for this machine is tried before the untagged copy —
    otherwise a drive filled on Linux hands a Mac a Linux binary.
    """
    suffix = ".exe" if os.name == "nt" else ""
    tagged = f"{name}-{platform_tag()}{suffix}"
    plain = f"{name}{suffix}"
    for folder in (SCRIPT_DIR / "tools", SCRIPT_DIR):
        for filename in (tagged, plain):
            candidate = folder / filename
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    die(f"{name} is missing.\n"
        f"     Run ./get-tools.sh next to this script to put it on the "
        f"drive,\n"
        f"     which installs nothing on this machine.")


def key_file() -> Path:
    """The private key that opens the archives.

    On the drive it lives in keys/archive.key. Keeping it there means a
    lost drive is a readable backup, so get-tools.sh makes you say so
    explicitly before it copies one over. If it is not on the drive, an
    environment variable can point at one held somewhere safer.
    """
    from_env = os.environ.get("HONESTBACKUP_KEY")
    if from_env:
        path = Path(from_env).expanduser()
        if path.is_file():
            return path
        die(f"HONESTBACKUP_KEY points at {path}, which is not there.")
    for candidate in (SCRIPT_DIR / "keys" / "archive.key",
                      SCRIPT_DIR / "archive.key"):
        if candidate.is_file():
            return candidate
    die("The private key is not on this drive, so the archives cannot be\n"
        "     opened. Either put it at keys/archive.key next to this "
        "script,\n"
        "     or point at one with:  export HONESTBACKUP_KEY=/path/to/"
        "archive.key")


# ---------------------------------------------------------------------------
# Where the backups are
# ---------------------------------------------------------------------------
def repository_root() -> Path:
    """The folder holding archives/, read from pull.conf if there is one."""
    conf = SCRIPT_DIR / "pull.conf"
    if conf.is_file():
        for line in conf.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "DESTINATION":
                root = Path(value.strip()).expanduser()
                if (root / "archives").is_dir():
                    return root
    # Failing that, look where the drive layout puts it relative to here.
    for candidate in (SCRIPT_DIR.parent, SCRIPT_DIR, Path.cwd()):
        if (candidate / "archives").is_dir():
            return candidate
    die("No archives folder found.\n"
        "     Either this drive has not been filled yet — run copy-now — "
        "or\n"
        "     DESTINATION in pull.conf does not point at the right place.")


def backups(root: Path) -> list[str]:
    folder = root / "archives"
    if not folder.is_dir():
        return []
    return sorted(
        path.name[: -len(ARCHIVE_SUFFIX)]
        for path in folder.glob(f"*{ARCHIVE_SUFFIX}")
    )


def archive_for(root: Path, backup_id: str) -> Path:
    path = root / "archives" / f"{backup_id}{ARCHIVE_SUFFIX}"
    if not path.is_file():
        die(f"No backup called {backup_id} on this drive.\n"
            f"     Run with --list to see what is here.")
    return path


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


# ---------------------------------------------------------------------------
# Reading an archive without unpacking it
# ---------------------------------------------------------------------------
def stream(archive: Path, tar_args: list[str], capture=True):
    """age -d | zstd -dc | tar …, as one pipeline.

    Piping rather than unpacking is the whole point: tar walks the stream
    and stops caring about everything that is not asked for, so pulling
    one file out of a large archive costs about what reading it costs.
    """
    age = subprocess.Popen(
        [tool("age"), "-d", "-i", str(key_file()), str(archive)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    zstd = subprocess.Popen(
        [tool("zstd"), "-dc"],
        stdin=age.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    age.stdout.close()
    tar = subprocess.Popen(
        [tool("tar")] + tar_args,
        stdin=zstd.stdout,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.DEVNULL,
    )
    zstd.stdout.close()
    out = tar.communicate()[0] if capture else (tar.wait(), b"")[1]

    age.wait()
    if age.returncode != 0:
        detail = age.stderr.read().decode(errors="replace").strip()
        if "no identity matched" in detail or "incorrect" in detail.lower():
            die("That key does not open this archive — it is the wrong "
                "key.")
        die(f"Could not decrypt the archive: {detail or 'age failed'}")
    return out


def tree(root: Path, backup_id: str) -> list[str]:
    listing = stream(archive_for(root, backup_id), ["-t"])
    return [line for line in listing.decode(errors="replace").splitlines()
            if line.strip()]


def read_member(root: Path, backup_id: str, member: str) -> bytes:
    """One file out of the archive, however the person spelled the path.

    The tree is printed without tar's leading "./" because it is noise,
    so what gets typed back rarely matches what tar has stored. Both
    spellings are tried rather than making that the reader's problem.
    """
    archive = archive_for(root, backup_id)
    wanted = member.lstrip("/")
    for spelling in (wanted, f"./{wanted}", wanted.removeprefix("./")):
        data = stream(archive, ["-xO", spelling])
        if data:
            return data
    return b""


# ---------------------------------------------------------------------------
# Showing it
# ---------------------------------------------------------------------------
def show_list(root: Path) -> int:
    found = backups(root)
    print()
    print(f"  {BOLD}Backups on this drive{OFF}")
    print(f"  {DIM}{root}{OFF}")
    print()
    if not found:
        print(f"  {YELLOW}None yet.{OFF} Run copy-now to bring them down.")
        print()
        return 1
    for backup_id in found:
        size = archive_for(root, backup_id).stat().st_size
        print(f"    {backup_id}   {DIM}{human(size)}{OFF}")
    print()
    print(f"  {DIM}{len(found)} backup{'s' if len(found) != 1 else ''}{OFF}")
    print()
    return 0


def print_tree(entries: list[str]) -> None:
    """Group by folder so the shape of the backup is visible.

    A backup holds thousands of paths. A flat list of them tells you
    nothing; the folders tell you which collector produced what.
    """
    folders: dict[str, list[str]] = {}
    for entry in entries:
        # tar writes members as "./m365/mail/x.json". Left alone, that
        # leading "./" is the only top-level folder there is and every
        # backup looks identical.
        path = entry.rstrip("/")
        while path.startswith("./"):
            path = path[2:]
        if not path or path == ".":
            continue
        head, _, tail = path.partition("/")
        folders.setdefault(head, [])
        if tail:
            folders[head].append(tail)
    for name in sorted(folders):
        children = folders[name]
        print(f"    {BOLD}{name}/{OFF}  {DIM}{len(children)} items{OFF}")
        for child in sorted(children)[:8]:
            print(f"        {DIM}{child}{OFF}")
        if len(children) > 8:
            print(f"        {DIM}… {len(children) - 8} more{OFF}")


def show_tree(root: Path, backup_id: str) -> int:
    print()
    print(f"  {BOLD}Inside {backup_id}{OFF}")
    print(f"  {DIM}reading the archive, this takes a moment…{OFF}")
    entries = tree(root, backup_id)
    print()
    print_tree(entries)
    print()
    print(f"  {DIM}{len(entries)} entries. To read one:{OFF}")
    print(f"  {DIM}  python3 view.py --cat {backup_id} <path>{OFF}")
    print()
    return 0


def show_file(root: Path, backup_id: str, member: str) -> int:
    data = read_member(root, backup_id, member)
    if not data:
        die(f"{member} is not in {backup_id}, or it is empty.\n"
            f"     Check the exact path with --tree {backup_id}.")
    # A JSON file is what this mostly holds, and reading it raw is
    # miserable, so it gets indented on the way out.
    if member.endswith(".json"):
        import json
        try:
            data = json.dumps(json.loads(data), indent=2).encode()
        except ValueError:
            pass
    sys.stdout.buffer.write(data)
    if data and not data.endswith(b"\n"):
        sys.stdout.write("\n")
    return 0


def get_file(root: Path, backup_id: str, member: str) -> int:
    data = read_member(root, backup_id, member)
    if not data:
        die(f"{member} is not in {backup_id}, or it is empty.")
    tidy = member.lstrip("/")
    while tidy.startswith("./"):
        tidy = tidy[2:]
    out = SCRIPT_DIR / "restored" / backup_id / tidy
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print()
    print(f"  {GREEN}Written{OFF} to {out}")
    print(f"  {DIM}{human(len(data))}{OFF}")
    print()
    return 0


# ---------------------------------------------------------------------------
# The menu, for the double-click case
# ---------------------------------------------------------------------------
def ask(prompt: str) -> str:
    try:
        return input(f"  {BOLD}{prompt}{OFF} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


def menu(root: Path) -> int:
    while True:
        found = backups(root)
        print()
        print(f"  {BOLD}Backups on this drive{OFF}   {DIM}{root}{OFF}")
        print()
        if not found:
            print(f"  {YELLOW}Nothing here yet.{OFF} Run copy-now first.")
            print()
            return 1
        for number, backup_id in enumerate(found, 1):
            size = archive_for(root, backup_id).stat().st_size
            print(f"    {number:>2}.  {backup_id}   {DIM}{human(size)}{OFF}")
        print()
        choice = ask(f"Which one? 1-{len(found)}, or q to quit:")
        if choice.lower() in ("q", "quit", "exit", ""):
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(found):
            print(f"  {RED}Pick a number from the list.{OFF}")
            continue
        backup_id = found[int(choice) - 1]

        print(f"  {DIM}Reading it, this takes a moment…{OFF}")
        entries = tree(root, backup_id)
        print()
        print_tree(entries)
        print()
        while True:
            wanted = ask("Path to read, or Enter to go back:")
            if not wanted:
                break
            def tidy(text: str) -> str:
                text = text.rstrip("/")
                while text.startswith("./"):
                    text = text[2:]
                return text

            if not any(tidy(e) == tidy(wanted) for e in entries):
                near = [tidy(e) for e in entries if wanted in e][:5]
                print(f"  {RED}Not in this backup.{OFF}")
                for candidate in near:
                    print(f"    {DIM}did you mean {candidate}{OFF}")
                continue
            data = read_member(root, backup_id, wanted)
            print()
            print(f"  {DIM}── {wanted}  ({human(len(data))}) "
                  f"{'─' * 20}{OFF}")
            text = data[:4000].decode(errors="replace")
            print(text)
            if len(data) > 4000:
                print(f"  {DIM}… truncated. For all of it:{OFF}")
                print(f"  {DIM}  python3 view.py --get {backup_id} "
                      f"{wanted}{OFF}")
            print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Look inside the backups on the office drive.")
    parser.add_argument("--list", action="store_true",
                        help="what backups are on the drive")
    parser.add_argument("--tree", metavar="ID",
                        help="what is inside one backup")
    parser.add_argument("--cat", nargs=2, metavar=("ID", "PATH"),
                        help="print one file from a backup")
    parser.add_argument("--get", nargs=2, metavar=("ID", "PATH"),
                        help="write one file out beside this script")
    options = parser.parse_args()

    root = repository_root()
    if options.list:
        return show_list(root)
    if options.tree:
        return show_tree(root, options.tree)
    if options.cat:
        return show_file(root, options.cat[0], options.cat[1])
    if options.get:
        return get_file(root, options.get[0], options.get[1])
    return menu(root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(0)
