"""Writing collector output without destroying the run before it.

An incremental workspace is built by hardlinking yesterday's, so on the way
in every file today shares an inode with yesterday's copy. `open(path, "w")`
writes *through* that link: it opens the shared inode and truncates it, so
saving today's data silently rewrites yesterday's snapshot to match. The
symptom is nasty and quiet — an audit file holding a seven-day catch-up was
replaced by "[]" in both days at once, because the second run of the day had
only forty minutes to report and wrote its empty result over the shared file.

Writing to a temporary file and renaming it into place fixes that: the rename
puts a brand-new inode at the path and leaves the old one alone, so yesterday
keeps whatever it had. It is also atomic, which means a crash mid-write can
no longer leave a half-written JSON file where a complete one used to be.
"""

import json
import os
from pathlib import Path


def write_json(path, data, indent=2):
    """Write JSON to path, replacing it rather than writing through it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent)
    os.replace(tmp, path)      # atomic, and breaks any hardlink at `path`


def read_json(path, default=None):
    """Read JSON, returning `default` if it is missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def merge_records(path, new_records, key_fields=("id",)):
    """Combine what is already at `path` with `new_records`, oldest first.

    A time-windowed collector only ever reports what happened inside its own
    window, so writing that result straight out makes each run's archive hold
    less history than the last. Merging keeps the file cumulative: the run
    that caught up on seven days stays in it, and later runs add their few
    new events rather than replacing thirteen thousand old ones.

    Records are matched on `key_fields` so a re-reported event appears once.
    Anything without those fields is kept as-is — dropping a record because
    it lacks an id would lose real data to a technicality.
    """
    existing = read_json(path, default=[])
    if not isinstance(existing, list):
        existing = []

    def identity(record):
        if not isinstance(record, dict):
            return None
        parts = [record.get(field) for field in key_fields]
        return tuple(parts) if any(p is not None for p in parts) else None

    merged = []
    seen = set()
    for record in list(existing) + list(new_records):
        ident = identity(record)
        if ident is None:
            merged.append(record)
            continue
        if ident in seen:
            continue
        seen.add(ident)
        merged.append(record)
    return merged
