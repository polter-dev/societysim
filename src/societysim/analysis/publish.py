"""Publish a batch (game logs + labels) to a Hugging Face Dataset.

Layout in the repo, one file per game so a re-upload overwrites instead of duplicating:
  README.md                          dataset card
  data/jsonl/<game_id>.jsonl         raw log, byte for byte
  data/jsonl/<game_id>.labels.jsonl  labels
  data/events/<game_id>.parquet      events table (suspicion and meta as JSON strings)
  data/labels/<game_id>.parquet      labels table
The game_id comes from the log, so the same batch always maps to the same paths.
"""

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from societysim.analysis.labeler import read_log
from societysim.logging.events import Label

LICENSE = "cc-by-4.0"


def _read_labels(path: Path) -> list[Label]:
    if not path.is_file():
        return []
    return [Label.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _events_table(events) -> pa.Table:
    rows = []
    for e in events:
        d = e.model_dump(mode="json")
        d["suspicion"] = json.dumps(d["suspicion"]) if d["suspicion"] is not None else None
        d["meta"] = json.dumps(d["meta"], sort_keys=True)
        rows.append(d)
    return pa.Table.from_pylist(rows)


def _labels_table(labels: list[Label]) -> pa.Table:
    return pa.Table.from_pylist([lb.model_dump(mode="json") for lb in labels])


def dataset_card(models: list[str], n_games: int, n_events: int, n_labels: int) -> str:
    model_list = "\n".join(f"- {m}" for m in models) or "- (none found)"
    return f"""---
license: {LICENSE}
pretty_name: SocietySim Werewolf games
tags:
  - werewolf
  - multi-agent
  - deception
  - llm-evaluation
configs:
  - config_name: events
    data_files: data/events/*.parquet
  - config_name: labels
    data_files: data/labels/*.parquet
---

# SocietySim Werewolf games

Games of Werewolf played by LLM agents from different providers. A deterministic engine owns
the true state; agents return a private scratchpad (with a suspicion score for every other living
player) and a public action. Every event is logged.

This dataset holds {n_games} games, {n_events} events and {n_labels} labels.

## Files

- `data/jsonl/<game_id>.jsonl`: the raw game log, one event per line, unedited.
- `data/jsonl/<game_id>.labels.jsonl`: lie labels for that game.
- `data/events/*.parquet`, `data/labels/*.parquet`: the same data as tables. In the events table,
  `suspicion` and `meta` are JSON strings.

## Schema

Events and labels are documented in
[docs/LOG_SCHEMA.md](https://github.com/polter-dev/societysim/blob/main/docs/LOG_SCHEMA.md).
`actor` and `target` are seat ids (`p0`...`p8`), never names. Event rows include the private
reasoning of each player, so this is a research log, not what any player saw.

## Models

{model_list}

## Labels

Each label marks a statement or action by `actor` at event `source_seq`.

- `false_role_claim`: a player claims a role they don't have.
- `false_report`: a non-Seer reports a check, or the Seer reports a result that contradicts
  their real one.
- `bussing`: a wolf accuses or votes for a fellow wolf.
- `hidden_knowledge`: the Seer holds a wolf result and speaks that day without reporting it.
- `mis_elimination`: a non-wolf is voted out; one label per voter.

`source` says what detected it: `self_tag` (the agents' own structured tags), `jev` (the Jev
classifier) or `engine` (game facts alone).

## License

{LICENSE.upper()}. Model outputs are subject to each provider's terms.
"""


def build_staging(batch_dir: Path, out: Path) -> dict[str, int]:
    """Write the repo layout for batch_dir into out. Returns counts."""
    logs = sorted(p for p in batch_dir.glob("*.jsonl") if not p.name.endswith(".labels.jsonl"))
    models: set[str] = set()
    n = {"games": 0, "events": 0, "labels": 0}
    for sub in ("jsonl", "events", "labels"):
        (out / "data" / sub).mkdir(parents=True, exist_ok=True)
    for path in logs:
        events = read_log(path)
        if not events:
            continue
        gid = events[0].game_id
        labels_path = path.with_name(f"{gid}.labels.jsonl")
        labels = _read_labels(labels_path)
        for seat in (events[0].meta.seats or {}).values():
            models.add(seat.model)
        (out / "data" / "jsonl" / f"{gid}.jsonl").write_bytes(path.read_bytes())
        if labels_path.is_file():
            (out / "data" / "jsonl" / f"{gid}.labels.jsonl").write_bytes(labels_path.read_bytes())
        pq.write_table(_events_table(events), out / "data" / "events" / f"{gid}.parquet")
        if labels:
            pq.write_table(_labels_table(labels), out / "data" / "labels" / f"{gid}.parquet")
        n["games"] += 1
        n["events"] += len(events)
        n["labels"] += len(labels)
    if not n["games"]:
        raise ValueError(f"no game logs found in {batch_dir}")
    (out / "README.md").write_text(
        dataset_card(sorted(models), n["games"], n["events"], n["labels"]), encoding="utf-8"
    )
    return n


def publish_batch(
    batch_dir: Path,
    repo_id: str,
    token: str,
    private: bool = True,
    api_factory: Callable[..., object] | None = None,
) -> dict[str, int]:
    """Upload batch_dir to the dataset repo repo_id. Re-running overwrites the same paths."""
    if api_factory is None:
        from huggingface_hub import HfApi as api_factory
    api = api_factory(token=token)
    with tempfile.TemporaryDirectory() as tmp:
        n = build_staging(batch_dir, Path(tmp))
        api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)
        # A game with no labels now must not keep an older labels shard on the hub.
        stale = []
        for log in sorted((Path(tmp) / "data" / "jsonl").glob("*.jsonl")):
            if log.name.endswith(".labels.jsonl"):
                continue
            gid = log.stem
            if not (Path(tmp) / "data" / "labels" / f"{gid}.parquet").exists():
                stale += [f"data/labels/{gid}.parquet", f"data/jsonl/{gid}.labels.jsonl"]
        api.upload_folder(
            folder_path=tmp,
            repo_id=repo_id,
            repo_type="dataset",
            delete_patterns=stale or None,
            commit_message=f"Publish {batch_dir.name}: {n['games']} games",
        )
    return n
