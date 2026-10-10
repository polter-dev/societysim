import json
import shutil
import subprocess
from pathlib import Path

import pytest

from societysim.analysis import postbatch

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
    monkeypatch.setattr(postbatch, "demo_block", lambda b, top: "For DEMO.md")
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
    assert "societysim publish" in out and "databricks_ingest.py --runs runs/" in out
    assert not (batch / "agreement.json").exists()  # no Jev


def test_pick_highlight_prefers_lie_moved_vote():
    ms = [{"type": "doctor_save"}, {"type": "lie_moved_vote"}]
    assert postbatch.pick_highlight(ms) == ms[1]
    assert postbatch.pick_highlight(ms[:1]) == ms[0]
    assert postbatch.pick_highlight([]) is None
