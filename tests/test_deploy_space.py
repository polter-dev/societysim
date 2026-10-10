import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

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


def _stray_pngs(site: Path) -> list[Path]:
    """PNGs anywhere but og/ (the report charts must not be deployed)."""
    return [p for p in site.rglob("*.png") if p.relative_to(site).parts[0] != "og"]


def _run(
    tmp_path: Path,
    *args: str,
    neon: bool = False,
    extra: dict | None = None,
    prebuilt: bool = False,
):
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("<html></html>")
    if prebuilt:  # the real build copies viewer/public into dist
        for f in ("_headers", "_redirects", "404.html"):
            (dist / f).write_text("built")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_TOKEN", "VITE_NEON"))}
    env.update(SOCIETYSIM_SKIP_VIEWER_BUILD="1", SOCIETYSIM_VIEWER_DIST=str(dist))
    if neon:
        env.update(VITE_NEON_DATA_API_URL="https://secret.example", VITE_NEON_ANON_KEY="k3y")
    env.update(extra or {})
    # On Windows a bare "bash" resolves to the WSL launcher in System32 before PATH,
    # so look it up on PATH (CI puts Git Bash first).
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    return subprocess.run(
        [bash, str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=ROOT
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
    assert not _stray_pngs(out)
    assert "data source: static data/report/index.json" in r.stdout
    assert f"python -m http.server -d {out} 8000" in r.stdout
    assert "?page=leaderboard" in r.stdout
    assert "?game=g&seq=3&mode=narrator" in r.stdout


def test_out_stages_models_catalog_and_setup_schema(tmp_path):
    out = tmp_path / "site"
    r = _run(tmp_path, "--out", str(out), str(_batch(tmp_path)))
    assert r.returncode == 0, r.stderr
    cat = json.loads((out / "models_catalog.json").read_text())
    assert cat["default_lineup"] and cat["models"]
    assert all("api_base" not in m for m in cat["models"])
    assert (out / "devui/schema/game_setup.schema.json").is_file()


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


def test_out_includes_devui_served_with_data(tmp_path):
    batch = _batch(tmp_path)
    out = tmp_path / "site"
    r = _run(tmp_path, "--out", str(out), str(batch))
    assert r.returncode == 0, r.stderr
    assert "/devui/" in r.stdout
    assert (out / "devui/index.html").is_file()
    assert (out / "devui/schema/events.schema.json").is_file()
    assert (out / "index.html").read_text() == "<html></html>"  # real viewer untouched

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(out)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"

        def get(path):
            with urllib.request.urlopen(base + path) as resp:
                return resp.read().decode()

        assert "SocietySim dev UI" in get("/devui/index.html")
        index = json.loads(get("/data/report/index.json"))
        assert json.loads(get("/devui/schema/index.schema.json"))["$id"] == "index.schema.json"
        for name in ("leaderboard", "metrics"):
            json.loads(get(f"/data/report/{name}.json"))
        assert get(f"/data/{index[0]['file']}").strip()
    finally:
        server.shutdown()


def test_devui_config_written_only_with_both_neon_vars(tmp_path):
    batch = _batch(tmp_path)
    out = tmp_path / "site"
    r = _run(tmp_path, "--out", str(out), str(batch), neon=True)
    assert r.returncode == 0, r.stderr
    assert json.loads((out / "devui/config.json").read_text()) == {
        "neon_url": "https://secret.example",
        "neon_anon_key": "k3y",
    }
    assert "k3y" not in r.stdout + r.stderr
    plain = tmp_path / "plain"
    r = _run(tmp_path, "--out", str(plain), str(batch))
    assert not (plain / "devui/config.json").exists()
    assert "::notice::" in r.stdout


def test_devui_config_skipped_when_key_missing(tmp_path):
    batch = _batch(tmp_path)
    out = tmp_path / "site"
    extra = {"VITE_NEON_DATA_API_URL": "https://only-url.example"}
    r = _run(tmp_path, "--out", str(out), str(batch), extra=extra)
    assert r.returncode == 0, r.stderr
    assert not (out / "devui/config.json").exists()


def test_devui_config_carries_auth_url(tmp_path):
    batch = _batch(tmp_path)
    out = tmp_path / "site"
    extra = {
        "VITE_NEON_DATA_API_URL": "https://db.example/rest/v1",
        "VITE_NEON_AUTH_URL": "https://auth.example/neondb/auth",
    }
    r = _run(tmp_path, "--out", str(out), str(batch), extra=extra)
    assert r.returncode == 0, r.stderr
    assert json.loads((out / "devui/config.json").read_text()) == {
        "neon_url": "https://db.example/rest/v1",
        "auth_url": "https://auth.example/neondb/auth",
    }


def test_out_stages_data_at_site_root(tmp_path):
    batch = _batch(tmp_path)
    out = tmp_path / "site"
    r = _run(tmp_path, "--out", str(out), str(batch))
    assert r.returncode == 0, r.stderr
    index = json.loads((out / "index.json").read_text())
    for name in ("leaderboard", "metrics"):
        json.loads((out / f"{name}.json").read_text())
    for entry in index:
        log = out / entry["file"]
        assert log.is_file() and log.with_suffix(".labels.jsonl").is_file()
    assert not (out / "report").exists() and not _stray_pngs(out)
    assert (out / "data/report/index.json").exists()  # existing viewer path still works
    headers = (out / "_headers").read_text()
    assert "Access-Control-Allow-Origin: *" in headers and "application/x-ndjson" in headers
    assert (out / "_redirects").is_file()
    assert (out / "404.html").is_file()  # keeps Pages from serving the SPA for a missing file


def test_out_keeps_static_config_already_in_dist(tmp_path):
    out = tmp_path / "site"
    r = _run(tmp_path, "--out", str(out), str(_batch(tmp_path)), prebuilt=True)
    assert r.returncode == 0, r.stderr
    assert (out / "_headers").read_text() == "built"


SECRETS = re.compile(r"\b(wolf|wolves|villager|seer|doctor|winner)\b", re.IGNORECASE)


def _preview_text(page: str) -> str:
    """What a chat app shows: <title> plus the description, og:* and twitter:* meta."""
    keep = ("<title>", '<meta name="description"', '<meta property="og:', '<meta name="twitter:')
    return " ".join(ln for ln in page.splitlines() if ln.startswith(keep))


def test_leak_check_catches_role_words():
    page = '<title>Day 2</title>\n<meta property="og:description" content="The wolf won">'
    assert SECRETS.search(_preview_text(page))
    assert SECRETS.search(_preview_text("<title>The Seer</title>"))
    assert SECRETS.search(_preview_text('<meta name="twitter:title" content="Wolves win">'))
    assert not SECRETS.search(_preview_text("<title>A Werewolf game</title>"))


def test_out_writes_og_cards_without_hidden_info(tmp_path):
    batch = _batch(tmp_path)
    gid = json.loads((batch / "report/index.json").read_text())[0]["game_id"]
    moment = {
        "game_id": gid,
        "day": 2,
        "seq": [57, 60],
        "description": "Cleo (p1, doctor) saved Dev (p4) from the wolves.",
        "link": f"?game={gid}&seq=57&mode=narrator",
    }
    evil = {"game_id": "../evil", "day": 1, "seq": [1, 2], "link": "?x"}
    (batch / "highlights.json").write_text(json.dumps([moment, evil]))
    out = tmp_path / "site"
    site = {"SOCIETYSIM_SITE_URL": "https://x.test"}
    r = _run(tmp_path, "--out", str(out), str(batch), extra=site)
    assert r.returncode == 0, r.stderr
    og = out / "og"
    for name in ("card.png", f"{gid}.png", f"{gid}-57.png"):
        assert (og / name).read_bytes()[1:4] == b"PNG"
    assert sorted(p.name for p in og.glob("*.html")) == [f"{gid}-57.html", f"{gid}.html"]
    page = (og / f"{gid}-57.html").read_text()
    assert f'<meta property="og:image" content="https://x.test/og/{gid}-57.png">' in page
    assert '<meta name="twitter:card" content="summary_large_image">' in page
    assert f"url=../?game={gid}&amp;seq=57&amp;mode=narrator" in page
    assert "Day 2" in page
    # no roles, winner or highlight description in any card
    for f in og.glob("*.html"):
        meta = _preview_text(f.read_text())
        assert "og:title" in meta and "<title>" in meta and "twitter:description" in meta
        assert not SECRETS.search(meta), f.name
        assert "cleo" not in meta.lower() and "saved" not in meta.lower()


def test_viewer_html_has_site_wide_card():
    text = (ROOT / "viewer/index.html").read_text()
    for needle in ("og:title", "og:description", "og:image", "twitter:card", "twitter:image"):
        assert needle in text
    assert 'content="https://society-sim.us/og/card.png"' in text
