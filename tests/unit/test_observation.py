"""What each role's observation contains. Leaks are tested in test_leakage.py."""

import copy
import re

from test_leakage import GAMES, WOLFCHAT, build, statement_text

from societysim.agents.observation import LAST_WORDS, MAX_CHARS, OMITTED, observe
from societysim.logging.events import Event, EventType, Visibility
from societysim.roles import Phase

GAME = GAMES[True]
CAST = GAME.cast


def obs(checkpoint: str, who: str) -> str:
    state, events = GAME.checkpoints[checkpoint]
    return observe(state, events, CAST[who])


def name(seat: str) -> str:
    return f"{GAME.final.names[seat]} ({seat})"


def test_now_line():
    assert obs("night1", "villager").startswith("Now: Night 1.\n")
    assert obs("day2", "villager").startswith("Now: Day 2, discussion.\n")
    assert obs("vote1", "villager").startswith("Now: Day 1, vote.\n")


def test_wolves_see_partners_and_the_channel():
    night1 = obs("night1", "wolf")
    assert f"Your fellow wolves: {name(CAST['wolf2'])}." in night1
    assert f"(wolf channel) {name(CAST['wolf2'])}: {WOLFCHAT}-1-2-{CAST['wolf2']}" in night1
    assert f"(wolf channel) {name(CAST['wolf'])} proposes killing {name(CAST['v_night1'])}." in (
        night1
    )
    assert f"(wolf channel) The pack attacks {name(CAST['v_night1'])}." in obs("day1", "wolf")
    assert f"(wolf channel) The pack attacked {name(CAST['v_night1'])}." in obs("night2", "wolf")


def test_dead_partner_is_marked():
    state, events = GAME.checkpoints["day2"]
    state = copy.deepcopy(state)
    state.alive.discard(CAST["wolf2"])
    assert f"Your fellow wolves: {name(CAST['wolf2'])}, dead." in observe(
        state, events, CAST["wolf"]
    )


def test_seer_sees_all_past_results():
    text = obs("day2", "seer")
    assert (
        f"Your Seer checks: night 1: {name(CAST['wolf'])} is a wolf; "
        f"night 2: {name(CAST['villager'])} is not a wolf." in text
    )


def test_doctor_sees_all_past_protects():
    assert "Your protects: none yet." in obs("night1", "doctor")
    assert (
        f"Your protects: night 1: {name(CAST['doctor'])}; night 2: {name(CAST['villager'])}."
        in obs("vote2", "doctor")
    )


def test_villager_gets_public_info_only():
    text = obs("vote2", "villager")
    for private in ("fellow wolves", "Seer checks", "protects", "wolf channel", WOLFCHAT):
        assert private not in text


def test_today_is_full_history():
    text = obs("day1", "villager")
    assert "[Night 1]" in text and "[Day 1]" in text
    assert f"{name(CAST['v_night1'])} was killed in the night (villager)." in text
    for seat in sorted(GAME.checkpoints["day1"][0].alive):
        assert f'{name(seat)}: "{statement_text(seat, 1, 1)}"' in text
    assert f"  {name(CAST['seer'])} accuses {name(CAST['wolf'])}." in text
    assert f"  {name(CAST['wolf2'])} claims to be seer." in text
    assert f"  {name(CAST['wolf2'])} reports {name(CAST['seer'])} as a wolf (night 1)." in text
    assert "Nobody died in the night." in obs("day2", "villager")


def test_earlier_days_are_summarized():
    text = obs("day2", "villager")
    summary = text.split("Day 1 summary:\n")[1].split("\n\n")[0]
    assert f"- {name(CAST['v_night1'])} was killed in the night (villager)." in summary
    seer_line = next(
        line for line in summary.splitlines() if line.startswith(f"- {name(CAST['seer'])}")
    )
    assert seer_line.count("accused") == 1  # tags repeated across rounds are listed once
    wolf2 = CAST["wolf2"]
    assert (
        f"- {name(wolf2)}: claimed to be seer; reported {name(CAST['seer'])} as a wolf (night 1);"
        in summary
    )
    # Last statement only, cut to 25 words.
    for seat in sorted(GAME.checkpoints["day1"][0].alive):
        quote = statement_text(seat, 1, 2).split()[:LAST_WORDS]
        assert f'last said: "{" ".join(quote)}..."' in summary
        assert f"START-{seat}-1-1" not in text and f"TAIL-{seat}-1-2" not in text
    assert "\n- Votes: " in summary
    assert f"{name(CAST['seer'])} -> {name(CAST['v_day1'])}," in summary
    assert f"{name(CAST['v_day1'])} -> abstain" in summary
    assert f"- {name(CAST['v_day1'])} was voted out (villager)." in summary


def test_players_render_as_name_and_seat_and_dead_are_listed():
    text = obs("vote2", "villager")
    assert f"Dead: {name(CAST['v_night1'])}, villager; {name(CAST['v_day1'])}, villager." in text
    unquoted = re.sub(r'"[^"]*"', "", text)  # statements quote canary markers like START-p0
    assert not re.search(r"(?<!\()\bp[0-8]\b(?!\))", unquoted)  # seats only as "Name (p3)"
    unrevealed = GAMES[False]
    state, events = unrevealed.checkpoints["vote2"]
    assert "role not revealed" in observe(state, events, unrevealed.cast["villager"])


def test_deterministic():
    again = build(True)
    for cp, (state, events) in GAME.checkpoints.items():
        state2, events2 = again.checkpoints[cp]
        for seat in sorted(state.alive):
            assert observe(state, events, seat) == observe(state2, events2, seat)


def _long_statement(state, seq: int, day: int, seat: str, text: str) -> Event:
    return Event(
        game_id="g",
        seq=seq,
        t=state.t,
        day=day,
        phase=Phase.DAY,
        type=EventType.STATEMENT,
        visibility=Visibility.PUBLIC,
        actor=seat,
        public_text=text,
    )


def test_budget_drops_oldest_history_first():
    state, events = GAME.checkpoints["vote2"]
    state = copy.deepcopy(state)
    state.day = 6
    events = list(events)
    word = "verbose" * 2
    for day in range(3, 7):
        for i in range(20):
            for seat in sorted(state.alive):
                text = f"D{day}-{i}-{seat} " + " ".join([word] * 79)
                events.append(_long_statement(state, len(events), day, seat, text))
    text = observe(state, events, CAST["seer"])
    assert len(text) <= MAX_CHARS
    assert OMITTED in text
    assert text.startswith("Now: ") and "Your Seer checks: night 1:" in text
    assert "Day 1 summary:" not in text
    assert f"D6-19-{max(state.alive)}" in text  # the latest statement survives


def test_typical_game_fits_without_cuts():
    state, events = GAME.checkpoints["vote2"]
    assert OMITTED not in observe(state, events, CAST["villager"])
