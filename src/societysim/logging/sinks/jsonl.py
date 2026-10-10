"""Always-on sink: runs/<batch_id>/<game_id>.jsonl, one event per line, flushed per event."""

import json
from pathlib import Path
from typing import IO, Any

from societysim.logging.events import Event


class JsonlSink:
    def __init__(self, batch_id: str, runs_dir: str | Path = "runs"):
        self.dir = Path(runs_dir) / batch_id
        self._f: IO[str] | None = None

    async def start_game(self, run_meta: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        gid = run_meta["game_id"]
        (self.dir / f"{gid}.run_meta.json").write_text(json.dumps(run_meta, indent=2, default=str))
        self._f = (self.dir / f"{run_meta['game_id']}.jsonl").open("w")

    async def write(self, event: Event) -> None:
        if self._f:
            self._f.write(event.model_dump_json() + "\n")
            self._f.flush()

    async def end_game(self, game_id: str, winner: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._f:
            self._f.close()
            self._f = None
