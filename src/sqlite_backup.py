"""Create a verified SQLite snapshot without overwriting an existing backup."""
import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def snapshot(source, destination, *, connect=sqlite3.connect):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if source == destination or destination.exists():
        raise FileExistsError("Backup destination must be new")
    descriptor, temporary = tempfile.mkstemp(prefix=".sqlite-backup-", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        with closing(connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(connect(temporary)) as backup:
                original.backup(backup, pages=256)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("Backup integrity check failed")
        # Publish atomically, fail if another backup already claimed this name.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    snapshot(args.source, args.destination)


if __name__ == "__main__":
    main()
