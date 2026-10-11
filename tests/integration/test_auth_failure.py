"""#424: a bad API key is our config error, not the model's invalid output. LiteLLMClient with
`litellm.acompletion` mocked to reject the key; no network."""

import json

import litellm

from societysim.agents.llm_agent import llm_agents
from societysim.batch.runner import game_invalid, run_batch
from societysim.config import BatchConfig, GameConfig, ModelSpec
from societysim.logging.events import EventType
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.models.client import LiteLLMClient, ProviderLimit
from societysim.orchestrator import run_game

SPEC = ModelSpec(name="gpt-5-nano", litellm_model="openai/gpt-5-nano")
FAST = {"openai": ProviderLimit(concurrency=8, rpm=1_000_000)}


class RejectKey:
    def __init__(self):
        self.calls = 0

    async def __call__(self, **kwargs):
        self.calls += 1
        raise litellm.exceptions.AuthenticationError(
            "Incorrect API key provided: sk-abc", llm_provider="openai", model="gpt-5-nano"
        )


async def test_bad_key_goes_straight_to_the_provider_fallback(tmp_path, monkeypatch):
    fake = RejectKey()
    monkeypatch.setattr(litellm, "acompletion", fake)
    config = GameConfig(seed=4, models=[SPEC.name])
    sink = JsonlSink("b", tmp_path)
    client = LiteLLMClient(limits=FAST)
    await run_game(config, llm_agents(client, {SPEC.name: SPEC}), [sink], "g")
    log = tmp_path / "b" / "g.jsonl"
    events = [json.loads(line) for line in log.read_text().splitlines()]

    invalid = [e for e in events if e["type"] == EventType.INVALID_OUTPUT]
    fallbacks = [e for e in events if e["type"] == EventType.FALLBACK_ACTION]
    assert invalid and fallbacks
    # one call per turn: no corrected-JSON retry after a rejected key
    assert fake.calls == len(fallbacks) == len(invalid)
    assert all(e["meta"]["reason"] == "provider" for e in invalid + fallbacks)
    assert all(e["meta"]["attempt"] == 1 for e in invalid)
    # metrics and the batch's invalid-output stop don't blame the model
    assert game_invalid(log, [SPEC.name] * config.n_players) == {}


async def test_bad_key_stops_the_batch_as_provider_down_on_the_first_call(tmp_path, monkeypatch):
    fake = RejectKey()
    monkeypatch.setattr(litellm, "acompletion", fake)
    cfg = BatchConfig.model_validate(
        {
            "batch_id": "t",
            "n_games": 3,
            "models": [SPEC.name],
            "budget_usd": 100,
            "max_concurrency": 1,
            "game": {"n_players": 7},
        }
    )
    client = LiteLLMClient(limits=FAST)
    res = await run_batch(cfg, {SPEC.name: SPEC}, client, runs_dir=tmp_path)

    assert res["status"] == "failed"
    [down] = res["provider_down"]
    assert down.startswith("gpt-5-nano: the call cannot be made")
    assert "OPENAI_API_KEY" in down and "sk-abc" not in down
    assert len(res["not_started"]) == 2
    assert res["invalid_stop"] is None
    assert fake.calls <= 7  # the first night's parallel calls at most, not ~98 per game
