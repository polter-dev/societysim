"""HumanAgent with scripted stdin: input handling, the orchestrator's validator, and leakage."""

import json
import re

import pytest

from societysim import orchestrator
from societysim.agents.base import Turn
from societysim.agents.human import DEFAULTED, THOUGHTS, HumanAgent, MarkDefaults, with_human
from societysim.agents.scripted import RandomAgent
from societysim.config import GameConfig
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventLogger, EventType, Visibility
from societysim.roles import Phase, Role

CFG = GameConfig(seed=3, models=["scripted"])
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
    """A `read` that always gives a legal answer: the first option, nothing optional."""
    if question.startswith(("statement", "message")):
        return "I have a feeling about this."
    if question.startswith(("vote", "target", "kill")):
        return re.search(r"\(([^)]*)\): $", question).group(1).split()[0]
    return ""  # accuse, claim, suspicion


def turn(action="vote", targets=("p1", "p2", "abstain")):
    return Turn(action, "OBS\nReply with the JSON object only.", ["p1", "p2"], list(targets))


def agent(read, out=None):
    out = [] if out is None else out
    return HumanAgent(GameState(CFG), "p0", read=read, write=out.append), out


async def test_vote_reprompts_until_legal_and_defaults_skipped_suspicion():
    read = scripted("p9", "p1", "")
    human, out = agent(read)
    result = await human.act(turn())
    assert result.parsed.action.vote == "p1"
    assert [(e.player, e.p) for e in result.parsed.private_reasoning.suspicion] == [
        ("p1", 0.5),
        ("p2", 0.5),
    ]
    assert result.parsed.private_reasoning.thoughts == DEFAULTED
    assert result.usage.model == "human"
    assert len(read.asked) == 3
    assert any("invalid" in line for line in out)
    assert not any("Reply with the JSON object" in line for line in out)
    assert json.loads(result.raw)["action"] == {"vote": "p1"}


async def test_suspicion_input_is_validated():
    read = scripted("p2", "p1=2", "p7=0.1", "p1=x", "p1=0.9 p2=0.1")
    human, out = agent(read)
    result = await human.act(turn())
    reasoning = result.parsed.private_reasoning
    assert [(e.player, e.p) for e in reasoning.suspicion] == [("p1", 0.9), ("p2", 0.1)]
    assert reasoning.thoughts == THOUGHTS
    assert sum("invalid" in line for line in out) == 3


async def test_partial_suspicion_is_marked_defaulted():
    human, _ = agent(scripted("p2", "p1=0.9"))
    result = await human.act(turn())
    assert result.parsed.private_reasoning.thoughts == DEFAULTED
    assert result.parsed.private_reasoning.suspicion[1].p == 0.5


async def test_statement_with_accusation_and_seer_claim():
    answers = ["Ava is a wolf.", "p1 p1", "seer", "p1 wolf 1", ""]
    human, _ = agent(scripted(*answers))
    result = await human.act(turn("statement", ("p1", "p2")))
    act = result.parsed.action
    assert (act.text, act.accuse, act.claim_role) == ("Ava is a wolf.", ["p1"], Role.SEER)
    assert act.seer_report.model_dump() == {"target": "p1", "result": "wolf", "night": 1}

    # bad accuse, then a bad role, then a bad seer result: each restarts the statement
    human, out = agent(
        scripted(
            *["x", "p5"],
            *["x", "", "sorcerer"],
            *["x", "", "seer", "p1 wolfish 1"],
            *answers,
        )
    )
    result = await human.act(turn("statement", ("p1", "p2")))
    assert result.parsed.action.seer_report.target == "p1"
    assert sum("invalid" in line for line in out) == 3


async def test_orchestrator_rejection_comes_back_as_a_retry():
    """The existing validator still runs: a seer report for a night that hasn't happened
    is rejected by the orchestrator, and the human is asked again with the error."""
    out = []
    read = scripted("I am the Seer.", "", "seer", "p1 wolf 1", "", "Fine, I'm not.", "", "", "")
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
    assert "Your previous reply was rejected" in out[-1]
    assert sum(block.startswith("You are playing Werewolf") for block in out) == 1


async def test_mark_defaults_sets_meta_only_for_defaulted_human_events():
    class Sink:
        def __init__(self):
            self.events, self.closed = [], False

        async def write(self, event):
            self.events.append(event)

        async def close(self):
            self.closed = True

    def event(actor, reasoning):
        return Event(
            game_id="g",
            seq=0,
            t=0,
            day=1,
            phase=Phase.VOTE,
            type=EventType.VOTE,
            visibility=Visibility.PUBLIC,
            actor=actor,
            private_reasoning=reasoning,
        )

    sink = Sink()
    marked = MarkDefaults(sink, "p0")
    for e in (event("p0", DEFAULTED), event("p0", THOUGHTS), event("p1", DEFAULTED)):
        await marked.write(e)
    await marked.close()
    metas = [e.model_dump(mode="json")["meta"] for e in sink.events]
    assert metas == [{"suspicion_default": True}, {}, {}]
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

    system, *turns = out
    assert system.startswith(f"You are playing Werewolf. You are {game.state.names[seat]}")
    assert turns and len(turns) == len(seen)
    for block, obs in zip(turns, seen, strict=True):
        assert block.startswith("\n=== your turn: ")
        assert obs in block
    shown = "\n".join(out)
    assert SECRET not in shown
    assert str(CANARY_P) not in shown
    assert (WOLFCHAT in shown) is (role is Role.WOLF)
