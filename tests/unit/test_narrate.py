"""Narration clips and the offline generator. HTTP is mocked; no API calls."""

import json
import shutil
from pathlib import Path

import httpx
import pytest

from societysim.analysis.labeler import read_log
from societysim.analysis.narrate import (
    MODEL_ID,
    NARRATOR_VOICE,
    SEAT_VOICES,
    build_clips,
    main,
    narrate,
)
from societysim.logging.events import Visibility

FIXTURE = Path(__file__).parents[2] / "fixtures/sample_game.jsonl"


def fake_client(calls: list[httpx.Request]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, content=b"ID3" + req.content[:8])

    return httpx.Client(transport=httpx.MockTransport(handler), headers={"xi-api-key": "k"})


@pytest.fixture
def log(tmp_path: Path) -> Path:
    return Path(shutil.copy(FIXTURE, tmp_path / "fixture-0001.jsonl"))


def test_clips_cover_public_speech_and_narration():
    clips = {c["seq"]: c for c in build_clips(read_log(FIXTURE))}
    assert clips[1] == {
        "seq": 1,
        "speaker": "narrator",
        "voice_id": NARRATOR_VOICE,
        "text": "Night 1",
    }
    assert clips[17]["speaker"] == "p5" and clips[17]["voice_id"] == SEAT_VOICES[5]
    assert clips[17]["text"].startswith("I'll be direct: I'm the Seer.")
    assert clips[44]["text"] == "Ava votes for Cleo."
    assert clips[50]["text"] == "Dev abstains."
    assert clips[104]["text"].startswith("Gia (p6) was eliminated.")
    assert clips[117]["text"] == "The wolves win."
    # self-tags repeat a statement; failures and game_start are not voiced
    assert not {0, 18, 19, 20, 25, 49} & clips.keys()


def test_stable_voice_per_seat():
    clips = build_clips(read_log(FIXTURE))
    by_speaker: dict[str, set[str]] = {}
    for c in clips:
        by_speaker.setdefault(c["speaker"], set()).add(c["voice_id"])
    assert all(len(v) == 1 for v in by_speaker.values())
    assert len({v.pop() for v in by_speaker.values()}) == len(by_speaker)  # all distinct


def test_leakage_villager_text_has_no_private_or_role_info():
    events = read_log(FIXTURE)
    # plant a role reveal in the narrator-only game_start text: it must stay silent
    events[0] = events[0].model_copy(update={"public_text": "Cleo and Finn are the wolves."})
    voiced = " ".join(c["text"] for c in build_clips(events))
    secrets = [e.private_reasoning for e in events if e.private_reasoning]
    secrets += [
        e.public_text for e in events if e.visibility != Visibility.PUBLIC and e.public_text
    ]
    assert "Cleo and Finn are the wolves." in secrets
    long_secrets = [s for s in secrets if len(s) > 20]  # "Ava." also appears as a vote line
    assert not [s for s in long_secrets if s in voiced]
    assert not [c for c in build_clips(events) if c["text"] in secrets]
    seqs = {c["seq"] for c in build_clips(events)}
    assert all(events[s].visibility == Visibility.PUBLIC for s in seqs)


def test_narrate_writes_clips_and_index(log: Path):
    calls: list[httpx.Request] = []
    index = narrate(log, max_chars=100_000, client=fake_client(calls))
    out = log.parent / "fixture-0001.audio"
    assert len(calls) == len(index["clips"]) > 0
    first = calls[0]
    assert first.url.path == f"/v1/text-to-speech/{NARRATOR_VOICE}"
    assert first.url.params["output_format"] == "mp3_44100_128"
    assert first.headers["xi-api-key"] == "k"
    assert json.loads(first.content) == {"text": "Night 1", "model_id": MODEL_ID}
    for c in index["clips"]:
        assert (out / c["file"]).read_bytes().startswith(b"ID3")
    on_disk = json.loads((out / "index.json").read_text())
    assert on_disk == index
    assert on_disk["voices"]["narrator"] == NARRATOR_VOICE
    assert on_disk["voices"]["p5"] == SEAT_VOICES[5]


def test_rerun_only_generates_missing_clips(log: Path):
    narrate(log, max_chars=100_000, client=fake_client([]))
    (log.parent / "fixture-0001.audio" / "17.mp3").unlink()
    calls: list[httpx.Request] = []
    narrate(log, max_chars=100_000, client=fake_client(calls))
    assert [json.loads(c.content)["text"][:15] for c in calls] == ["I'll be direct:"]


def test_max_chars_guard_blocks_before_any_call(log: Path):
    calls: list[httpx.Request] = []
    with pytest.raises(ValueError, match="exceeds --max-chars 100"):
        narrate(log, max_chars=100, client=fake_client(calls))
    assert calls == []
    assert not (log.parent / "fixture-0001.audio").exists()


def test_dry_run_prints_estimate_without_calls(log: Path, capsys, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    expected = sum(len(c["text"]) for c in build_clips(read_log(log)))
    main([str(log), "--dry-run", "--max-chars", "1"])  # no key and a tiny budget: still fine
    assert f"{expected} characters" in capsys.readouterr().out
    assert not (log.parent / "fixture-0001.audio").exists()


def test_missing_key_exits(log: Path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ELEVENLABS_API_KEY"):
        main([str(log), "--max-chars", "100000"])
