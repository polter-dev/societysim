"""Re-encode staged .jsonl files to UTF-8.

    python scripts/reencode_logs.py <staged_site_dir>

Game logs written on Windows before T230 are cp1252. The viewer decodes UTF-8, so those would
show garbled quotes and dashes. This reads each staged .jsonl with the same lenient rule as the
Python readers (UTF-8, else cp1252) and rewrites it as UTF-8. Valid UTF-8 files are left
untouched. It only ever sees the staged copy that scripts/deploy-space.sh made, never runs/.
"""

import sys
from pathlib import Path

from societysim.logging.textio import read_text_lenient


def reencode(site: Path) -> int:
    """Rewrite non-UTF-8 .jsonl files under `site` as UTF-8; return how many changed."""
    changed = 0
    for path in sorted(site.rglob("*.jsonl")):
        try:
            path.read_bytes().decode("utf-8")
            continue
        except UnicodeDecodeError:
            pass
        # bytes, not write_text: no newline translation on Windows
        path.write_bytes(read_text_lenient(path).encode("utf-8"))
        changed += 1
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <staged_site_dir>")
    n = reencode(Path(sys.argv[1]))
    if n:
        print(f"re-encoded {n} staged log file(s) from cp1252 to UTF-8")
