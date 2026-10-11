"""CLI output survives consoles that can't encode the text, and bad batch dirs exit 1."""

import io
import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from societysim import cli
from societysim.analysis import jev
from societysim.analysis.labeler import read_log
from societysim.cli import app
from societysim.logging.events import EventType

REASON = "ServiceUnavailableError: 服务暂时不可用 → retry later ≥ 3"


def cp1252_stdout(monkeypatch) -> io.BytesIO:
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", errors="strict"))
    return raw


def test_echo_replaces_unencodable_text_on_cp1252_stream(monkeypatch):
    raw = cp1252_stdout(monkeypatch)
    cli._echo(f"provider down: {REASON}")
    sys.stdout.flush()
    out = raw.getvalue().decode("cp1252")
    assert out.startswith("provider down: ServiceUnavailableError: ")
    assert "retry later" in out and "?" in out


def test_echo_accepts_paths_and_exceptions(monkeypatch):
    raw = cp1252_stdout(monkeypatch)
    cli._echo(ValueError("bad → input"))
    cli._echo(Path("runs") / "x")
    sys.stdout.flush()
    assert "bad ? input" in raw.getvalue().decode("cp1252")


def test_progress_line_for_failed_game_echoes_on_cp1252(monkeypatch):
    raw = cp1252_stdout(monkeypatch)
    line = cli._progress_line(
        {
            "event": "failed",
            "reason": f"ProviderDown: a: {REASON}",
            "done": 1,
            "total": 3,
            "game_id": "enc-g0000",
            "seconds": 5,
            "spent": 0.0,
            "budget": 1.0,
            "running": 0,
            "eta_seconds": None,
        }
    )
    cli._echo(line)
    sys.stdout.flush()
    assert "[1/3] enc-g0000 failed" in raw.getvalue().decode("cp1252")


@pytest.mark.parametrize("cmd", ["label", "report", "highlights", "sync-neon", "publish"])
def test_missing_batch_dir_exits_1(tmp_path, monkeypatch, cmd):
    monkeypatch.setenv("HF_TOKEN", "x")  # keep the dir check reachable; nothing is called
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://x")
    args = [cmd, str(tmp_path / "no_such_batch")]
    if cmd == "label":
        args.append("--no-jev")
    if cmd == "publish":
        args += ["--repo", "o/n"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "not a directory" in result.output


def test_label_on_empty_batch_dir_exits_1(tmp_path):
    result = CliRunner().invoke(app, ["label", str(tmp_path), "--no-jev"])
    assert result.exit_code == 1
    assert "no game logs" in result.output


def test_report_on_empty_batch_dir_exits_1(tmp_path):
    result = CliRunner().invoke(app, ["report", str(tmp_path)])
    assert result.exit_code == 1


FIXTURE = Path(__file__).parents[2] / "fixtures" / "sample_game.jsonl"


def _fake_jev_cache(batch: Path, skip_every: int = 0) -> int:
    """Write a Jev cache entry for the fixture's statements; every `skip_every`-th is left out.

    Returns how many statements were left out. Elif (p4) claims Seer, so Jev labels exist.
    """
    events = read_log(FIXTURE)
    start = next(e for e in events if e.type is EventType.GAME_START)
    names = {seat: s.name for seat, s in start.meta.seats.items()}
    left_out = 0
    for i, stmt in enumerate(e for e in events if e.type is EventType.STATEMENT):
        if skip_every and i % skip_every == 0:
            left_out += 1
            continue
        answers = {}
        for key, q in jev.build_request(stmt, names)["questions"].items():
            if q["type"] == "noul":
                answers[key] = {"type": "noul", "noul": 0.1}
                continue
            want = {"claim_role": "seer" if stmt.actor == "p4" else "none"}.get(key)
            choice = want if want in q["criteria"] else next(iter(q["criteria"]))
            answers[key] = {"type": "choice", "choice": choice, "confidence": 0.9}
        path = jev.cache_dir_for(batch) / stmt.game_id / f"{stmt.seq}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"answers": answers}), encoding="utf-8")
    return left_out


def _jev_label_count(batch: Path) -> int:
    return sum(p.read_text().count('"source":"jev"') for p in batch.glob("*.labels.jsonl"))


@pytest.fixture
def no_network(monkeypatch):
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    async def boom(self, *a, **kw):
        raise AssertionError("network call")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)


def _batch(tmp_path: Path) -> Path:
    batch = tmp_path / "b"
    batch.mkdir()
    shutil.copy(FIXTURE, batch / "fixture-0001.jsonl")
    return batch


@pytest.mark.parametrize("with_key", [False, True])
def test_label_without_key_or_with_no_jev_keeps_jev_labels_from_full_cache(
    tmp_path, monkeypatch, no_network, with_key
):
    batch = _batch(tmp_path)
    _fake_jev_cache(batch)
    (batch / "agreement.json").write_text("{}")  # stale: must be rewritten, not deleted
    args = ["label", str(batch)]
    if with_key:
        monkeypatch.setenv("TYPESAFE_API_KEY", "fake")
        args.append("--no-jev")

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    n = _jev_label_count(batch)
    assert n > 0
    assert f"{n} Jev labels from 26 cached statements; 0 statements had no cache entry" in (
        result.output
    )
    assert (batch / "agreement.json").read_text().strip() != "{}"


def test_label_without_key_uses_partial_cache_and_counts_skipped(tmp_path, no_network):
    batch = _batch(tmp_path)
    left_out = _fake_jev_cache(batch, skip_every=5)

    result = CliRunner().invoke(app, ["label", str(batch)])

    assert result.exit_code == 0, result.output
    assert f"from {26 - left_out} cached statements; {left_out} statements had no cache" in (
        result.output
    )
    assert _jev_label_count(batch) > 0
    assert (batch / "agreement.json").exists()


def test_label_without_key_and_no_cache_falls_back_and_says_how_many_are_missing(
    tmp_path, no_network
):
    batch = _batch(tmp_path)
    (batch / "agreement.json").write_text("{}")

    result = CliRunner().invoke(app, ["label", str(batch)])

    assert result.exit_code == 0, result.output
    assert "Jev skipped (TYPESAFE_API_KEY not set)" in result.output
    assert "26 statements have no cached Jev response" in result.output
    assert _jev_label_count(batch) == 0
    assert not (batch / "agreement.json").exists()


def test_label_without_key_skips_cache_entry_whose_fingerprint_does_not_match(tmp_path, no_network):
    batch = _batch(tmp_path)
    _fake_jev_cache(batch)
    events = read_log(FIXTURE)
    stmts = [e for e in events if e.type is EventType.STATEMENT]
    stale, good, legacy = stmts[0], stmts[1], stmts[2]
    cache = jev.cache_dir_for(batch) / stale.game_id
    for stmt, fp in [(stale, "0" * 16), (good, jev.fingerprint(good))]:
        path = cache / f"{stmt.seq}.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "fingerprint": fp}))
    assert "fingerprint" not in json.loads((cache / f"{legacy.seq}.json").read_text())

    result = CliRunner().invoke(app, ["label", str(batch)])

    assert result.exit_code == 0, result.output
    # the mismatched entry is a miss; verified and legacy entries still count
    assert "from 25 cached statements; 1 statements had no cache entry" in result.output


def test_cached_verdict_checks_fingerprint_but_trusts_legacy_entries(tmp_path):
    entry = {"answers": {}}
    d = tmp_path / "g1"
    d.mkdir()
    (d / "1.json").write_text(json.dumps(entry))
    (d / "2.json").write_text(json.dumps({**entry, "fingerprint": jev._fingerprint("p1", "hi")}))
    get = jev.cached_verdict
    assert get(tmp_path, "g1", 1, "p1", "anything") == entry  # legacy: reused unverified
    assert get(tmp_path, "g1", 2, "p1", "hi") is not None
    assert get(tmp_path, "g1", 2, "p1", "changed") is None
    assert get(tmp_path, "g1", 2, "p2", "hi") is None
    assert get(tmp_path, "g1", 3, "p1", "hi") is None
