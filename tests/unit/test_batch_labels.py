"""A resume replays unfinished games; labels made from their partial logs must not survive."""

from fakes import FakeClient

from societysim.batch.runner import run_batch
from societysim.config import BatchConfig, ModelSpec

SPECS = {"a": ModelSpec(name="a", litellm_model="openai/a")}
PLAYERS = [f"p{i}" for i in range(7)]


def cfg() -> BatchConfig:
    return BatchConfig.model_validate(
        {
            "batch_id": "t",
            "n_games": 2,
            "models": ["a"],
            "budget_usd": 100,
            "game": {"n_players": 7},
            "max_invalid_rate": 1.0,
        }
    )


async def test_batch_replay_deletes_stale_labels_and_keeps_finished_games_labels(tmp_path):
    first = await run_batch(cfg(), SPECS, FakeClient(players=PLAYERS), runs_dir=tmp_path)
    done, cut = first["finished"][0], first["finished"][1]
    batch = tmp_path / "t"
    victim = batch / f"{cut}.jsonl"
    victim.write_text(victim.read_text().splitlines()[0] + "\n")  # crash: no game_end
    stale = batch / f"{cut}.labels.jsonl"
    kept = batch / f"{done}.labels.jsonl"
    stale.write_text('{"source_seq": 99}\n')
    kept.write_text('{"source_seq": 1}\n')

    res = await run_batch(cfg(), SPECS, FakeClient(players=PLAYERS), runs_dir=tmp_path)

    assert res["finished"] == [cut]
    assert not stale.exists()
    assert kept.read_text() == '{"source_seq": 1}\n'
