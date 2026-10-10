"""Reading run files (game logs, labels, run_meta) written by older code.

Before T230 these were written in the platform default encoding, which is cp1252 on Windows,
so some logs on disk hold cp1252 bytes (curly quotes, em dashes). New files are always UTF-8.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)
_warned: set[str] = set()


def read_text_lenient(path: Path) -> str:
    """The file decoded as UTF-8, or as cp1252 if it is not valid UTF-8 (warned once per file).

    Writes must stay strict UTF-8; this is only for reading files that may predate that.
    """
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        key = str(Path(path).resolve())
        if key not in _warned:
            _warned.add(key)
            log.warning("%s is not UTF-8; reading it as cp1252 (written before T230)", path)
        # errors="replace": cp1252 leaves 5 bytes undefined; a stray one must not lose the file
        return data.decode("cp1252", errors="replace")
