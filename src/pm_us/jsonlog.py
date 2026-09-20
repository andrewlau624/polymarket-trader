"""Bounded append-only JSONL: stream it, tail it, rotate it.

Every log reader in this project slurped the whole file into a list, and the
bot appends on every sweep - with --verticals that is a record per candidate
per game per sweep. past_hits() re-read all of it each time and
snapshot_prices did `recs + new`, copying the entire list. Nothing was capped,
which is the shape of an out-of-memory failure on a small box, and it took one
down.

So: iterate instead of slurp, read only the tail when only the tail matters,
and rotate the file before it gets big enough to matter at all.
"""

import json
import os

MAX_BYTES = 8 * 1024 * 1024        # rotate past this
KEEP_ROTATIONS = 2


def iter_records(path, limit=None):
    """Yield records one at a time. Never builds the whole list."""
    if not os.path.exists(path):
        return
    n = 0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield rec
            n += 1
            if limit and n >= limit:
                return


def tail_records(path, n=2000, block=262144):
    """The last n records, read from the END of the file.

    For anything that only cares about recent history - which is everything
    here - this is O(n) instead of O(file).
    """
    if not os.path.exists(path):
        return []
    size = os.path.getsize(path)
    want = min(size, block * 8)
    with open(path, "rb") as fh:
        fh.seek(max(size - want, 0))
        chunk = fh.read()
    lines = chunk.split(b"\n")
    if size > want and lines:
        lines = lines[1:]            # first line is probably truncated
    out = []
    for raw in lines[-n:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def append(path, rec, max_bytes=MAX_BYTES):
    """Append one record, rotating first if the file has grown too large."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        if os.path.getsize(path) > max_bytes:
            rotate(path)
    except OSError:
        pass
    with open(path, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def rotate(path, keep=KEEP_ROTATIONS):
    for i in range(keep - 1, 0, -1):
        older, newer = f"{path}.{i + 1}", f"{path}.{i}"
        if os.path.exists(newer):
            try:
                os.replace(newer, older)
            except OSError:
                pass
    try:
        os.replace(path, f"{path}.1")
    except OSError:
        pass
