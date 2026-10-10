import shutil
from pathlib import Path

import pyarrow.parquet as pq
from typer.testing import CliRunner

from societysim.analysis import publish
from societysim.cli import app

FIXTURES = Path(__file__).parents[2] / "fixtures"


class FakeHub:
    """Stands in for HfApi: a dict of path -> bytes, like a dataset repo."""

    repo: dict[str, bytes] = {}
    calls: list[tuple] = []

    def __init__(self, token=None):
        self.token = token

    def create_repo(self, repo_id, repo_type, exist_ok):
        FakeHub.calls.append(("create_repo", repo_id, repo_type, exist_ok, self.token))

    def upload_folder(self, folder_path, repo_id, repo_type, commit_message):
        FakeHub.calls.append(("upload_folder", repo_id, repo_type))
        for p in Path(folder_path).rglob("*"):
            if p.is_file():
                FakeHub.repo[p.relative_to(folder_path).as_posix()] = p.read_bytes()


def batch(tmp_path):
    b = tmp_path / "batch"
    b.mkdir()
    shutil.copy(FIXTURES / "sample_game.jsonl", b / "sample_game.jsonl")
    shutil.copy(FIXTURES / "sample_game.labels.jsonl", b / "fixture-0001.labels.jsonl")
    return b


def test_publish_uploads_shards_labels_and_card(tmp_path):
    FakeHub.repo, FakeHub.calls = {}, []
    n = publish.publish_batch(batch(tmp_path), "org/ds", "tok", api_factory=FakeHub)
    assert n["games"] == 1 and n["events"] > 0 and n["labels"] > 0
    assert FakeHub.calls[0] == ("create_repo", "org/ds", "dataset", True, "tok")
    assert set(FakeHub.repo) == {
        "README.md",
        "data/jsonl/fixture-0001.jsonl",
        "data/jsonl/fixture-0001.labels.jsonl",
        "data/events/fixture-0001.parquet",
        "data/labels/fixture-0001.parquet",
    }
    assert (
        FakeHub.repo["data/jsonl/fixture-0001.jsonl"]
        == (FIXTURES / "sample_game.jsonl").read_bytes()
    )
    card = FakeHub.repo["README.md"].decode()
    assert "LOG_SCHEMA.md" in card and "license: cc-by-4.0" in card
    assert "qwen3-235b" in card and "false_role_claim" in card


def test_parquet_row_counts_match(tmp_path):
    out = tmp_path / "out"
    n = publish.build_staging(batch(tmp_path), out)
    assert pq.read_table(out / "data/events/fixture-0001.parquet").num_rows == n["events"]
    assert pq.read_table(out / "data/labels/fixture-0001.parquet").num_rows == n["labels"]


def test_republish_does_not_duplicate(tmp_path):
    FakeHub.repo, FakeHub.calls = {}, []
    b = batch(tmp_path)
    publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    first = dict(FakeHub.repo)
    publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert FakeHub.repo == first  # same paths, same bytes: nothing added


def test_cli_requires_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    r = CliRunner().invoke(app, ["publish", str(batch(tmp_path)), "--repo", "org/ds"])
    assert r.exit_code == 1 and "HF_TOKEN" in r.output


def test_cli_publish(tmp_path, monkeypatch):
    FakeHub.repo, FakeHub.calls = {}, []
    monkeypatch.setenv("HF_TOKEN", "tok")
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.setattr("huggingface_hub.HfApi", FakeHub)
    r = CliRunner().invoke(app, ["publish", str(batch(tmp_path)), "--repo", "org/ds"])
    assert r.exit_code == 0, r.output
    assert "published 1 games" in r.output and "data/events/fixture-0001.parquet" in FakeHub.repo
