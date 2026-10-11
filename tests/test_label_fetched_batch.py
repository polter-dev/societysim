"""scripts/label_fetched_batch.sh: what deploy.yml runs on a batch fetched from the HF Dataset,
which has logs and labels but no Jev cache (#645)."""

import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from societysim.analysis import jev
from societysim.analysis.labeler import read_log
from societysim.logging.events import EventType

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "label_fetched_batch.sh"
LOG = ROOT / "fixtures" / "sample_game.jsonl"
GAME = "fixture-0001"


def _write_jev_cache(batch: Path) -> None:
    """A complete Jev cache for the fixture: p4 claims Seer, everyone else nothing."""
    events = read_log(LOG)
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


def _cli(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "societysim.cli", *args], check=True, cwd=ROOT, env=_env()
    )


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY", "HF_TOKEN")}


@pytest.fixture
def local(tmp_path) -> Path:
    """The local batch after postbatch: Jev cache, labels from it, agreement.json, report."""
    batch = tmp_path / "local" / "b"
    batch.mkdir(parents=True)
    shutil.copy(LOG, batch / f"{GAME}.jsonl")
    _write_jev_cache(batch)
    _cli("label", str(batch), "--no-jev")
    _cli("report", str(batch))
    return batch


def _fetched(local: Path, tmp_path: Path, labels: bool = True) -> Path:
    """What fetch_hf_batch.py rebuilds from the dataset: logs and labels, nothing else."""
    batch = tmp_path / "ci" / "b"
    batch.mkdir(parents=True)
    shutil.copy(local / f"{GAME}.jsonl", batch)
    if labels:
        shutil.copy(local / f"{GAME}.labels.jsonl", batch)
    return batch


def _cache_zip(local: Path, tmp_path: Path) -> Path:
    """Laid out like the jev-cache-<batch_id> release asset: .cache/jev/<game_id>/<seq>.json."""
    out = tmp_path / "jev_cache.zip"
    with zipfile.ZipFile(out, "w") as z:
        for f in sorted((local / ".cache" / "jev").rglob("*.json")):
            z.write(f, f.relative_to(local).as_posix())
    return out


def _run(*args: Path) -> subprocess.CompletedProcess:
    bash = shutil.which("bash")
    if bash is None or shutil.which("uv") is None:
        pytest.skip("bash or uv not found on PATH")
    r = subprocess.run(
        [bash, str(SCRIPT), *map(str, args)], capture_output=True, text=True, env=_env(), cwd=ROOT
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def _jev_rate(batch: Path) -> float | None:
    metrics = json.loads((batch / "report" / "metrics.json").read_text(encoding="utf-8"))
    return metrics["summary"]["lies"]["jev"]["lie_rate"]


def test_with_the_cache_zip_the_site_matches_the_local_postbatch(local, tmp_path):
    assert _jev_rate(local)  # the fixture has Jev lies, so a real rate to match
    ci = _fetched(local, tmp_path)
    _run(ci, _cache_zip(local, tmp_path))
    assert _jev_rate(ci) == _jev_rate(local)
    for f in ("report/metrics.json", "report/leaderboard.json", "agreement.json"):
        assert (ci / f).read_bytes() == (local / f).read_bytes(), f
    # labels rebuilt from the cache are the published ones
    assert (ci / f"{GAME}.labels.jsonl").read_bytes() == (
        local / f"{GAME}.labels.jsonl"
    ).read_bytes()
    assert (ci / "highlights.json").is_file()


def test_without_a_cache_jev_labels_stay_but_the_rate_is_null_and_it_warns(local, tmp_path):
    ci = _fetched(local, tmp_path)
    r = _run(ci)
    assert "no Jev cache" in r.stdout
    # the published labels, Jev ones included, are kept; no denominator, so no rate
    assert (ci / f"{GAME}.labels.jsonl").read_bytes() == (
        local / f"{GAME}.labels.jsonl"
    ).read_bytes()
    assert '"source":"jev"' in (ci / f"{GAME}.labels.jsonl").read_text(encoding="utf-8")
    assert _jev_rate(ci) is None
    assert not (ci / "agreement.json").exists()
    assert not (ci / ".cache").exists()


def test_without_a_cache_unlabeled_games_get_free_labels(local, tmp_path):
    ci = _fetched(local, tmp_path, labels=False)
    _run(ci)
    labels = (ci / f"{GAME}.labels.jsonl").read_text(encoding="utf-8")
    assert '"source":"self_tag"' in labels
    assert '"source":"jev"' not in labels
    assert (ci / "report" / "metrics.json").is_file()


def test_a_zip_can_only_add_jev_cache_files(local, tmp_path):
    ci = _fetched(local, tmp_path)
    good = f".cache/jev/{GAME}/7.json"
    bad = [
        "planted.jsonl",  # batch root: would be published
        f".cache/jev/{GAME}/../../../planted2.jsonl",
        "/tmp/planted3.json",
        f".cache/jev/{GAME}/sub/1.json",
        ".cache/other/1.json",
        f".cache/jev/{GAME}/8.txt",
    ]
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr(good, "{}")
        for name in bad:
            z.writestr(name, "{}")
        link = zipfile.ZipInfo(f".cache/jev/{GAME}/9.json")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "/etc/passwd")
    before = {p.name for p in ci.iterdir()}
    r = _run(ci, evil)
    assert "restored 1 files, rejected 7" in r.stdout
    cached = [p.relative_to(ci).as_posix() for p in (ci / ".cache").rglob("*") if p.is_file()]
    assert cached == [good]
    assert not list(tmp_path.rglob("planted*"))
    added = {p.name for p in ci.iterdir()} - before
    assert added <= {".cache", "report", "highlights.json", "agreement.json"}
