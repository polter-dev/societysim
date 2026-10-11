"""Run the free scripted pipeline on a tiny batch and validate every file the viewer reads
against its schema in schema/ (docs/DATA_CONTRACT.md). The schemas reject unknown keys and
require every key, so a producer that adds, renames or drops a field fails here."""

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from societysim.analysis.highlights import write_highlights
from societysim.analysis.labeler import label_batch
from societysim.analysis.narrate import narrate
from societysim.analysis.report import SCHEMA as REPORT_SCHEMA
from societysim.analysis.report import write_report
from societysim.batch.runner import run_batch
from societysim.config import BatchConfig, ModelSpec

ROOT = Path(__file__).parents[2]
DOC = (ROOT / "docs/DATA_CONTRACT.md").read_text(encoding="utf-8")
FIXTURE = "fixture-0001"  # the hand-written game: every event and label type, real highlights
STATIC = {
    "index": "report/index.json",
    "leaderboard": "report/leaderboard.json",
    "metrics": "report/metrics.json",
    "highlights": "highlights.json",
    "narration_index": f"{FIXTURE}.audio/index.json",
}


def schema(name: str) -> dict:
    s = json.loads((ROOT / f"schema/{name}.schema.json").read_text())
    Draft202012Validator.check_schema(s)
    return s


@pytest.fixture(scope="module")
def batch(tmp_path_factory) -> Path:
    runs = tmp_path_factory.mktemp("runs")
    specs = {n: ModelSpec(name=n, litellm_model=f"openai/{n}") for n in ("a", "b")}
    cfg = BatchConfig.model_validate(
        {"batch_id": "c", "n_games": 2, "models": ["a", "b"], "budget_usd": 1, "game": {}}
    )
    asyncio.run(run_batch(cfg, specs, None, runs_dir=runs))  # scripted: no client, no spend
    b = runs / "c"
    shutil.copy(ROOT / "fixtures/sample_game.jsonl", b / f"{FIXTURE}.jsonl")
    label_batch(b)  # what `label --no-jev` runs
    write_report(b)
    write_highlights(b, top=100)
    stub = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"")))
    bank = tmp_path_factory.mktemp("bank")
    every = ("speech", "wolf_chat", "highlight")
    narrate(b / f"{FIXTURE}.jsonl", max_chars=10**9, select=every, bank=bank, client=stub)
    return b


@pytest.mark.parametrize("name", list(STATIC))
def test_static_file_matches_schema(batch, name):
    data = json.loads((batch / STATIC[name]).read_text())
    assert data, f"{STATIC[name]} is empty; the check would prove nothing"
    errors = [
        f"{list(e.absolute_path)}: {e.message}"
        for e in Draft202012Validator(schema(name)).iter_errors(data)
    ]
    assert not errors, "\n".join(errors[:10])


def test_events_and_labels_match_event_schema(batch):
    s = schema("events")
    events, labels = (
        Draft202012Validator(s),
        Draft202012Validator({"$defs": s["$defs"], "$ref": "#/$defs/Label"}),
    )
    logs = [p for p in batch.glob("*.jsonl") if not p.name.endswith(".labels.jsonl")]
    assert len(logs) == 3
    for log in logs:
        for line in log.read_text().splitlines():
            events.validate(json.loads(line))
        for line in log.with_name(f"{log.stem}.labels.jsonl").read_text().splitlines():
            labels.validate(json.loads(line))


def test_deception_matrix_is_populated_so_its_schema_check_means_something(batch):
    dm = json.loads((batch / STATIC["metrics"]).read_text())["deception_matrix"]
    cells = [c for row in dm["pairs"]["self_tag"].values() for c in row.values()]
    assert cells and dm["baseline"]
    assert any(c["vote_follow_ci"] for c in cells) and any(c["baseline_rate"] for c in cells)


def test_role_skill_is_populated_so_its_schema_check_means_something(batch):
    rs = json.loads((batch / STATIC["metrics"]).read_text())["role_skill"]
    assert rs["doctor"]["all"]["nights"] and rs["seer"]["all"]["checks"]
    assert rs["doctor"]["models"] and rs["seer"]["models"]
    assert rs["doctor"]["all"]["save_ci"] and rs["seer"]["all"]["hit_ci"]
    assert 0 < rs["doctor"]["all"]["chance_rate"] < 1 and 0 < rs["seer"]["all"]["chance_rate"] < 1


def test_index_file_paths_resolve_from_the_batch_dir(batch):
    for entry in json.loads((batch / STATIC["index"]).read_text()):
        assert (batch / entry["file"]).is_file()


def test_highlight_links_and_narration_files(batch):
    for h in json.loads((batch / "highlights.json").read_text()):
        assert h["link"] == f"?game={h['game_id']}&seq={h['seq'][0]}&mode=narrator"
    audio = batch / f"{FIXTURE}.audio"
    clips = json.loads((audio / "index.json").read_text())["clips"]
    assert {c["category"] for c in clips} == {"narrator", "highlight", "speech", "wolf_chat"}
    for clip in clips:
        assert (audio / clip["file"]).is_file()


def test_one_contract_version_everywhere():
    """CONTRACT_VERSION (gen-schema.sh), the doc and leaderboard.json's `schema` move together."""
    versions = {schema(name)["x-contract-version"] for name in STATIC}
    assert len(versions) == 1
    (v,) = versions
    assert f"Contract version: {v}\n" in DOC
    assert v == REPORT_SCHEMA
    assert schema("leaderboard")["properties"]["schema"]["const"] == v


def test_doc_names_every_schema_file():
    for name in [*STATIC, "events"]:
        assert f"schema/{name}.schema.json" in DOC
