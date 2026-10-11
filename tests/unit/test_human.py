"""HumanAgent with scripted stdin: plain prompts, the orchestrator's validator, and leakage."""

import json

import pytest

from societysim import orchestrator
from societysim.agents.base import Turn
from societysim.agents.human import (
    LEVELS,
    THOUGHTS,
    HumanAgent,
    HumanSink,
    choose,
    plain,
    reasoning,
    with_human,
)
from societysim.agents.prompts import phase_prompt
from societysim.agents.scripted import RandomAgent
from societysim.config import GameConfig
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventLogger, EventType, Suspicion, Visibility
from societysim.roles import Phase, Role

CFG = GameConfig(seed=3, models=["scripted"])  # p0 Ava, p1 Cleo, p2 Dev, ...
SECRET = "SECRET"
WOLFCHAT = "WOLFCHAT"
CANARY_P = 0.4242


def scripted(*lines):
    """A `read` that answers from a list and records every question."""
    asked, answers = [], list(lines)

    def read(question):
        asked.append(question)
        return answers.pop(0)

    read.asked = asked
    return read


def answer(question):
    """A `read` that always gives a legal answer: choice 1, nothing optional."""
    if "blank" in question:
        return ""
    if "[1-" in question:
        return "1"
    return "I have a feeling about this."


def turn(action="vote", targets=("p1", "p2", "abstain")):
    prompt = phase_prompt(action, "OBS", {"p1": "Cleo", "p2": "Dev"}, {}, CFG.rules)
    return Turn(action, prompt, ["p1", "p2"], list(targets))


def agent(read, out=None):
    out = [] if out is None else out
    return HumanAgent(GameState(CFG), "p0", read=read, write=out.append), out


def rated(result):
    """The seats a reply rated, as HumanSink will log them."""
    return result.parsed.private_reasoning.thoughts.split(":", 1)[1].split()


async def test_vote_is_a_numbered_choice_with_names():
    read = scripted("9", "p7", "1", "")
    human, out = agent(read)
    result = await human.act(turn())
    assert result.parsed.action.vote == "p1"
    assert result.usage.model == "human"
    assert len(read.asked) == 4
    assert read.asked[0] == "Your vote [1-3]: "
    shown = "\n".join(out)
    assert "  1. Cleo (p1)\n  2. Dev (p2)\n  3. abstain (vote for nobody)" in shown
    assert sum("invalid" in line for line in out) == 2
    # no JSON in what the human reads
    assert "JSON" not in shown and '"action"' not in shown and '"suspicion"' not in shown
    assert json.loads(result.raw) == {"action": {"vote": "p1"}, "suspicion": {}}


async def test_skipped_suspicion_rates_nobody():
    result = await agent(scripted("2", ""))[0].act(turn())
    assert result.parsed.action.vote == "p2"
    assert rated(result) == ["-"]


async def test_suspicion_is_three_buttons_per_player():
    # "4" is not a level: the suspicion question starts over
    human, out = agent(scripted("1", "y", "4", "yes", "1", "3"))
    result = await human.act(turn())
    reasoning_ = result.parsed.private_reasoning
    assert [(e.player, e.p) for e in reasoning_.suspicion] == [
        ("p1", LEVELS["suspicious"]),
        ("p2", LEVELS["trust"]),
    ]
    assert rated(result) == ["p1", "p2"]
    assert sum("invalid" in line for line in out) == 1


async def test_partial_suspicion_names_only_the_rated_seats():
    human, _ = agent(scripted("1", "y", "", "unsure"))
    result = await human.act(turn())
    assert rated(result) == ["p2"]
    assert json.loads(result.raw)["suspicion"] == {"p2": LEVELS["unsure"]}


async def test_statement_with_accusation_and_seer_claim():
    # text, accuse 1 (twice), claim 2 = seer, report on seat 1 (p1): 1 = wolf, night 1
    answers = ["Cleo is a wolf.", "1 1", "2", "1", "1", "1", ""]
    human, _ = agent(scripted(*answers))
    result = await human.act(turn("statement", ("p1", "p2")))
    act = result.parsed.action
    assert (act.text, act.accuse, act.claim_role) == ("Cleo is a wolf.", ["p1"], Role.SEER)
    assert act.seer_report.model_dump() == {"target": "p1", "result": "wolf", "night": 1}

    # empty text, a bad accuse, a bad role, a bad seer result, a bad night: each restarts
    human, out = agent(
        scripted(
            "  ",
            *["x", "5"],
            *["x", "", "9"],
            *["x", "", "seer", "1", "3"],
            *["x", "", "2", "1", "2", "first"],
            *answers,
        )
    )
    result = await human.act(turn("statement", ("p1", "p2")))
    assert result.parsed.action.seer_report.target == "p1"
    assert sum("invalid" in line for line in out) == 5


async def test_statement_without_claim():
    human, _ = agent(scripted("Hello.", "", "", ""))
    act = (await human.act(turn("statement", ("p1", "p2")))).parsed.action
    assert (act.accuse, act.claim_role, act.seer_report) == ([], None, None)


def test_choose_takes_a_number_or_the_option():
    assert choose(" 2 ", ["p1", "p2"]) == "p2"
    assert choose("p1", ["p1", "p2"]) == "p1"
    for bad in ("0", "3", "", "-1", "Cleo"):
        with pytest.raises(ValueError):
            choose(bad, ["p1", "p2"])


def test_plain_drops_only_the_json_format_lines():
    prompt = phase_prompt(
        "statement", 'Now: Day 1.\nCleo (p1): "hi"', {"p1": "Cleo"}, {"p1": "Cleo"}, CFG.rules
    )
    text = plain(prompt)
    assert 'Now: Day 1.\nCleo (p1): "hi"' in text
    assert "Players you can accuse: Cleo (p1)." in text
    assert "JSON" not in text and '"action"' not in text and '"suspicion"' not in text


async def test_orchestrator_rejection_comes_back_as_a_retry():
    """The existing validator still runs: a seer report for a night that hasn't happened
    is rejected by the orchestrator, and the human is asked again with the error."""
    out = []
    first = ["I am the Seer.", "", "2", "1", "1", "1", ""]
    read = scripted(*first, "Fine, I'm not.", "", "", "")
    game = orchestrator.Game(
        CFG,
        with_human(RandomAgent, "p0"),
        EventLogger("t", []),
    )
    game.agents["p0"] = HumanAgent(game.state, "p0", read=read, write=out.append)
    reply = await game._ask("p0", "statement", game._others("p0"))
    assert len(reply.failures) == 1
    assert "seer_report" in reply.failures[0][1]
    assert reply.response.action.text == "Fine, I'm not."
    retry = [block for block in out if "Your previous reply was rejected" in block]
    assert len(retry) == 1 and "JSON" not in retry[0]
    assert sum(block.startswith("You are playing Werewolf") for block in out) == 1


def event(actor, thoughts, suspicion=None):
    return Event(
        game_id="g",
        seq=0,
        t=0,
        day=1,
        phase=Phase.VOTE,
        type=EventType.VOTE,
        visibility=Visibility.PUBLIC,
        actor=actor,
        private_reasoning=thoughts,
        suspicion=suspicion,
    )


async def test_human_sink_logs_only_given_suspicion():
    class Sink:
        def __init__(self):
            self.events, self.closed = [], False

        async def write(self, event):
            self.events.append(event)

        async def close(self):
            self.closed = True

    def reply(given):
        r = reasoning(given, ["p1", "p2"])
        return r["thoughts"], [Suspicion(**s) for s in r["suspicion"]]

    sink = Sink()
    human_sink = HumanSink(sink, "p0")
    ai = event("p1", "AI thoughts", [Suspicion(player="p0", p=0.5)])
    for e in (
        event("p0", *reply({})),
        event("p0", *reply({"p2": 0.8})),
        event("p0", *reply({"p1": 0.2, "p2": 0.8})),
        ai,
    ):
        await human_sink.write(e)
    await human_sink.close()
    logged = [e.model_dump(mode="json") for e in sink.events]
    assert [e.get("suspicion") for e in logged] == [
        None,  # nobody rated: null, never a made-up 0.5
        [{"player": "p2", "p": 0.8}],
        [{"player": "p1", "p": 0.2}, {"player": "p2", "p": 0.8}],
        [{"player": "p0", "p": 0.5}],  # other seats are untouched
    ]
    assert [e["private_reasoning"] for e in logged] == [THOUGHTS] * 3 + ["AI thoughts"]
    assert all(e["meta"] == {} for e in logged)
    assert sink.closed


class Canary(RandomAgent):
    """RandomAgent with canaries in everything the human must not see."""

    async def act(self, turn):
        result = await super().act(turn)
        reply = json.loads(result.raw)
        reply["private_reasoning"]["thoughts"] = SECRET
        for entry in reply["private_reasoning"]["suspicion"]:
            entry["p"] = CANARY_P
        if "message" in reply["action"]:
            reply["action"]["message"] = WOLFCHAT
        parsed = type(result.parsed).model_validate(reply)
        return result.model_copy(update={"parsed": parsed, "raw": json.dumps(reply)})


@pytest.mark.parametrize("role", list(Role))
async def test_leakage_human_seat_sees_only_its_observations(role, monkeypatch):
    """Everything shown to the human is its system prompt and, per turn, the prompt built
    from observe() for its seat. No other player's private reasoning, suspicion or (for a
    non-wolf) wolf chat reaches the terminal."""
    seat = next(s for s, r in GameState(CFG).roles.items() if r is role)
    seen = []
    real_observe = orchestrator.observe

    def spy(state, events, who):
        obs = real_observe(state, events, who)
        if who == seat:
            seen.append(obs)
        return obs

    monkeypatch.setattr(orchestrator, "observe", spy)
    out = []
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr("builtins.print", out.append)
    game = orchestrator.Game(CFG, with_human(Canary, seat), EventLogger("leak", []))
    await game.run()

    system, *rest = out
    assert system.startswith(f"You are playing Werewolf. You are {game.state.names[seat]}")
    turns = [block for block in rest if block.startswith("\n=== your turn: ")]
    assert turns and len(turns) == len(seen)
    for block, obs in zip(turns, seen, strict=True):
        assert plain(obs) in block
    shown = "\n".join(out)
    assert "JSON" not in shown and '"suspicion"' not in shown
    assert SECRET not in shown
    assert str(CANARY_P) not in shown
    assert (WOLFCHAT in shown) is (role is Role.WOLF)
