"""Narrator templates, the phrase bank and per-model voices. TTS is a fake; no API calls."""

import json
import shutil
from pathlib import Path

import httpx
import pytest
import yaml

from societysim.analysis import narrate as nar
from societysim.analysis.labeler import read_log
from societysim.analysis.narrate import (
    MODEL_ID,
    NarrationIndex,
    bank_lines,
    build_bank,
    build_clips,
    load_voices,
    main,
    narrate,
    narrator_line,
)
from societysim.engine.state import NAMES
from societysim.logging.events import Event, EventMeta, Visibility

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "fixtures/sample_game.jsonl"
VOICES = load_voices()
CALLUM, HARRY = "N2lVS1w4EtoT3dr4eOWO", "SOYHLrjzK2X1ezoPC6cr"


def fake_client(calls: list[httpx.Request]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, content=b"ID3" + req.content[:8])

    return httpx.Client(transport=httpx.MockTransport(handler), headers={"xi-api-key": "k"})


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    loaded = []
    monkeypatch.setattr(nar, "load_dotenv", lambda: loaded.append(1))
    return loaded


@pytest.fixture
def log(tmp_path: Path) -> Path:
    return Path(shutil.copy(FIXTURE, tmp_path / "fixture-0001.jsonl"))


@pytest.fixture
def bank(tmp_path: Path) -> Path:
    return tmp_path / "bank"


def ev(type_, phase="day", day=1, target=None, visibility=Visibility.PUBLIC, **meta) -> Event:
    return Event(
        game_id="g", seq=0, t=0, day=day, phase=phase, type=type_, visibility=visibility,
        target=target, meta=EventMeta(**meta),
    )  # fmt: skip


def test_fixture_narrator_lines():
    clips = build_clips(read_log(FIXTURE), VOICES)
    assert {c["category"] for c in clips} == {"narrator"}  # nothing else unless selected
    got = {c["seq"]: (c["voice"], c["voice_id"], c["text"]) for c in clips}
    assert got == {
        1: ("narrator_night", HARRY, "Night 1. The village sleeps."),
        13: ("narrator_night", HARRY, "Nobody died last night."),
        14: ("narrator", CALLUM, "Day 1. The village wakes."),
        43: ("narrator", CALLUM, "Day 1. Time to vote."),
        54: ("narrator", CALLUM, "The vote is tied. Nobody is eliminated."),
        55: ("narrator_night", HARRY, "Night 2. The village sleeps."),
        67: ("narrator_night", HARRY, "The wolves struck. Ava, the Seer, was found dead."),
        68: ("narrator", CALLUM, "Day 2. The village wakes."),
        97: ("narrator", CALLUM, "Day 2. Time to vote."),
        104: ("narrator", CALLUM, "The village voted out Gia, a villager."),
        105: ("narrator_night", HARRY, "Night 3. The village sleeps."),
        116: ("narrator_night", HARRY, "The wolves struck. Dev, the Doctor, was found dead."),
        117: ("narrator", CALLUM, "The wolves win. They now match the village."),
    }


@pytest.mark.parametrize(
    ("event", "line"),
    [
        (ev("elimination", "vote", target="p1", role="wolf"), "The village voted out Dev, a wolf."),
        (ev("elimination", "vote", target="p1"), "The village voted out Dev."),
        (ev("death_reveal", "night", target="p1"), "The wolves struck. Dev was found dead."),
        (ev("no_elimination", reason="all_abstain"), "Everyone abstained. Nobody is eliminated."),
        (ev("game_end", winner="villagers"), "The village wins. Every wolf is dead."),
        (ev("game_end", winner="draw", reason="max_days"), "Time is up. The game is a draw."),
        (ev("phase_start", "night", day=6), "Night 6. The village sleeps."),
    ],
)  # fmt: skip
def test_templates(event, line):
    assert narrator_line(event, {"p1": "Dev"})[1] == line


def test_night_voice_rule():
    """Harry: night falls and the night's deaths. Callum: the rest, even a game end at night."""
    assert narrator_line(ev("death_reveal", "night", target="p1"), {})[0] == "narrator_night"
    assert narrator_line(ev("game_end", "night", winner="wolves"), {})[0] == "narrator"


def test_no_hidden_info():
    events = read_log(FIXTURE)
    names = {s: seat.name for s, seat in events[0].meta.seats.items()}
    # Only public events get a line: night kills, saves, seer checks, wolf chat and the role
    # list (game_start) stay silent.
    assert all(narrator_line(e, names) is None for e in events if e.visibility != "public")
    hidden = {"night_kill", "doctor_save", "seer_check", "wolf_chat", "wolf_proposal"}
    assert hidden <= {e.type for e in events if e.visibility != "public"}
    # A night death names only the victim, never the wolves who chose them.
    wolves = {names[s] for s, r in events[-1].meta.roles.items() if r == "wolf"}
    for e in events:
        if e.type == "death_reveal" and (line := narrator_line(e, names)):
            assert not [w for w in wolves if w in line[1]]
    # A role is said only when the event reveals it, and the game end names nobody.
    hidden_role = ev("death_reveal", "night", target="p0")  # reveal_role_on_death off
    assert narrator_line(hidden_role, {"p0": "Ava"})[1] == "The wolves struck. Ava was found dead."
    end = narrator_line(events[-1], names)[1]
    assert not [n for n in NAMES if n in end]
    # A public doctor_save would still say nothing: it has no template.
    assert narrator_line(ev("doctor_save", "night", target="p0", saved=True), {}) is None
    # Any non-public event is silent, even a type that has a template.
    secret = ev("elimination", target="p0", role="wolf", visibility=Visibility.NARRATOR)
    assert narrator_line(secret, {"p0": "Ava"}) is None


def test_categories_and_voices():
    events = read_log(FIXTURE)
    seats = events[0].meta.seats
    by_cat = {}
    for c in build_clips(events, VOICES, ("speech", "wolf_chat")):
        by_cat.setdefault(c["category"], []).append(c)
    assert set(by_cat) == {"narrator", "speech", "wolf_chat"}
    for c in by_cat["speech"] + by_cat["wolf_chat"]:
        model = seats[c["speaker"]].model
        assert c["voice"] == model
        assert c["voice_id"] == VOICES.models.get(model, VOICES.default_model_voice)
    speech = {c["seq"] for c in by_cat["speech"]}
    assert all(events[s].type == "statement" and events[s].visibility == "public" for s in speech)
    assert all(events[c["seq"]].type == "wolf_chat" for c in by_cat["wolf_chat"])
    wolves = {s for s, r in events[-1].meta.roles.items() if r == "wolf"}
    assert {c["speaker"] for c in by_cat["wolf_chat"]} <= wolves
    p0 = next(c for c in by_cat["speech"] if c["speaker"] == "p0")  # qwen3-235b -> Eric
    assert p0["voice_id"] == "cjVigY5qzO86Huf0OWal"
    p1 = next(c for c in by_cat["speech"] if c["speaker"] == "p1")  # not in voices.yaml
    assert p1["voice_id"] == VOICES.default_model_voice
    only_speech = build_clips(events, VOICES, ("speech",))
    assert {c["category"] for c in only_speech} == {"narrator", "speech"}
    # A statement that is not public is never voiced as speech.
    i = next(c["seq"] for c in by_cat["speech"])
    events[i] = events[i].model_copy(update={"visibility": Visibility.WOLVES})
    assert i not in {c["seq"] for c in build_clips(events, VOICES, ("speech",))}


def test_voices_yaml():
    models = {
        m["name"] for m in yaml.safe_load((ROOT / "configs/models.yaml").read_text())["models"]
    }
    assert set(VOICES.models) - {"human"} <= models
    assert (VOICES.narrator.day, VOICES.narrator.night) == (CALLUM, HARRY)
    players = {*VOICES.models.values(), VOICES.default_model_voice}
    assert not {CALLUM, HARRY} & players  # the narrators never voice a player
    assert len(set(VOICES.models.values())) == len(VOICES.models)  # one voice per model


def test_bank_covers_every_narrator_line_and_fits_the_free_tier():
    lines = bank_lines()
    assert sum(len(t) for _, t in lines) < 5_000  # free tier is 10k characters a month
    events = read_log(FIXTURE)
    names = {s: seat.name for s, seat in events[0].meta.seats.items()}
    assert {narrator_line(e, names) for e in events} - {None} <= lines


def test_bank_reuse_makes_no_calls(log: Path, bank: Path):
    calls: list[httpx.Request] = []
    todo = build_bank(bank, max_chars=10_000, yes=True, voices=VOICES, client=fake_client(calls))
    assert len(calls) == len(todo) == len(bank_lines())
    index = json.loads((bank / "index.json").read_text())
    assert len(index["clips"]) == len(calls)
    assert all((bank / c["file"]).is_file() for c in index["clips"])
    first = calls[0]
    assert first.url.params["output_format"] == "mp3_44100_64"
    assert json.loads(first.content)["model_id"] == MODEL_ID

    calls.clear()
    assert build_bank(bank, max_chars=0, yes=True, voices=VOICES, client=fake_client(calls)) == []
    out = narrate(log, max_chars=0, bank=bank, voices=VOICES, client=fake_client(calls))
    assert calls == []
    audio = log.parent / "fixture-0001.audio"
    for c in out["clips"]:
        assert (audio / c["file"]).read_bytes() == (bank / c["file"]).read_bytes()


def test_narrator_miss_is_added_to_the_bank(log: Path, bank: Path, tmp_path: Path):
    calls: list[httpx.Request] = []
    narrate(log, max_chars=10_000, bank=bank, voices=VOICES, client=fake_client(calls))
    assert len(calls) == len({c.content for c in calls}) > 0  # each line once
    other = tmp_path / "other"
    other.mkdir()
    copy = Path(shutil.copy(log, other / log.name))
    calls.clear()
    narrate(copy, max_chars=0, bank=bank, voices=VOICES, client=fake_client(calls))
    assert calls == []


def test_selected_clips_are_generated_and_cached(log: Path, bank: Path):
    build_bank(bank, max_chars=10_000, yes=True, voices=VOICES, client=fake_client([]))
    calls: list[httpx.Request] = []
    index = narrate(
        log, max_chars=100_000, select=("speech",), bank=bank, voices=VOICES,
        client=fake_client(calls),
    )  # fmt: skip
    speech = [c for c in index["clips"] if c["category"] == "speech"]
    assert len(calls) == len({c["file"] for c in speech}) > 0
    assert {c.url.path.rsplit("/", 1)[1] for c in calls} <= {c["voice_id"] for c in speech}
    assert list(bank.glob("*.mp3"))  # the bank holds narrator lines only, never speech
    assert not {c["file"] for c in speech} & {p.name for p in bank.glob("*.mp3")}
    on_disk = json.loads((log.parent / "fixture-0001.audio/index.json").read_text())
    assert on_disk == index
    NarrationIndex.model_validate(on_disk)
    assert on_disk["voices"]["narrator"] == CALLUM
    assert on_disk["voices"]["narrator_night"] == HARRY
    assert on_disk["voices"]["p0"] == "cjVigY5qzO86Huf0OWal"
    calls.clear()
    narrate(
        log, max_chars=0, select=("speech",), bank=bank, voices=VOICES, client=fake_client(calls)
    )
    assert calls == []


def test_highlight_clips(log: Path, bank: Path):
    hl = [{"game_id": "fixture-0001", "seq": [11, 13], "description": "Dev saved Ben."}]
    hl.append({"game_id": "other", "seq": [1, 2], "description": "Not this game."})
    (log.parent / "highlights.json").write_text(json.dumps(hl))
    index = narrate(log, max_chars=10_000, select=("highlight",), bank=bank, voices=VOICES,
                    client=fake_client([]))  # fmt: skip
    clips = index["clips"]
    h = [c for c in clips if c["category"] == "highlight"]
    assert [(c["seq"], c["text"], c["voice_id"]) for c in h] == [(13, "Dev saved Ben.", CALLUM)]
    assert clips[clips.index(h[0]) - 1]["seq"] == 13  # after its event's own narrator line


def test_max_chars_guard_blocks_before_any_call(log: Path, bank: Path):
    calls: list[httpx.Request] = []
    with pytest.raises(ValueError, match="exceeds --max-chars 100"):
        narrate(log, max_chars=100, bank=bank, voices=VOICES, client=fake_client(calls))
    with pytest.raises(ValueError, match="exceeds --max-chars 100"):
        build_bank(bank, max_chars=100, yes=True, voices=VOICES, client=fake_client(calls))
    assert calls == []
    assert not (log.parent / "fixture-0001.audio").exists()


def test_dry_runs_print_estimates_without_calls(log: Path, bank: Path, capsys, no_dotenv):
    main([str(log), "--dry-run", "--max-chars", "1", "--bank", str(bank)])
    out = capsys.readouterr().out
    assert "13 clips, 0 from the bank, 13 to generate" in out
    main(["--build-bank", "--bank", str(bank)])  # no --yes: list only
    chars = sum(len(t) for _, t in bank_lines())
    assert f"{len(bank_lines())} lines, {len(bank_lines())} to generate, {chars} characters" in (
        capsys.readouterr().out
    )
    assert not bank.exists() and not (log.parent / "fixture-0001.audio").exists()
    assert no_dotenv == []  # nothing to pay for, so .env is not read


def test_dry_run_counts_bank_hits(log: Path, bank: Path, capsys):
    build_bank(bank, max_chars=10_000, yes=True, voices=VOICES, client=fake_client([]))
    main([str(log), "--dry-run", "--bank", str(bank), "--select", "speech"])
    out = capsys.readouterr().out
    assert "fixture-0001: " in out and ", 13 from the bank, " in out
    assert ", 0 to generate" not in out


def test_missing_key_exits_after_loading_dotenv(log: Path, bank: Path, monkeypatch, no_dotenv):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ELEVENLABS_API_KEY"):
        main([str(log), "--max-chars", "100000", "--bank", str(bank)])
    assert no_dotenv == [1]


def test_bad_select_and_missing_log():
    with pytest.raises(SystemExit):
        main(["x.jsonl", "--select", "speech,votes"])
    with pytest.raises(SystemExit):
        main([])
