"""CLI output survives consoles that can't encode the text, and bad batch dirs exit 1."""

import io
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from societysim import cli
from societysim.cli import app

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
