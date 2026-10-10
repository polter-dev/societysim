import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "deploy-space.sh"
FIXTURES = ROOT / "fixtures"


def _batch(tmp_path: Path) -> Path:
    batch = tmp_path / "batch"
    batch.mkdir()
    shutil.copy(FIXTURES / "sample_game.jsonl", batch)
    shutil.copy(FIXTURES / "sample_game.labels.jsonl", batch)
    subprocess.run(
        [sys.executable, "-m", "societysim.cli", "report", str(batch)], check=True, cwd=ROOT
    )
    (batch / "report" / "x.png").write_bytes(b"png")
    (batch / ".cache").mkdir()
    (batch / ".cache" / "judge.json").write_text("{}")
    (batch / "highlights.json").write_text(json.dumps([{"link": "?game=g&seq=3&mode=narrator"}]))
    return batch


def _run(tmp_path: Path, *args: str, neon: bool = False):
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("<html></html>")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_TOKEN", "VITE_NEON"))}
    env.update(SOCIETYSIM_SKIP_VIEWER_BUILD="1", SOCIETYSIM_VIEWER_DIST=str(dist))
    if neon:
        env.update(VITE_NEON_DATA_API_URL="https://secret.example", VITE_NEON_ANON_KEY="k3y")
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=ROOT
    )


def test_out_stages_site(tmp_path):
    batch = _batch(tmp_path)
    out = tmp_path / "site"
    r = _run(tmp_path, "--out", str(out), str(batch))
    assert r.returncode == 0, r.stderr
    assert (out / "index.html").exists()
    index_path = out / "data/report/index.json"
    index = json.loads(index_path.read_text())
    assert index
    for entry in index:
        # the viewer resolves each log as "../" + file relative to index.json
        log = (index_path.parent / ".." / entry["file"]).resolve()
        assert log.is_file()
        assert log.with_suffix(".labels.jsonl").is_file()
    assert not (out / "data/.cache").exists()
    assert not list(out.rglob("*.png"))
    assert "data source: static data/report/index.json" in r.stdout
    assert f"python -m http.server -d {out} 8000" in r.stdout
    assert "?page=leaderboard" in r.stdout
    assert "?game=g&seq=3&mode=narrator" in r.stdout


def test_out_refuses_non_empty_dir_without_force(tmp_path):
    batch = _batch(tmp_path)
    out = tmp_path / "site"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    r = _run(tmp_path, "--out", str(out), str(batch))
    assert r.returncode == 1 and "--force" in r.stderr
    assert _run(tmp_path, "--out", str(out), "--force", str(batch)).returncode == 0
    assert (out / "data/report/index.json").exists()


def test_neon_source_line_hides_secrets(tmp_path):
    batch = _batch(tmp_path)
    r = _run(tmp_path, "--out", str(tmp_path / "site"), str(batch), neon=True)
    assert "Neon (VITE_NEON_DATA_API_URL is set)" in r.stdout
    assert "secret.example" not in r.stdout + r.stderr
    assert "k3y" not in r.stdout + r.stderr


def test_dry_run_still_lists_and_uploads_nothing(tmp_path):
    batch = _batch(tmp_path)
    r = _run(tmp_path, "--dry-run", str(batch), "owner/name")
    assert r.returncode == 0, r.stderr
    assert "dry run: nothing uploaded" in r.stdout
    assert "./data/report/index.json" in r.stdout
