"""Orchestrator tests: every call goes LLMAgent -> FakeClient, no network."""

import random

from fakes import FakeClient

from societysim.agents.base import Turn
from societysim.agents.llm_agent import LLMAgent, llm_agents
from societysim.agents.prompts import player
from societysim.config import GameConfig, ModelSpec, RoleCounts
from societysim.engine.state import GameState
from societysim.logging.events import EventLogger, EventType
from societysim.models.client import Usage
from societysim.orchestrator import DEFAULT_COLOR, SILENT, Game, _add, run_game
from societysim.roles import Role

SPEC = ModelSpec(name="fake", litellm_model="fake/fake")


def reply(others, action, p=0.5):
    return {
        "private_reasoning": {
            "thoughts": "t",
            "suspicion": [{"player": s, "p": p} for s in others],
        },
        "action": action,
    }


def valid(turn: Turn, rng: random.Random) -> dict:
    pick = rng.choice(turn.targets) if turn.targets else None
    action = {
        "statement": {
            "text": "I have a feeling.",
            "accuse": [pick],
            "claim_role": None,
            "seer_report": None,
        },
        "vote": {"vote": pick},
        "wolf_chat": {"message": "go", "proposed_target": pick},
        "lone_wolf": {"target": pick},
        "seer": {"target": pick},
        "doctor": {"target": pick},
    }[turn.action]
    return reply(turn.others, action)


class FakeAgent(LLMAgent):
    """LLMAgent over a FakeClient. Replies from `scripts[action]` first, then a valid reply."""

    def __init__(self, state: GameState, seat: str):
        super().__init__(FakeClient(), SPEC, "system")
        self.state, self.seat = state, seat
        self.rng = random.Random(f"{state.config.seed}-{seat}")
        self.scripts: dict[str, list] = {}
        self.turns: list[Turn] = []

    async def act(self, turn):
        assert self.seat in self.state.alive, "dead players make no calls"
        script = self.scripts.get(turn.action)
        self.client.script.append(script.pop(0) if script else valid(turn, self.rng))
        self.turns.append(turn)
        return await super().act(turn)


def new_game(config: GameConfig | None = None) -> Game:
    return Game(config or GameConfig(seed=3), FakeAgent, EventLogger("g", []))


def seats(game, role):
    return [s for s in game.state.seats if game.state.roles[s] is role]


def others(game, seat):
    return [s for s in game.state.seats if s in game.state.alive and s != seat]


def of(game, type_, actor=None):
    return [e for e in game.events if e.type is type_ and (actor is None or e.actor == actor)]


def test_single_cached_attempt_keeps_cached_usage():
    cached = Usage(cached=True, model="fake", input_tokens=10)

    total = _add(None, cached)

    assert total is cached
    assert total.cached is True


def test_cached_and_live_retry_is_not_cached():
    cached = Usage(cached=True, model="fake", input_tokens=10, output_tokens=2, cost_usd=0.125)
    live = Usage(model="fake", input_tokens=20, output_tokens=3, cost_usd=0.25)

    total = _add(_add(None, cached), live)

    assert type(total) is Usage
    assert total.cached is False
    assert total.input_tokens == 30
    assert total.output_tokens == 5
    assert total.cost_usd == 0.375


def test_two_cached_attempts_keep_cached_and_sum_usage():
    first = Usage(
        cached=True, model="fake", input_tokens=10, output_tokens=2, cost_usd=0.125, latency_ms=5
    )
    second = Usage(
        cached=True, model="fake", input_tokens=20, output_tokens=3, cost_usd=0.25, latency_ms=7
    )

    total = _add(_add(None, first), second)

    assert total.cached is True
    assert total.model == "fake"
    assert total.input_tokens == 30
    assert total.output_tokens == 5
    assert total.cost_usd == 0.375
    assert total.latency_ms == 12
    assert total.attempts == 2


async def test_retry_then_success_appends_error_naming_missing_player():
    game = new_game()
    seer = seats(game, Role.SEER)[0]
    rest = others(game, seer)
    bad = reply([seer, *rest[1:]], {"target": rest[0]})  # self instead of rest[0]
    game.agents[seer].scripts["seer"] = [bad, reply(rest, {"target": rest[0]})]

    await game.night()

    [invalid] = of(game, EventType.INVALID_OUTPUT)
    assert invalid.actor == seer and invalid.meta.attempt == 1 and invalid.meta.action == "seer"
    missing = player(rest[0], game.state.names[rest[0]])
    assert f"missing {missing}" in invalid.meta.error
    assert "must not include" in invalid.meta.error
    assert invalid.meta.raw and invalid.visibility == "narrator"
    retry = game.agents[seer].turns[-1].prompt
    assert retry.startswith(game.agents[seer].turns[0].prompt) and missing in retry
    [check] = of(game, EventType.SEER_CHECK)
    assert check.target == rest[0] and check.meta.fallback is None
    assert check.meta.usage.attempts == 2 and check.meta.usage.input_tokens == 200
    assert not of(game, EventType.FALLBACK_ACTION)


async def test_night_retry_then_fallback_picks_random_legal_target():
    game = new_game()
    seer = seats(game, Role.SEER)[0]
    game.agents[seer].scripts["seer"] = [reply(others(game, seer), {"target": seer})] * 2

    await game.night()

    assert [e.meta.attempt for e in of(game, EventType.INVALID_OUTPUT)] == [1, 2]
    [fb] = of(game, EventType.FALLBACK_ACTION)
    [check] = of(game, EventType.SEER_CHECK)
    assert fb.actor == seer and fb.meta.action == "seer" and fb.meta.usage.attempts == 2
    assert check.target == fb.target and check.target in game.agents[seer].turns[0].targets
    assert check.meta.usage is None and check.suspicion is None
    assert check.meta.fallback is True
    assert game.events.index(fb) < game.events.index(check)


async def test_doctor_fallback_marks_doctor_save():
    game = new_game()
    doctor = seats(game, Role.DOCTOR)[0]
    game.agents[doctor].scripts["doctor"] = ["not json"] * 2

    await game.night()

    [fb] = of(game, EventType.FALLBACK_ACTION)
    [save] = of(game, EventType.DOCTOR_SAVE)
    assert fb.actor == doctor and fb.meta.action == "doctor" and save.target == fb.target
    assert save.meta.fallback is True and save.meta.usage is None and save.suspicion is None
    assert of(game, EventType.SEER_CHECK)[0].meta.fallback is None


async def test_vote_and_statement_fallbacks():
    game = new_game()
    await game.night()
    voter = sorted(game.state.alive)[0]
    agent = game.agents[voter]
    agent.scripts["vote"] = [reply(others(game, voter), {"vote": voter})] * 2  # self-vote
    agent.scripts["statement"] = ["not json", "still not json"]

    await game.day()

    statements = of(game, EventType.STATEMENT, voter)
    assert statements[0].public_text == SILENT and statements[0].meta.fallback is True
    assert statements[0].meta.usage is None and statements[1].meta.fallback is None
    [vote] = of(game, EventType.VOTE, voter)
    assert vote.target is None and vote.meta.fallback is True and vote.meta.usage is None
    fbs = of(game, EventType.FALLBACK_ACTION, voter)
    assert [f.meta.action for f in fbs] == ["statement", "vote"]
    assert fbs[1].target is None and fbs[1].meta.usage.attempts == 2
    errors = [e.meta.error for e in of(game, EventType.INVALID_OUTPUT, voter)]
    assert len(errors) == 4 and "not allowed" in errors[-1]
    assert all(v.meta.usage for v in of(game, EventType.VOTE) if v.actor != voter)


async def wolves_split(seed=3):
    game = new_game(GameConfig(seed=seed))
    a, b = seats(game, Role.WOLF)
    x, y = [s for s in game.state.seats if game.state.roles[s] is not Role.WOLF][:2]
    for wolf, target in ((a, x), (b, y)):
        action = {"message": "m", "proposed_target": target}
        game.agents[wolf].scripts["wolf_chat"] = [reply(others(game, wolf), action)] * 2
    await game.night()
    return game, {x, y}


async def test_wolf_disagreement_is_broken_by_seeded_rng():
    game, split = await wolves_split()
    chat = of(game, EventType.WOLF_CHAT)
    assert [e.meta.round for e in chat] == [1, 1, 2, 2]
    assert all(e.meta.usage for e in chat)
    assert len(of(game, EventType.WOLF_PROPOSAL)) == 4
    [kill] = of(game, EventType.NIGHT_KILL)
    assert kill.meta.method == "tiebreak" and kill.target in split
    assert kill.meta.tally == dict.fromkeys(split, 1)
    again, _ = await wolves_split()
    assert of(again, EventType.NIGHT_KILL)[0].target == kill.target


async def test_lone_wolf_gets_target_prompt_and_logs_only_proposal():
    game = new_game(GameConfig(seed=5, roles=RoleCounts(wolves=1)))
    [wolf] = seats(game, Role.WOLF)

    await game.night()

    [turn] = game.agents[wolf].turns
    assert turn.action == "lone_wolf" and '"action" shape: {"target": id}' in turn.prompt
    assert "Wolf channel" not in turn.prompt and "message" not in turn.prompt
    # LOG_SCHEMA night order for a lone wolf: no wolf_chat, the proposal carries the reply.
    assert [e.type for e in game.events] == [
        EventType.PHASE_START,
        EventType.WOLF_PROPOSAL,
        EventType.SEER_CHECK,
        EventType.NIGHT_KILL,
        EventType.DOCTOR_SAVE,
        EventType.DEATH_REVEAL,
    ]
    proposal = game.events[1]
    assert proposal.actor == wolf and proposal.visibility == "wolves"
    assert proposal.meta.round == 1 and proposal.meta.usage and proposal.meta.fallback is None
    assert proposal.suspicion and proposal.private_reasoning == "t"
    [kill] = of(game, EventType.NIGHT_KILL)
    assert kill.meta.method == "consensus" and kill.target == proposal.target
    assert kill.meta.tally == {proposal.target: 1}


async def test_lone_wolf_fallback():
    game = new_game(GameConfig(seed=5, roles=RoleCounts(wolves=1)))
    [wolf] = seats(game, Role.WOLF)
    game.agents[wolf].scripts["lone_wolf"] = ["not json"] * 2

    await game.night()

    [fb] = of(game, EventType.FALLBACK_ACTION)
    [proposal] = of(game, EventType.WOLF_PROPOSAL)
    assert fb.actor == wolf and fb.meta.action == "lone_wolf" and fb.meta.usage.attempts == 2
    assert proposal.target == fb.target and proposal.meta.fallback is True
    assert proposal.meta.usage is None and proposal.suspicion is None
    assert of(game, EventType.NIGHT_KILL)[0].target == fb.target


async def test_statement_truncation_and_self_tags():
    game = new_game()
    await game.night()
    speaker = sorted(game.state.alive)[0]
    a, b = others(game, speaker)[:2]
    action = {
        "text": " ".join(["word"] * 100),
        "accuse": [a, b, a],
        "claim_role": "seer",
        "seer_report": {"target": a, "result": "wolf", "night": 1},
    }
    game.agents[speaker].scripts["statement"] = [reply(others(game, speaker), action)]

    await game.day()

    statement = of(game, EventType.STATEMENT, speaker)[0]
    assert len(statement.public_text.split()) == 80 and statement.meta.truncated is True
    assert statement.meta.usage and statement.suspicion
    i = game.events.index(statement)
    tags = game.events[i + 1 : i + 5]
    assert [e.type for e in tags] == [
        EventType.ACCUSATION,
        EventType.ACCUSATION,
        EventType.ROLE_CLAIM,
        EventType.SEER_CLAIM,
    ]
    assert [e.target for e in tags[:2]] == [a, b]
    assert all(e.meta.statement_seq == statement.seq and e.actor == speaker for e in tags)
    assert tags[2].meta.role is Role.SEER
    assert (tags[3].target, tags[3].meta.result, tags[3].meta.night) == (a, "wolf", 1)


async def test_full_game_is_deterministic_and_complete():
    async def play(seed, wolves):
        config = GameConfig(seed=seed, models=["fake"], roles=RoleCounts(wolves=wolves))
        return await run_game(config, FakeAgent, [], "g")

    for seed, wolves in [(s, w) for s in range(6) for w in (2, 1)]:
        game = await play(seed, wolves)
        events = game.events
        assert events[0].type is EventType.GAME_START and events[0].meta.seats
        assert events[-1].type is EventType.GAME_END and game.winner == events[-1].meta.winner
        assert [e.seq for e in events] == list(range(len(events)))
        llm = (EventType.STATEMENT, EventType.VOTE, EventType.WOLF_CHAT, EventType.SEER_CHECK)
        assert all(e.meta.usage for e in events if e.type in llm and not e.meta.fallback)
        assert events[1].day == 1 and events[1].phase == "night"
        again = await play(seed, wolves)
        assert [e.model_dump() for e in again.events] == [e.model_dump() for e in events]


def test_llm_agents_builds_seat_system_prompt():
    client = FakeClient()
    state = GameState(GameConfig(models=["fake"]))
    agent = llm_agents(client, {"fake": SPEC})(state, "p0")
    assert agent.spec is SPEC and state.names["p0"] in agent.system


async def test_model_without_color_gets_default():
    game = await run_game(GameConfig(models=["fake"]), FakeAgent, [], "g", colors={"fake": None})
    seats = game.events[0].meta.seats
    assert {seat.color for seat in seats.values()} == {DEFAULT_COLOR}
