"""#344: a finished visitor game gets rule-based labels (as `label --no-jev`) at game end."""

import pytest

pytest.importorskip("fastapi", reason="needs the server extra: uv run --extra server pytest")

from societysim.agents.scripted import RandomAgent  # noqa: E402
from societysim.analysis.labeler import label_game, read_log  # noqa: E402
from societysim.server.ledger import Ledger  # noqa: E402
from societysim.server.manager import GameManager, Settings  # noqa: E402
from societysim.setup import GameSetup  # noqa: E402


async def test_finished_visitor_game_is_labelled(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAY_DEV_MODE", "1")
    settings = Settings(runs_dir=tmp_path, agents=lambda cfg: (RandomAgent, {}), neon=False)
    manager = GameManager(settings, Ledger())
    session, _ = manager.start(GameSetup.basic(), watch_only=True)
    await session.task
    assert session.status == "finished"

    log = tmp_path / "visitor" / f"{session.game_id}.jsonl"
    out = tmp_path / "visitor" / f"{session.game_id}.labels.jsonl"
    labels = label_game(read_log(log))
    assert labels, "a finished game has at least its engine labels"
    assert out.read_text(encoding="utf-8") == "".join(lb.model_dump_json() + "\n" for lb in labels)
    assert {lb.source for lb in labels} <= {"self_tag", "engine"}  # never Jev
