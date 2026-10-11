import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from societysim.analysis import publish
from societysim.cli import app

FIXTURES = Path(__file__).parents[2] / "fixtures"


class FakeHub:
    """Stands in for HfApi: a dict of path -> bytes, like a dataset repo."""

    repo: dict[str, bytes] = {}
    calls: list[tuple] = []
    downloads: list[tuple] = []
    cache: Path = Path()

    def __init__(self, token=None):
        self.token = token

    def create_repo(self, repo_id, repo_type, private, exist_ok):
        FakeHub.calls.append(("create_repo", repo_id, repo_type, exist_ok, self.token, private))

    def list_repo_files(self, repo_id, repo_type):
        FakeHub.downloads.append(("list", repo_id))
        return list(FakeHub.repo)

    def hf_hub_download(self, repo_id, filename, repo_type):
        FakeHub.downloads.append(("download", filename))
        path = FakeHub.cache / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(FakeHub.repo[filename])
        return str(path)

    def upload_folder(self, folder_path, repo_id, repo_type, commit_message, delete_patterns=None):
        FakeHub.calls.append(("upload_folder", repo_id, repo_type, delete_patterns))
        for p in Path(folder_path).rglob("*"):
            if p.is_file():
                FakeHub.repo[p.relative_to(folder_path).as_posix()] = p.read_bytes()


@pytest.fixture(autouse=True)
def _hub_state(tmp_path):
    FakeHub.repo, FakeHub.calls, FakeHub.downloads = {}, [], []
    FakeHub.cache = tmp_path / "hub-cache"


def batch(tmp_path, name="batch", game_id="fixture-0001", model_swap=None):
    """A one-game batch from the fixture, with game_id (and optionally models) renamed."""
    b = tmp_path / name
    b.mkdir()
    for src, dst in (
        ("sample_game.jsonl", f"{game_id}.jsonl"),
        ("sample_game.labels.jsonl", f"{game_id}.labels.jsonl"),
    ):
        text = (FIXTURES / src).read_bytes().decode("utf-8").replace("fixture-0001", game_id)
        for old, new in (model_swap or {}).items():
            text = text.replace(old, new)
        (b / dst).write_bytes(text.encode("utf-8"))
    return b


def manifest():
    return json.loads(FakeHub.repo["manifest.json"])


def test_publish_uploads_shards_labels_and_card(tmp_path):
    FakeHub.repo, FakeHub.calls = {}, []
    n = publish.publish_batch(batch(tmp_path), "org/ds", "tok", api_factory=FakeHub)
    assert n["games"] == 1 and n["events"] > 0 and n["labels"] > 0
    assert FakeHub.calls[0] == ("create_repo", "org/ds", "dataset", True, "tok", True)
    assert set(FakeHub.repo) == {
        "README.md",
        "manifest.json",
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


def test_second_batch_card_covers_both(tmp_path):
    a = batch(tmp_path, "batch-a")
    b = batch(tmp_path, "batch-b", "fixture-0002", {"qwen3-235b": "other-model"})
    na = publish.publish_batch(a, "org/ds", "tok", api_factory=FakeHub)
    nb = publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert FakeHub.downloads[-1] == ("download", "manifest.json")
    assert set(manifest()["batches"]) == {"batch-a", "batch-b"}
    card = FakeHub.repo["README.md"].decode()
    assert f"{na['games'] + nb['games']} games, {na['events'] + nb['events']} events" in card
    assert f"{na['labels'] + nb['labels']} labels" in card
    assert "- qwen3-235b" in card and "- other-model" in card
    assert "`batch-a`: 1 games" in card and "`batch-b`: 1 games" in card
    line_b = next(ln for ln in card.splitlines() if ln.startswith("- `batch-b`"))
    assert "other-model" in line_b and "qwen3-235b" not in line_b
    # both batches' shards are still in the repo
    assert "data/events/fixture-0001.parquet" in FakeHub.repo
    assert "data/events/fixture-0002.parquet" in FakeHub.repo


def test_republish_second_batch_is_unchanged(tmp_path):
    a = batch(tmp_path, "batch-a")
    b = batch(tmp_path, "batch-b", "fixture-0002", {"qwen3-235b": "other-model"})
    publish.publish_batch(a, "org/ds", "tok", api_factory=FakeHub)
    publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    first = dict(FakeHub.repo)
    publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert FakeHub.repo == first


def test_missing_manifest_is_rebuilt_from_repo_files(tmp_path):
    """A repo published before the manifest existed: its games are counted, not overwritten."""
    publish.publish_batch(batch(tmp_path, "old"), "org/ds", "tok", api_factory=FakeHub)
    del FakeHub.repo["manifest.json"]
    FakeHub.repo["README.md"] = b"old card from one batch"
    FakeHub.downloads = []
    b = batch(tmp_path, "new", "fixture-0002", {"qwen3-235b": "other-model"})
    nb = publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert ("download", "data/jsonl/fixture-0001.jsonl") in FakeHub.downloads
    m = manifest()
    assert set(m["batches"]) == {publish.LEGACY_BATCH, "new"}
    assert list(m["batches"][publish.LEGACY_BATCH]["games"]) == ["fixture-0001"]
    old = m["batches"][publish.LEGACY_BATCH]["games"]["fixture-0001"]
    assert old["events"] == nb["events"] and old["labels"] == nb["labels"] > 0
    card = FakeHub.repo["README.md"].decode()
    assert "2 games" in card and "- qwen3-235b" in card and "- other-model" in card
    # re-publishing the same batch into the migrated repo changes nothing
    first = dict(FakeHub.repo)
    publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert FakeHub.repo == first


def test_republishing_a_legacy_game_moves_it_to_its_batch(tmp_path):
    publish.publish_batch(batch(tmp_path, "old"), "org/ds", "tok", api_factory=FakeHub)
    del FakeHub.repo["manifest.json"]
    n = publish.publish_batch(batch(tmp_path, "again"), "org/ds", "tok", api_factory=FakeHub)
    assert list(manifest()["batches"]) == ["again"]
    assert f"{n['games']} games" in FakeHub.repo["README.md"].decode()


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


def test_cli_repo_from_env_when_flag_omitted(tmp_path, monkeypatch):
    FakeHub.repo, FakeHub.calls = {}, []
    monkeypatch.setenv("HF_TOKEN", "tok")
    monkeypatch.setenv("SOCIETYSIM_HF_DATASET", "env-org/env-ds")
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.setattr("huggingface_hub.HfApi", FakeHub)
    b = str(batch(tmp_path))
    r = CliRunner().invoke(app, ["publish", b])
    assert r.exit_code == 0, r.output
    assert FakeHub.calls[0][1] == "env-org/env-ds" and "to env-org/env-ds" in r.output
    FakeHub.calls = []
    r = CliRunner().invoke(app, ["publish", b, "--repo", "org/ds"])
    assert r.exit_code == 0, r.output
    assert FakeHub.calls[0][1] == "org/ds"


def test_cli_requires_repo(tmp_path, monkeypatch):
    FakeHub.calls = []
    monkeypatch.setenv("HF_TOKEN", "tok")
    monkeypatch.delenv("SOCIETYSIM_HF_DATASET", raising=False)
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.setattr("huggingface_hub.HfApi", FakeHub)
    r = CliRunner().invoke(app, ["publish", str(batch(tmp_path))])
    assert r.exit_code == 1
    assert "--repo" in r.output and "SOCIETYSIM_HF_DATASET" in r.output
    assert FakeHub.calls == []


def test_cli_publish(tmp_path, monkeypatch):
    FakeHub.repo, FakeHub.calls = {}, []
    monkeypatch.setenv("HF_TOKEN", "tok")
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.setattr("huggingface_hub.HfApi", FakeHub)
    r = CliRunner().invoke(app, ["publish", str(batch(tmp_path)), "--repo", "org/ds"])
    assert r.exit_code == 0, r.output
    assert "published 1 games" in r.output and "data/events/fixture-0001.parquet" in FakeHub.repo


def test_cli_private_by_default_and_public_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "tok")
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.setattr("huggingface_hub.HfApi", FakeHub)
    b = str(batch(tmp_path))
    for extra, want in (([], True), (["--public"], False), (["--private"], True)):
        FakeHub.repo, FakeHub.calls = {}, []
        r = CliRunner().invoke(app, ["publish", b, "--repo", "org/ds", *extra])
        assert r.exit_code == 0, r.output
        assert FakeHub.calls[0][-1] is want


def test_game_without_labels_deletes_stale_shards(tmp_path):
    FakeHub.repo, FakeHub.calls = {}, []
    b = batch(tmp_path)
    (b / "fixture-0001.labels.jsonl").unlink()
    publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert FakeHub.calls[1][3] == [
        "data/labels/fixture-0001.parquet",
        "data/jsonl/fixture-0001.labels.jsonl",
    ]


def _unfinished(b, game_id="fixture-0002"):
    """Add a second game to batch b: the fixture cut off before game_end."""
    lines = (FIXTURES / "sample_game.jsonl").read_text(encoding="utf-8").splitlines()
    cut = [ln for ln in lines if json.loads(ln)["type"] != "game_end"]
    assert len(cut) < len(lines)
    text = "\n".join(cut).replace("fixture-0001", game_id) + "\n"
    (b / f"{game_id}.jsonl").write_text(text, encoding="utf-8", newline="")


def test_unfinished_game_is_not_published(tmp_path, capsys):
    b = batch(tmp_path)
    _unfinished(b)
    n = publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert n["games"] == 1 and n["skipped"] == 1
    assert "fixture-0002.jsonl has no game_end" in capsys.readouterr().out
    assert not any("fixture-0002" in k for k in FakeHub.repo)
    assert list(manifest()["batches"]["batch"]["games"]) == ["fixture-0001"]
    assert "1 games" in FakeHub.repo["README.md"].decode()


def test_batch_with_only_unfinished_games_is_refused(tmp_path):
    b = batch(tmp_path)
    (b / "fixture-0001.jsonl").unlink()
    _unfinished(b)
    with pytest.raises(ValueError, match="no finished game"):
        publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert FakeHub.repo == {}


def test_running_batch_refused_without_force(tmp_path):
    b = batch(tmp_path)
    _unfinished(b)
    (b / "run_meta.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    with pytest.raises(ValueError, match="still running"):
        publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert FakeHub.calls == [] and FakeHub.repo == {}
    # force publishes the finished game only; the unfinished log still stays out
    n = publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub, force=True)
    assert n["games"] == 1 and n["skipped"] == 1
    assert not any("fixture-0002" in k for k in FakeHub.repo)


def test_cli_running_batch_needs_force(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "tok")
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.setattr("huggingface_hub.HfApi", FakeHub)
    b = batch(tmp_path)
    _unfinished(b)
    (b / "run_meta.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    r = CliRunner().invoke(app, ["publish", str(b), "--repo", "org/ds"])
    assert r.exit_code == 1 and "still running" in r.output and FakeHub.calls == []
    r = CliRunner().invoke(app, ["publish", str(b), "--repo", "org/ds", "--force"])
    assert r.exit_code == 0, r.output
    assert "published 1 games" in r.output and "1 unfinished skipped" in r.output
    assert not any("fixture-0002" in k for k in FakeHub.repo)


def test_game_published_earlier_is_kept_when_now_unfinished(tmp_path):
    b = batch(tmp_path)
    done = (FIXTURES / "sample_game.jsonl").read_text(encoding="utf-8")
    (b / "fixture-0002.jsonl").write_text(
        done.replace("fixture-0001", "fixture-0002"), encoding="utf-8", newline=""
    )
    publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert set(manifest()["batches"]["batch"]["games"]) == {"fixture-0001", "fixture-0002"}
    # the same game_id is replayed and cut off: its hub files and manifest entry stay
    _unfinished(b)
    before = dict(FakeHub.repo)
    publish.publish_batch(b, "org/ds", "tok", api_factory=FakeHub)
    assert FakeHub.repo == before


def test_dataset_card_says_ten_seats(tmp_path):
    FakeHub.repo, FakeHub.calls = {}, []
    publish.publish_batch(batch(tmp_path), "org/ds", "tok", api_factory=FakeHub)
    card = FakeHub.repo["README.md"].decode()
    assert "`p0`...`p9`" in card and "p8`" not in card
