import importlib.util
import shutil
from pathlib import Path

import pytest

from societysim.analysis.publish import build_staging

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("fetch_hf_batch", ROOT / "scripts/fetch_hf_batch.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


def _fake_hub(staging: Path, calls: list):
    """Stands in for snapshot_download: copies the matching files from a publish staging dir."""

    def download(repo_id, repo_type, allow_patterns, local_dir, token):
        calls.append((repo_id, repo_type, token))
        for pat in allow_patterns:
            for f in staging.glob(pat):
                dest = Path(local_dir) / f.relative_to(staging)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dest)
        return local_dir

    return download


def test_round_trips_with_publish(tmp_path):
    batch = tmp_path / "src"
    batch.mkdir()
    shutil.copy(ROOT / "fixtures/sample_game.jsonl", batch / "g.jsonl")
    shutil.copy(ROOT / "fixtures/sample_game.labels.jsonl", batch / "fixture-0001.labels.jsonl")
    staging = tmp_path / "staging"
    build_staging(batch, staging)
    calls: list = []
    out = fetch.fetch_batch(
        "o/n", "b1", tmp_path / "runs", token="t", download=_fake_hub(staging, calls)
    )
    assert out == tmp_path / "runs" / "b1"
    pub = staging / "data" / "jsonl"
    assert sorted(p.name for p in out.iterdir()) == sorted(p.name for p in pub.iterdir())
    # raw log and labels come back byte for byte, under the game_id names publish chose
    assert (out / "fixture-0001.jsonl").read_bytes() == (batch / "g.jsonl").read_bytes()
    labels = out / "fixture-0001.labels.jsonl"
    assert labels.read_bytes() == (batch / "fixture-0001.labels.jsonl").read_bytes()
    assert calls == [("o/n", "dataset", "t")]


def test_no_logs_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="no game logs"):
        fetch.fetch_batch("o/n", "b", tmp_path / "runs", download=_fake_hub(tmp_path / "empty", []))


def test_parse_spec():
    assert fetch.parse_spec("hf:polter-dev/societysim-games") == (
        "polter-dev/societysim-games",
        "societysim-games",
    )
    assert fetch.parse_spec("hf:a/b:run1") == ("a/b", "run1")
    for bad in ("hf:nope", "hf:a/b:../x", "hf:a/b/c"):
        with pytest.raises(ValueError):
            fetch.parse_spec(bad)
