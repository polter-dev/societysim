import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from societysim.analysis import jev, postbatch
from societysim.analysis.labeler import read_log
from societysim.logging.events import EventType

FIX = Path(__file__).parents[2] / "fixtures"


def _batch(tmp_path: Path, status: str = "completed") -> Path:
    batch = tmp_path / "batch"
    batch.mkdir()
    shutil.copy(FIX / "sample_game.jsonl", batch / "fixture-0001.jsonl")
    (batch / "run_meta.json").write_text(json.dumps({"status": status, "spent_usd": 1.5}))
    return batch


@pytest.fixture
def calls(monkeypatch):
    """Record the commands instead of running them; fake the files the steps would write."""
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(postbatch.subprocess, "run", fake_run)
    monkeypatch.setattr(postbatch, "demo_block", lambda *a: "For DEMO.md")
    return seen


def _fake_read(monkeypatch, status):
    monkeypatch.setattr(
        postbatch, "_read", lambda p: [] if p.name == "highlights.json" else {"status": status}
    )


def _steps(seen):
    return [c[3] if c[2] == "societysim.cli" else "narrate" for c in seen]


def test_free_by_default_and_ordered(tmp_path, calls, monkeypatch):
    _fake_read(monkeypatch, "completed")
    postbatch.main([str(_batch(tmp_path))])
    assert _steps(calls) == ["label", "report", "highlights"]  # no highlights: narrate skipped
    assert "--no-jev" in calls[0]


def test_jev_flag_drops_no_jev(tmp_path, calls, monkeypatch):
    _fake_read(monkeypatch, "completed")
    postbatch.main([str(_batch(tmp_path)), "--jev"])
    assert "--no-jev" not in calls[0]


def test_running_refuses_unless_force(tmp_path, calls, monkeypatch):
    batch = _batch(tmp_path, "running")
    _fake_read(monkeypatch, "running")
    with pytest.raises(SystemExit):
        postbatch.main([str(batch)])
    assert calls == []
    postbatch.main([str(batch), "--force"])
    assert _steps(calls)[0] == "label"


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _running(monkeypatch, batch: Path, **kw) -> None:
    meta = {"status": "running", "updated_at": datetime.now(UTC).isoformat()} | kw
    (batch / "run_meta.json").write_text(json.dumps(meta))
    monkeypatch.setattr(
        postbatch,
        "_read",
        lambda p: [] if p.name == "highlights.json" else json.loads(p.read_text()),
    )


def test_running_with_dead_pid_is_treated_as_interrupted(tmp_path, calls, monkeypatch, capsys):
    batch = _batch(tmp_path, "running")
    _running(monkeypatch, batch, pid=_dead_pid())
    postbatch.main([str(batch)])  # no --force needed
    out = capsys.readouterr().out
    assert "the runner is not alive; treating the batch as interrupted" in out
    assert "warning: batch status is interrupted" in out
    assert _steps(calls)[0] == "label"


def test_running_with_stale_heartbeat_is_treated_as_interrupted(tmp_path, calls, monkeypatch):
    batch = _batch(tmp_path, "running")
    old = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    _running(monkeypatch, batch, updated_at=old)
    postbatch.main([str(batch)])
    assert _steps(calls)[0] == "label"


def test_running_with_live_pid_and_fresh_heartbeat_still_refuses(tmp_path, calls, monkeypatch):
    batch = _batch(tmp_path, "running")
    _running(monkeypatch, batch, pid=os.getpid())
    with pytest.raises(SystemExit, match="still running"):
        postbatch.main([str(batch)])
    assert calls == []


def test_stopped_invalid_warns_and_still_runs_the_steps(tmp_path, calls, monkeypatch, capsys):
    _fake_read(monkeypatch, "stopped_invalid")
    postbatch.main([str(_batch(tmp_path, "stopped_invalid"))])
    assert "warning: batch status is stopped_invalid" in capsys.readouterr().out
    assert _steps(calls) == ["label", "report", "highlights"]


def test_failing_step_stops(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 1 if cmd[3] == "report" else 0)

    monkeypatch.setattr(postbatch.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as e:
        postbatch.main([str(_batch(tmp_path))])
    assert "report" in str(e.value)
    assert _steps(seen) == ["label", "report"]


def test_real_pipeline_on_scripted_batch(tmp_path, capsys):
    batch = _batch(tmp_path, "budget_stopped")
    postbatch.main([str(batch)])
    out = capsys.readouterr().out
    assert "warning: batch status is budget_stopped" in out
    assert (batch / "fixture-0001.labels.jsonl").exists()
    assert (batch / "report" / "metrics.json").exists()
    assert "For DEMO.md" in out
    assert "spent: $1.50" in out
    assert "games played: 1, unfinished: 0" in out
    assert "top models by OpenSkill ordinal" in out
    assert "lie success (self_tag)" in out
    assert "lie success (jev): Jev skipped (run with --jev)" in out
    assert "societysim publish" in out and "databricks_ingest.py --runs runs/" in out
    assert not (batch / "agreement.json").exists()  # no Jev


def test_pick_highlight_prefers_lie_moved_vote():
    ms = [{"type": "doctor_save"}, {"type": "lie_moved_vote"}]
    assert postbatch.pick_highlight(ms) == ms[1]
    assert postbatch.pick_highlight(ms[:1]) == ms[0]
    assert postbatch.pick_highlight([]) is None


def test_demo_block_runs_jev_line_when_jev_ran(tmp_path):
    batch = _batch(tmp_path)
    postbatch.main([str(batch)])
    assert "Jev skipped" not in postbatch.demo_block(batch, None, jev=True)


def _fake_jev_cache(batch: Path) -> None:
    """A complete Jev cache for the fixture: Elif (p4) claims Seer, everyone else nothing."""
    events = read_log(FIX / "sample_game.jsonl")
    start = next(e for e in events if e.type is EventType.GAME_START)
    names = {seat: s.name for seat, s in start.meta.seats.items()}
    for stmt in (e for e in events if e.type is EventType.STATEMENT):
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


def test_postbatch_without_jev_keeps_jev_labels_of_a_fully_cached_batch(
    tmp_path, capfd, monkeypatch
):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    batch = _batch(tmp_path)
    _fake_jev_cache(batch)

    postbatch.main([str(batch)])  # no --jev: label runs with --no-jev

    out = capfd.readouterr().out
    labels = (batch / "fixture-0001.labels.jsonl").read_text()
    assert '"source":"jev"' in labels
    assert (batch / "agreement.json").exists()
    assert "Jev from cache only" in out
    assert "Jev skipped" not in out
