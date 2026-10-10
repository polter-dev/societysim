"""Play alerts (#309): thresholds fire once, the kill switch and failed starts post, and the
webhook URL never shows up in logs."""

import logging

import pytest

pytest.importorskip("fastapi", reason="needs the server extra: uv run --extra server pytest")

from societysim.server import alerts
from societysim.server.alerts import Alerts
from societysim.server.ledger import Ledger
from societysim.server.manager import Settings
from societysim.server.security import PlayConfig

URL = "https://discord.example/api/webhooks/123/SECRET-TOKEN-VALUE"


@pytest.fixture
def posted(monkeypatch):
    sent = []
    monkeypatch.setenv(alerts.ENV, URL)
    monkeypatch.setattr(alerts, "_post", lambda url, text: sent.append((url, text)))
    return sent


async def test_spend_thresholds_fire_once(posted):
    a = Alerts()
    await a.spend(0.4, 1.0)
    assert posted == []
    await a.spend(0.5, 1.0)
    await a.spend(0.6, 1.0)
    assert len(posted) == 1 and "50%" in posted[0][1]
    await a.spend(0.95, 1.0)
    await a.spend(1.0, 1.0)
    assert len(posted) == 2 and "90%" in posted[1][1]
    assert all(url == URL for url, _ in posted)


async def test_one_jump_past_both_thresholds_posts_both(posted):
    await Alerts().spend(0.95, 1.0)
    assert [("50%" in t, "90%" in t) for _, t in posted] == [(True, False), (False, True)]


async def test_kill_switch_alerts_on_change_only(posted):
    a = Alerts()
    await a.kill_switch(True)  # startup with play on: quiet
    await a.kill_switch(True)
    assert posted == []
    await a.kill_switch(False)
    await a.kill_switch(False)
    assert len(posted) == 1 and "ON" in posted[0][1]
    await a.kill_switch(True)
    assert len(posted) == 2 and "OFF" in posted[1][1]


async def test_startup_with_kill_switch_on_alerts(posted):
    await Alerts().kill_switch(False)
    assert len(posted) == 1 and "ON" in posted[0][1]


async def test_no_webhook_is_a_noop(monkeypatch):
    monkeypatch.delenv(alerts.ENV, raising=False)
    monkeypatch.setattr(alerts, "_post", lambda *a: pytest.fail("posted without a webhook"))
    await Alerts().spend(2.0, 1.0)
    await Alerts().kill_switch(False)


async def test_ledger_booking_triggers_alert(posted):
    ledger = Ledger(1.0)
    ledger.on_spend = Alerts().spend
    held = ledger.try_reserve(0.6)
    await ledger.settle("g1", held, 0.6)
    assert len(posted) == 1 and "50%" in posted[0][1]


async def test_failing_alert_does_not_break_booking():
    ledger = Ledger(1.0)

    async def boom(*_):
        raise RuntimeError("down")

    ledger.on_spend = boom
    held = ledger.try_reserve(0.1)
    await ledger.settle("g1", held, 0.1)
    assert ledger.spent_usd() == pytest.approx(0.1)


def test_failed_start_posts_type_only_and_raises(posted, monkeypatch):
    from societysim.server.app import create_app

    for name in ("TURNSTILE_SECRET_KEY", "PLAY_DEV_MODE", "SPACE_HOST"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError):  # no Turnstile secret and not dev mode: check_startup fails
        create_app(Settings(neon=False, play=PlayConfig()))
    assert len(posted) == 1 and "failed to start" in posted[0][1]


def test_send_failure_logs_type_not_url(monkeypatch, caplog):
    monkeypatch.setenv(alerts.ENV, URL)

    def refuse(*_a, **_k):
        raise OSError(f"cannot reach {URL}")

    monkeypatch.setattr(alerts.urllib.request, "urlopen", refuse)
    with caplog.at_level(logging.WARNING):
        alerts.send("x")
    assert "OSError" in caplog.text and "SECRET-TOKEN-VALUE" not in caplog.text


def test_webhook_url_counts_as_a_secret_name():
    from societysim.server.security import secret_names

    assert alerts.ENV in secret_names({alerts.ENV: URL})
