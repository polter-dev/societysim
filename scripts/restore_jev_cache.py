"""Restore a batch's Jev cache from a jev_cache.zip release asset (deploy, #645).

  python scripts/restore_jev_cache.py JEV_CACHE_ZIP BATCH_DIR

Only regular files named .cache/jev/<game_id>/<seq>.json are taken; anything else (a file at
the root, "..", an absolute path, a symlink, another dir) is skipped and counted, so the zip
can't put files into the published batch. Files go to a temp dir first, then into BATCH_DIR.
"""

import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

ENTRY = re.compile(r"\.cache/jev/[A-Za-z0-9][A-Za-z0-9_-]*/[0-9]+\.json")


def restore(zip_path: Path, batch_dir: Path) -> tuple[int, int]:
    """Copy the zip's Jev cache entries into batch_dir. Returns (restored, rejected)."""
    ok = rejected = 0
    with zipfile.ZipFile(zip_path) as z, tempfile.TemporaryDirectory() as tmp:
        for info in z.infolist():
            if info.is_dir():
                continue
            # no file type bits (zip tools that don't record them) counts as a regular file
            kind = stat.S_IFMT(info.external_attr >> 16)
            if not ENTRY.fullmatch(info.filename) or kind not in (0, stat.S_IFREG):
                rejected += 1
                continue
            out = Path(tmp, *info.filename.split("/"))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(info))
            ok += 1
        if ok:
            shutil.copytree(Path(tmp) / ".cache", batch_dir / ".cache", dirs_exist_ok=True)
    return ok, rejected


def main(argv: list[str]) -> int:
    ok, rejected = restore(Path(argv[0]), Path(argv[1]))
    print(f"Jev cache: restored {ok} files, rejected {rejected}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
