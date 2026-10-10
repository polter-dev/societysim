"""Always-on sink: runs/<batch_id>/<game_id>.jsonl, one event per line, flushed per event.

Files are UTF-8 with "\\n" line ends whatever the platform default (cp1252 on Windows), and
events keep non-ASCII text as is (pydantic's model_dump_json, like json.dumps with
ensure_ascii=False); the viewer reads the log as UTF-8.
"""

import json
import logging
from pathlib import Path
from typing import IO, Any

from societysim.logging.events import Event

log = logging.getLogger(__name__)


class JsonlSink:
    def __init__(self, batch_id: str, runs_dir: str | Path = "runs"):
        self.dir = Path(runs_dir) / batch_id
        self._f: IO[str] | None = None
        self._meta: dict[str, Any] = {}
        self.write_errors = 0  # events that could not be written this game

    def _meta_path(self) -> Path:
        return self.dir / f"{self._meta['game_id']}.run_meta.json"

    def _write_meta(self) -> None:
        text = json.dumps(self._meta, indent=2, default=str)
        self._meta_path().write_text(text, encoding="utf-8")

    async def start_game(self, run_meta: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._meta = {**run_meta, "write_errors": 0}
        self.write_errors = 0
        self._write_meta()
        path = self.dir / f"{run_meta['game_id']}.jsonl"
        self._f = path.open("w", encoding="utf-8", newline="\n")

    async def write(self, event: Event) -> None:
        if not self._f:
            return
        try:
            self._f.write(event.model_dump_json() + "\n")
            self._f.flush()
        except (OSError, ValueError):
            self.write_errors += 1
            log.exception(
                "game %s: event seq %s (%s) NOT written to the JSONL log; the log has a hole",
                event.game_id,
                event.seq,
                event.type,
            )

    async def end_game(self, game_id: str, winner: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._f:
            self._f.close()
            self._f = None
            if self.write_errors:
                self._meta["write_errors"] = self.write_errors
                try:
                    self._write_meta()
                except OSError:
                    log.exception("game %s: write_errors not recorded", self._meta["game_id"])
