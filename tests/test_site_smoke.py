# ruff: noqa: E501  (HTML fixture strings)
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("site_smoke", ROOT / "scripts/site_smoke.py")
smoke = importlib.util.module_from_spec(spec)
sys.modules["site_smoke"] = smoke  # dataclasses look the module up
spec.loader.exec_module(smoke)


def test_page_url_keeps_query_and_adds_slash():
    assert (
        smoke.page_url("https://x.test", "?page=model&model=a b")
        == "https://x.test/?page=model&model=a b"
    )
    assert smoke.page_url("https://x.test/sub", "index.json") == "https://x.test/sub/index.json"


def test_routes_only_use_known_context_values():
    for r in smoke.ROUTES:
        used = {n for n in ("game", "step", "model") if "{" + n + "}" in r.query}
        assert used == set(r.needs), r.name


def test_pick_context_takes_first_game_first_model_and_a_mid_game_step():
    log = "\n".join(json.dumps({"seq": i}) for i in range(9)) + "\n"
    ctx = smoke.pick_context(
        [{"game_id": "g1"}, {"game_id": "g2"}], {"models": [{"model": "m1"}]}, log
    )
    assert ctx == {"game": "g1", "model": "m1", "step": "3"}
    assert smoke.pick_context([], {}, "") == {}


def test_role_leaks_flags_only_unrevealed_seats_with_role_names():
    cards = [
        {"seat": "p0", "revealed": "false", "text": "?\nrole unknown"},
        {"seat": "p1", "revealed": "false", "text": "Wolf"},
        {"seat": "p2", "revealed": "true", "text": "Seer"},
        {"seat": "p3", "revealed": "false", "text": "Doctor"},
    ]
    assert [leak.split(":")[0] for leak in smoke.role_leaks(cards)] == ["p1", "p3"]


def test_console_filter_ignores_optional_404s_and_turnstile_but_not_page_errors():
    f = smoke.ignorable_console_error
    err = "Failed to load resource: the server responded with a status of 404 ()"
    assert f(err, "https://s.test/favicon.ico")
    assert f(err, "https://s.test/sample_game.audio/index.json")
    assert not f(err, "https://s.test/leaderboard.json")
    assert f("anything", "https://challenges.cloudflare.com/x")
    assert not f("TypeError: x is undefined", "https://s.test/assets/index.js")


def test_step_beat_reads_the_label():
    assert smoke.step_beat("beat 29 / 73") == 29
    assert smoke.step_beat("nothing") is None


def test_table_counts_failures():
    out = smoke.table([smoke.Result("a", "x", True, "ok"), smoke.Result("b", "y", False, "bad")])
    assert "FAIL" in out and "1 passed, 1 failed" in out


# ---- the browser run against a local static site ---------------------------------------------

# SITE_SMOKE_CHANNEL=chrome uses an installed Chrome when `playwright install chromium` wasn't run.
CHANNEL = os.environ.get("SITE_SMOKE_CHANNEL")
SEATS = ["p0", "p1", "p2", "p3", "p4", "p5", "p6"]


def _seats(leak: bool) -> str:
    cards = "".join(
        f'<li data-seat="{s}" data-revealed="false"><button>{s}'
        f"<span data-role-card>{'Wolf' if leak and s == 'p2' else '? role unknown'}</span></button></li>"
        for s in SEATS
    )
    return (
        f"<ul>{cards}</ul><span class='step-label'>beat {{beat}} / 9</span>"
        "<details class='replay-spotwolf'><summary>Spot the wolf</summary><section class='spotwolf'>"
        "<ul class='spotwolf-seats'>"
        + "".join(f"<li><button aria-pressed='false'>{s}</button></li>" for s in SEATS)
        + "</ul></section></details>"
        "<script>document.querySelector('[data-seat] button').onclick="
        "()=>{document.body.insertAdjacentHTML('beforeend','<b class=role-badge>role unknown</b>')}</script>"
    )


def _page(query: dict, leak: bool) -> str:
    page, step = query.get("page", [""])[0], query.get("step")
    if "game" in query:
        body = "<h1>SocietySim viewer</h1>" + _seats(leak).replace("{beat}", "4" if step else "1")
    elif page == "leaderboard":
        body = "<h1>SocietySim leaderboard</h1><table><caption>Leaderboard</caption><tbody><tr><td>m1</td></tr></tbody></table>"
    elif page == "research":
        body = "<h1>SocietySim research</h1><section class='deception-matrix'><p>No lies heard</p></section>"
    elif page == "model":
        body = "<h1>SocietySim m1</h1><h2>m1</h2><dl><dt>Games</dt><dd>1</dd></dl>"
    elif page == "new":
        body = "<h1>SocietySim new game</h1><form class='setup-form'><label>Mode</label></form><pre class='setup-json'>{}</pre>"
    elif page == "play":
        body = "<h1>SocietySim play</h1><section class='play-coming-soon'>coming soon</section>"
    else:
        body = "<h1>SocietySim games</h1><main><table><tr><td><a href='?game=g1'>g1</a></td></tr></table></main>"
    return f"<!doctype html><html><body><main>{body}</main></body></html>"


def _serve(leak: bool):
    files = {
        "/index.json": json.dumps([{"game_id": "g1", "file": "g1.jsonl"}]),
        "/leaderboard.json": json.dumps({"models": [{"model": "m1"}]}),
        "/metrics.json": "{}",
        "/g1.jsonl": "\n".join(json.dumps({"seq": i}) for i in range(9)),
    }

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlsplit(self.path)
            if u.path in files:
                data, kind = files[u.path], "application/json"
            else:
                data, kind = _page(parse_qs(u.query), leak), "text/html"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.end_headers()
            self.wfile.write(data.encode())

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def browser_ready():
    sync = pytest.importorskip("playwright.sync_api")
    try:
        with sync.sync_playwright() as pw:
            pw.chromium.launch(channel=CHANNEL).close()
    except Exception as e:  # browser not installed (playwright install chromium)
        pytest.skip(f"no chromium: {str(e).splitlines()[0]}")


def _run(leak: bool):
    srv = _serve(leak)
    try:
        return smoke.run(f"http://127.0.0.1:{srv.server_port}/", timeout_ms=5000, channel=CHANNEL)
    finally:
        srv.shutdown()


def test_run_passes_on_a_healthy_site(browser_ready):
    results = _run(leak=False)
    assert [r for r in results if not r.ok] == []
    assert {r.page for r in results} >= {
        "index",
        "replay",
        "step deep link",
        "leaderboard",
        "research",
        "model profile",
        "new game",
        "play",
        "spot the wolf",
    }


def test_run_fails_when_an_unrevealed_seat_shows_a_role(browser_ready):
    failed = [r for r in _run(leak=True) if not r.ok]
    assert [(r.page, r.check) for r in failed] == [("replay", "hidden info")]
