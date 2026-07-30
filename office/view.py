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
import atexit
import getpass
import os
import platform
import shutil
import subprocess
import sys
import tempfile
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


_key_path_cache: Path | None = None
# Whether that path is a temp file this process made, and may therefore
# delete. A key the operator pointed us at belongs to them, not to us.
_key_is_ours: bool = False


def _write_temp_key(content: str) -> Path:
    """Key text, held on disk only as long as one decrypt needs it.

    age has no way to take key material except as a file path, so this is
    unavoidable — but the file lives in the system temp directory, never
    on the drive, is created readable only by this user, and is deleted
    the moment the process exits.
    """
    fd, raw_path = tempfile.mkstemp(prefix="honestbackup-key-")
    os.chmod(raw_path, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content.strip() + "\n")
    return Path(raw_path)


def _forget_key() -> None:
    """Delete the temporary key file — only if this process created it.

    This used to delete whatever path it had cached. When the key arrived by
    HONESTBACKUP_KEY, that path was the operator's own key file, and exiting
    destroyed the one thing that can open every archive ever made. Cleanup
    must never be able to remove something it did not create, so ownership
    is now tracked rather than assumed.
    """
    global _key_path_cache, _key_is_ours
    if _key_path_cache is not None and _key_is_ours:
        _key_path_cache.unlink(missing_ok=True)
    _key_path_cache = None
    _key_is_ours = False


atexit.register(_forget_key)


def key_file() -> Path:
    """The private key that opens the archives — never read from the drive.

    A drive is easy to lose and easy to steal; a key that travelled with
    it would make that the same thing as losing the backups themselves.
    So this asks for the key instead of looking for one, in order:

      1. HONESTBACKUP_KEY        a path to a key file kept somewhere else
      2. HONESTBACKUP_KEY_TEXT   the key itself, e.g. set once by the menu
                                  so a whole session only asks once
      3. typed in now, hidden, if this is a real terminal

    Whichever way it arrives, resolving it happens once per run — every
    later call in the same session reuses the same answer rather than
    asking again for every file opened.
    """
    global _key_path_cache, _key_is_ours
    if _key_path_cache is not None and _key_path_cache.is_file():
        return _key_path_cache

    from_env = os.environ.get("HONESTBACKUP_KEY")
    if from_env:
        path = Path(from_env).expanduser()
        if path.is_file():
            # Theirs. Read it where it lies and never touch it again.
            _key_path_cache, _key_is_ours = path, False
            return path
        die(f"HONESTBACKUP_KEY points at {path}, which is not there.")

    from_text = os.environ.get("HONESTBACKUP_KEY_TEXT")
    if from_text:
        _key_path_cache, _key_is_ours = _write_temp_key(from_text), True
        return _key_path_cache

    if sys.stdin.isatty():
        print()
        print(f"  {BOLD}This backup is encrypted.{OFF}")
        print(f"  {DIM}Paste the private key — it starts with "
              f"AGE-SECRET-KEY-1. Nothing is saved.{OFF}")
        try:
            entered = getpass.getpass("  Key: ").strip()
        except (EOFError, KeyboardInterrupt):
            entered = ""
        if entered:
            _key_path_cache, _key_is_ours = _write_temp_key(entered), True
            return _key_path_cache

    die("No private key available.\n"
        "     Either point at one:  export HONESTBACKUP_KEY=/path/to/key\n"
        "     or set its text:      export HONESTBACKUP_KEY_TEXT='AGE-"
        "SECRET-KEY-1...'\n"
        "     or run this from a real terminal, which will ask for it.")


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
    # This script normally lives in HonestBackup/Scripts, with the data in
    # the sibling folder HonestBackup/Backups — SCRIPT_DIR.parent / "Backups".
    # The rest are fallbacks for an older or hand-built drive.
    for candidate in (SCRIPT_DIR.parent / "Backups", SCRIPT_DIR / "Backups",
                      SCRIPT_DIR, SCRIPT_DIR.parent, Path.cwd()):
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


def reports(root: Path) -> list[str]:
    """Backup ids that have a report on this drive, newest first.

    Reports live beside archives/, hashes/ and manifests/ — the same
    repository tree that already syncs here — but unlike the archives
    they are not encrypted. A report is a summary of what ran, not the
    tenant's actual data, so reading one needs no key at all: the point
    is to make the run's outcome checkable at a glance, including by
    someone who is not the one holding the key.
    """
    folder = root / "reports"
    if not folder.is_dir():
        return []
    return sorted(
        (path.stem for path in folder.glob("*.md")), reverse=True,
    )


def read_report(root: Path, backup_id: str) -> str:
    path = root / "reports" / f"{backup_id}.md"
    if not path.is_file():
        die(f"No report for {backup_id} on this drive.\n"
            f"     Run with --reports to see what is here.")
    return path.read_text(encoding="utf-8", errors="replace")


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


def show_reports(root: Path) -> int:
    found = reports(root)
    print()
    print(f"  {BOLD}Reports on this drive{OFF}")
    print(f"  {DIM}no key needed — these are not encrypted{OFF}")
    print()
    if not found:
        print(f"  {YELLOW}None yet.{OFF} A report is saved here after each "
              f"backup that reaches this drive.")
        print()
        return 1
    for backup_id in found:
        print(f"    {backup_id}")
    print()
    print(f"  {DIM}{len(found)} report{'s' if len(found) != 1 else ''}. "
          f"Read one with --report <id>.{OFF}")
    print()
    return 0


def show_one_report(root: Path, backup_id: str) -> int:
    print()
    print(read_report(root, backup_id))
    return 0


def do_restore(root: Path, backup_id: str, dest: Path,
               files: list[str] | None = None) -> int:
    restore(root, backup_id, dest, files)
    print()
    print(f"  {GREEN}Restored{OFF} to {dest}")
    print()
    return 0


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


def restore(root: Path, backup_id: str, dest: Path,
           files: list[str] | None = None) -> None:
    """Decrypt a backup into ordinary files and folders on disk.

    Reading with --cat is for looking; this is for having a real, usable
    copy afterward that needs nothing further from this script. Everything
    by default, or just the files named — either way the pipeline is the
    same one --cat already uses, just handed to tar's own -C instead of
    captured, so a whole backup extracts as fast as tar can write it.
    """
    archive = archive_for(root, backup_id)
    key_file()  # resolved (and possibly asked for) before dest exists at all
    dest.mkdir(parents=True, exist_ok=True)

    if not files:
        stream(archive, ["-x", "-C", str(dest)], capture=False)
        return

    # Selective: resolve each request against the archive's own recorded
    # spelling — tar keeps tar's leading "./", the tree printed here does
    # not — so a path typed exactly as Browse showed it still matches.
    entries = tree(root, backup_id)

    def tidy(text: str) -> str:
        text = text.rstrip("/")
        while text.startswith("./"):
            text = text[2:]
        return text

    resolved, missing = [], []
    for wanted in files:
        match = next((e for e in entries if tidy(e) == tidy(wanted)), None)
        (resolved if match else missing).append(match or wanted)
    for name in missing:
        print(f"  {YELLOW}not in this backup, skipped:{OFF} {name}")
    if not resolved:
        die("None of the requested files are in this backup.")
    stream(archive, ["-x", "-C", str(dest)] + [r.rstrip("/") for r in resolved],
           capture=False)


def default_restore_dir(root: Path, backup_id: str) -> Path:
    """Where a restore lands when nobody names a folder.

    A sibling of wherever Backups actually turned out to be — same
    reasoning repository_root() already applies for finding it in the
    first place, so this stays correct on whichever machine and mount
    point the drive is plugged into today.
    """
    return root.parent / "Restored" / backup_id


def _report_menu(root: Path) -> None:
    found = reports(root)
    print()
    if not found:
        print(f"  {YELLOW}No reports on this drive yet.{OFF}")
        print()
        return
    print(f"  {BOLD}Reports{OFF}   {DIM}no key needed{OFF}")
    print()
    for number, backup_id in enumerate(found, 1):
        print(f"    {number:>2}.  {backup_id}")
    print()
    choice = ask(f"Which one? 1-{len(found)}, or Enter to go back:")
    if not choice:
        return
    if not choice.isdigit() or not 1 <= int(choice) <= len(found):
        print(f"  {RED}Pick a number from the list.{OFF}")
        return
    print()
    print(read_report(root, found[int(choice) - 1]))


def _restore_prompt(root: Path, backup_id: str) -> None:
    print()
    default = default_restore_dir(root, backup_id)
    typed = ask(f"Restore to which folder? Enter for {default}:")
    dest = Path(typed).expanduser() if typed else default
    print(f"  {DIM}Restoring everything to {dest} …{OFF}")
    try:
        restore(root, backup_id, dest)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"  {RED}Restore failed:{OFF} {exc}")
        return
    print(f"  {GREEN}Restored{OFF} to {dest}")


def reports_menu(root: Path) -> int:
    """Reports only — the compliance path. No key, no archive browsing."""
    found = reports(root)
    if not found:
        print()
        print(f"  {YELLOW}No reports on this drive yet.{OFF} A report is "
              f"saved here after each backup that reaches this drive.")
        print()
        return 1
    while True:
        _report_menu(root)
        again = ask("Read another? Enter for yes, q to go back:")
        if again.lower() in ("q", "quit", "exit"):
            return 0


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
        choice = ask(f"Which one? 1-{len(found)}, r for reports, or q to quit:")
        if choice.lower() in ("q", "quit", "exit", ""):
            return 0
        if choice.lower() == "r":
            _report_menu(root)
            continue
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
            wanted = ask("Path to read, 'r' to restore this backup, "
                        "or Enter to go back:")
            if not wanted:
                break
            if wanted.lower() == "r":
                _restore_prompt(root, backup_id)
                continue

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
    parser.add_argument("--reports", action="store_true",
                        help="which backups have a report — no key needed")
    parser.add_argument("--reports-menu", action="store_true",
                        help="pick and read a report interactively — no "
                             "key needed")
    parser.add_argument("--report", metavar="ID",
                        help="read one backup's report — no key needed")
    parser.add_argument("--restore", metavar="ID",
                        help="decrypt a whole backup to real files on disk")
    parser.add_argument("--to", metavar="DIR",
                        help="where --restore writes to (default: a "
                             "Restored/ folder on this drive)")
    parser.add_argument("--files", nargs="+", metavar="PATH",
                        help="with --restore, only these paths rather "
                             "than the whole backup")
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
    if options.reports:
        return show_reports(root)
    if options.reports_menu:
        return reports_menu(root)
    if options.report:
        return show_one_report(root, options.report)
    if options.restore:
        dest = Path(options.to).expanduser() if options.to else \
            default_restore_dir(root, options.restore)
        return do_restore(root, options.restore, dest, options.files)
    return menu(root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(0)
