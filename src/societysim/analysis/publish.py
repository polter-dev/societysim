"""Publish a batch (game logs + labels) to a Hugging Face Dataset.

Layout in the repo, one file per game so a re-upload overwrites instead of duplicating:
  README.md                          dataset card
  manifest.json                      per batch_id, per game: models and event/label counts
  data/jsonl/<game_id>.jsonl         raw log, byte for byte
  data/jsonl/<game_id>.labels.jsonl  labels
  data/events/<game_id>.parquet      events table (suspicion and meta as JSON strings)
  data/labels/<game_id>.parquet      labels table
The game_id comes from the log, so the same batch always maps to the same paths.

The card is rendered from the manifest. Each publish downloads the repo's manifest, merges the
new batch into it and uploads both again, so the card always describes the whole repo. A repo
with no manifest (published before it existed) is rebuilt from its data/jsonl files.
"""

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from societysim.analysis.labeler import read_log
from societysim.logging.events import Label
from societysim.logging.textio import read_text_lenient

LICENSE = "cc-by-4.0"
MANIFEST = "manifest.json"
LEGACY_BATCH = "(earlier batches)"


def _read_labels(path: Path) -> list[Label]:
    if not path.is_file():
        return []
    return [
        Label.model_validate_json(line) for line in read_text_lenient(path).splitlines() if line
    ]


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


def _game_record(events, labels: list[Label]) -> dict:
    seats = events[0].meta.seats or {}
    return {
        "models": sorted({seat.model for seat in seats.values()}),
        "events": len(events),
        "labels": len(labels),
    }


def _merge_manifest(manifest: dict, batch_id: str, games: dict[str, dict]) -> dict:
    """Return manifest with batch_id's games replaced by `games`. A game id lives in one batch."""
    batches = {}
    for b, v in manifest.get("batches", {}).items():
        kept = {g: r for g, r in v["games"].items() if g not in games}
        if b != batch_id and kept:
            batches[b] = {"games": kept}
    batches[batch_id] = {"games": dict(sorted(games.items()))}
    return {"version": 1, "batches": dict(sorted(batches.items()))}


def _models_of(games) -> list[str]:
    return sorted({m for r in games for m in r["models"]})


def dataset_card(manifest: dict) -> str:
    games = [r for v in manifest["batches"].values() for r in v["games"].values()]
    n_events = sum(r["events"] for r in games)
    n_labels = sum(r["labels"] for r in games)
    model_list = "\n".join(f"- {m}" for m in _models_of(games)) or "- (none found)"
    batch_list = (
        "\n".join(
            f"- `{b}`: {len(v['games'])} games; {', '.join(_models_of(v['games'].values()))}"
            for b, v in manifest["batches"].items()
        )
        or "- (none found)"
    )
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

This dataset holds {len(games)} games, {n_events} events and {n_labels} labels, from
{len(manifest["batches"])} batches.

## Files

- `data/jsonl/<game_id>.jsonl`: the raw game log, one event per line, unedited.
- `data/jsonl/<game_id>.labels.jsonl`: lie labels for that game.
- `data/events/*.parquet`, `data/labels/*.parquet`: the same data as tables. In the events table,
  `suspicion` and `meta` are JSON strings.
- `manifest.json`: games, models and counts per batch. The totals above come from it.

## Schema

Events and labels are documented in
[docs/LOG_SCHEMA.md](https://github.com/polter-dev/societysim/blob/main/docs/LOG_SCHEMA.md).
`actor` and `target` are seat ids (`p0`...`p9`), never names. Event rows include the private
reasoning of each player, so this is a research log, not what any player saw.

## Models

{model_list}

## Batches

{batch_list}

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


def build_staging(batch_dir: Path, out: Path, existing: dict | None = None) -> dict[str, int]:
    """Write the repo layout for batch_dir into out. Returns this batch's counts.

    `existing` is the repo's manifest so far; README.md and manifest.json cover it plus this batch.
    """
    logs = sorted(p for p in batch_dir.glob("*.jsonl") if not p.name.endswith(".labels.jsonl"))
    games: dict[str, dict] = {}
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
        games[gid] = _game_record(events, labels)
        # the published copies are always UTF-8, even of a pre-T230 cp1252 log
        for src, name in ((path, f"{gid}.jsonl"), (labels_path, f"{gid}.labels.jsonl")):
            if src.is_file():
                (out / "data" / "jsonl" / name).write_text(
                    read_text_lenient(src), encoding="utf-8", newline=""
                )
        pq.write_table(_events_table(events), out / "data" / "events" / f"{gid}.parquet")
        if labels:
            pq.write_table(_labels_table(labels), out / "data" / "labels" / f"{gid}.parquet")
        n["games"] += 1
        n["events"] += len(events)
        n["labels"] += len(labels)
    if not n["games"]:
        raise ValueError(f"no game logs found in {batch_dir}")
    manifest = _merge_manifest(existing or {}, batch_dir.name, games)
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / "README.md").write_text(dataset_card(manifest), encoding="utf-8")
    return n


def _existing_manifest(api, repo_id: str) -> dict:
    """The repo's manifest, or one rebuilt from its game files if it has none (or is empty)."""
    files = set(api.list_repo_files(repo_id, repo_type="dataset"))

    def fetch(name: str) -> Path:
        return Path(api.hf_hub_download(repo_id=repo_id, filename=name, repo_type="dataset"))

    if MANIFEST in files:
        return json.loads(fetch(MANIFEST).read_text(encoding="utf-8"))
    games = {}
    for name in sorted(files):
        if not name.startswith("data/jsonl/") or name.endswith(".labels.jsonl"):
            continue
        if not name.endswith(".jsonl"):
            continue
        events = read_log(fetch(name))
        if not events:
            continue
        gid = events[0].game_id
        labels_name = f"data/jsonl/{gid}.labels.jsonl"
        labels = _read_labels(fetch(labels_name)) if labels_name in files else []
        games[gid] = _game_record(events, labels)
    return _merge_manifest({}, LEGACY_BATCH, games) if games else {}


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
    api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)
    existing = _existing_manifest(api, repo_id)
    with tempfile.TemporaryDirectory() as tmp:
        n = build_staging(batch_dir, Path(tmp), existing)
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
