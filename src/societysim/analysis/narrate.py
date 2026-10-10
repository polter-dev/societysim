"""Offline ElevenLabs narration for a finished game: a narrator line after each public event.

    uv run python -m societysim.analysis.narrate --build-bank [--yes]
    uv run python -m societysim.analysis.narrate runs/x/<game_id>.jsonl \
        [--select speech,wolf_chat,highlight] [--max-chars 10000] [--dry-run]

Narrator lines come from fixed templates (`narrator_line`), so every possible line is generated
once into a phrase bank, `narration/bank/<sha1(text+voice_id)>.mp3` plus `index.json`.
`--build-bank` lists those lines and prints a character estimate; `--yes` generates the
missing ones. After that, narrating a game copies clips from the bank and makes no API calls.
Two narrator voices: Callum (day voice) narrates day phases, eliminations and the result;
Harry (night voice) narrates night falling and the night's deaths.

`--select` adds clips voiced by each seat's model (configs/voices.yaml): `speech` (public
statements), `wolf_chat` (the wolf channel) and `highlight` (highlights.json descriptions,
narrator voice). These cost characters. wolf_chat and highlight carry hidden information:
the viewer plays them only in narrator mode.

Writes `<game_id>.audio/<sha1>.mp3` and `<game_id>.audio/index.json` next to the log. Clips
already on disk are skipped. The run refuses to start if the characters still to generate
exceed --max-chars; --dry-run prints the estimate and makes no calls. The key is
ELEVENLABS_API_KEY, read from the environment or `.env`.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated, Literal

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from societysim.analysis.labeler import read_log
from societysim.config import RuleFlags, load_yaml
from societysim.engine.state import NAMES
from societysim.logging.events import Event, EventMeta, EventType, Visibility
from societysim.roles import Phase, Role

ROOT = Path(__file__).resolve().parents[3]
VOICES = ROOT / "configs/voices.yaml"
BANK = ROOT / "narration/bank"
API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
MODEL_ID = "eleven_flash_v2_5"  # cheapest per character
OUTPUT_FORMAT = "mp3_44100_64"  # free tier; half the size of 128 kbps, so the bank fits in git
SILENT = "(silent)"  # orchestrator.SILENT: a statement nobody said
OPTIONAL = ("speech", "wolf_chat", "highlight")
DAY, NIGHT = "narrator", "narrator_night"  # `voice` of narrator clips
ROLE = {
    Role.WOLF: "a wolf",
    Role.SEER: "the Seer",
    Role.DOCTOR: "the Doctor",
    Role.VILLAGER: "a villager",
}

Speaker = Annotated[str, Field(pattern=r"^(narrator|p[0-8])$")]
VoiceKey = Annotated[str, Field(pattern=r"^(narrator|narrator_night|p[0-8])$")]
Category = Literal["narrator", "highlight", "speech", "wolf_chat"]


class NarratorVoices(BaseModel):
    day: str
    night: str


class Voices(BaseModel):
    """configs/voices.yaml."""

    narrator: NarratorVoices
    models: dict[str, str]
    default_model_voice: str

    def model(self, name: str | None) -> str:
        return self.models.get(name or "", self.default_model_voice)


class Clip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int  # the event the clip follows
    category: Category
    speaker: Speaker
    voice: str  # "narrator" (day), "narrator_night", or the speaker's model name
    voice_id: str
    text: str
    file: str  # relative to the .audio dir


class NarrationIndex(BaseModel):
    """<game_id>.audio/index.json (the contract, docs/DATA_CONTRACT.md). Only describes it:
    `narrate` builds the dict."""

    model_config = ConfigDict(extra="forbid")

    game_id: str
    model_id: str
    output_format: str
    voices: dict[VoiceKey, str]
    clips: list[Clip]


def load_voices(path: Path = VOICES) -> Voices:
    return load_yaml(path, Voices)


def clip_file(text: str, voice_id: str) -> str:
    return hashlib.sha1((text + voice_id).encode()).hexdigest() + ".mp3"


def narrator_line(e: Event, names: dict[str, str]) -> tuple[str, str] | None:
    """(voice, text) the narrator says after `e`, or None. Uses only what the public event
    shows: the target's name and, if the event reveals it, their role. Never who acted at
    night. doctor_save is never public, so it has no line."""
    if e.visibility != Visibility.PUBLIC:
        return None
    who = names.get(e.target, e.target) if e.target else None
    role = f", {ROLE[e.meta.role]}" if e.meta.role else ""
    winner = e.meta.winner
    match e.type:
        case EventType.PHASE_START if e.phase == Phase.NIGHT:
            return NIGHT, f"Night {e.day}. The village sleeps."
        case EventType.PHASE_START if e.phase == Phase.DAY:
            return DAY, f"Day {e.day}. The village wakes."
        case EventType.PHASE_START if e.phase == Phase.VOTE:
            return DAY, f"Day {e.day}. Time to vote."
        case EventType.DEATH_REVEAL if who:
            return NIGHT, f"The wolves struck. {who}{role and role + ','} was found dead."
        case EventType.DEATH_REVEAL:
            return NIGHT, "Nobody died last night."
        case EventType.ELIMINATION if who:
            return DAY, f"The village voted out {who}{role}."
        case EventType.NO_ELIMINATION if e.meta.reason == "tie":
            return DAY, "The vote is tied. Nobody is eliminated."
        case EventType.NO_ELIMINATION if e.meta.reason == "all_abstain":
            return DAY, "Everyone abstained. Nobody is eliminated."
        case EventType.NO_ELIMINATION:
            return DAY, "Nobody is eliminated."
        case EventType.GAME_END if winner == "villagers":
            return DAY, "The village wins. Every wolf is dead."
        case EventType.GAME_END if winner == "wolves":
            return DAY, "The wolves win. They now match the village."
        case EventType.GAME_END if winner == "draw":
            return DAY, "Time is up. The game is a draw."
    return None


def bank_lines(max_days: int = RuleFlags().max_days) -> set[tuple[str, str]]:
    """Every (voice, text) `narrator_line` can produce for up to `max_days` days, any seat
    name, with and without a revealed role. Wolves never die at night."""

    def line(type_, phase=Phase.DAY, day=1, name=None, **meta):
        e = Event(
            game_id="bank", seq=0, t=0, day=day, phase=phase, type=type_,
            visibility=Visibility.PUBLIC, target="p0" if name else None, meta=EventMeta(**meta),
        )  # fmt: skip
        return narrator_line(e, {"p0": name})

    lines = {line(EventType.DEATH_REVEAL, Phase.NIGHT)}
    lines |= {line(EventType.NO_ELIMINATION, reason=r) for r in ("tie", "all_abstain", None)}
    lines |= {line(EventType.GAME_END, winner=w) for w in ("villagers", "wolves", "draw")}
    for day in range(1, max_days + 1):
        lines |= {line(EventType.PHASE_START, p, day) for p in (Phase.NIGHT, Phase.DAY, Phase.VOTE)}
    for name in NAMES:
        for role in (None, *Role):
            lines.add(line(EventType.ELIMINATION, name=name, role=role))
            if role != Role.WOLF:
                lines.add(line(EventType.DEATH_REVEAL, Phase.NIGHT, name=name, role=role))
    return lines


def build_clips(
    events: list[Event], voices: Voices, select=(), highlights: list[dict] = ()
) -> list[dict]:
    """The clips to voice, in log order: {seq, category, speaker, voice, voice_id, text}.
    Narrator lines always; `select` adds speech, wolf_chat and highlight clips."""
    start = next((e for e in events if e.type == EventType.GAME_START), None)
    seats = (start.meta.seats or {}) if start else {}
    names = {s: seat.name for s, seat in seats.items()}
    narrator = {DAY: voices.narrator.day, NIGHT: voices.narrator.night}

    def clip(seq, category, speaker, voice, voice_id, text):
        keys = ("seq", "category", "speaker", "voice", "voice_id", "text")
        return dict(zip(keys, (seq, category, speaker, voice, voice_id, text), strict=True))

    clips = []
    for e in events:
        if line := narrator_line(e, names):
            clips.append(clip(e.seq, "narrator", "narrator", line[0], narrator[line[0]], line[1]))
            continue
        category = {EventType.STATEMENT: "speech", EventType.WOLF_CHAT: "wolf_chat"}.get(e.type)
        if category not in select or not e.actor or e.public_text in (None, "", SILENT):
            continue
        if category == "speech" and e.visibility != Visibility.PUBLIC:
            continue
        model = seats[e.actor].model if e.actor in seats else None
        clips.append(clip(e.seq, category, e.actor, model or e.actor, voices.model(model),
                          e.public_text))  # fmt: skip
    if "highlight" in select:
        for h in highlights:
            clips.append(clip(h["seq"][1], "highlight", "narrator", DAY, narrator[DAY],
                              h["description"]))  # fmt: skip
    clips.sort(key=lambda c: c["seq"])  # stable: a highlight follows its event's own clip
    for c in clips:
        c["file"] = clip_file(c["text"], c["voice_id"])
    return clips


def _client(client: httpx.Client | None) -> httpx.Client:
    if client is not None:
        return client
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    return httpx.Client(headers={"xi-api-key": key}, timeout=60)


def _tts(client: httpx.Client, voice_id: str, text: str) -> bytes:
    r = client.post(
        API_URL.format(voice_id=voice_id),
        params={"output_format": OUTPUT_FORMAT},
        json={"text": text, "model_id": MODEL_ID},
    )
    r.raise_for_status()
    return r.content


def _add_to_bank(bank: Path, clips: list[dict]) -> None:
    """Merge clips ({file, voice, voice_id, text}) into bank/index.json."""
    path = bank / "index.json"
    old = json.loads(path.read_text(encoding="utf-8"))["clips"] if path.exists() else []
    keep = ("file", "voice", "voice_id", "text")
    merged = {c["file"]: {k: c[k] for k in keep} for c in [*old, *clips]}
    index = {"model_id": MODEL_ID, "output_format": OUTPUT_FORMAT}
    index["clips"] = sorted(merged.values(), key=lambda c: c["file"])
    path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def _check_budget(chars: int, max_chars: int) -> None:
    if chars > max_chars:
        raise ValueError(f"{chars} characters to generate exceeds --max-chars {max_chars}")


def build_bank(
    bank: Path = BANK,
    *,
    max_chars: int,
    yes: bool = False,
    voices: Voices | None = None,
    client: httpx.Client | None = None,
) -> list[dict]:
    """List every narrator line and, with `yes`, generate the ones missing from the bank.
    Returns the lines still to generate (before generating them)."""
    voices = voices or load_voices()
    ids = {DAY: voices.narrator.day, NIGHT: voices.narrator.night}
    lines = [
        {"voice": v, "voice_id": ids[v], "text": t, "file": clip_file(t, ids[v])}
        for v, t in sorted(bank_lines())
    ]
    todo = [c for c in lines if not (bank / c["file"]).exists()]
    chars = sum(len(c["text"]) for c in todo)
    print(f"bank: {len(lines)} lines, {len(todo)} to generate, {chars} characters")
    if not yes:
        print("dry run: pass --yes to generate them")
        return todo
    _check_budget(chars, max_chars)
    bank.mkdir(parents=True, exist_ok=True)
    if todo:
        client = _client(client)
        for c in todo:
            (bank / c["file"]).write_bytes(_tts(client, c["voice_id"], c["text"]))
    _add_to_bank(bank, lines)
    return todo


def narrate(
    log: Path,
    *,
    max_chars: int,
    dry_run: bool = False,
    select=(),
    bank: Path = BANK,
    voices: Voices | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Copy bank clips, generate missing ones and write index.json. Returns the index.

    Raises ValueError (before any API call) if the characters to generate exceed max_chars.
    A narrator line missing from the bank is generated into the bank, so the next game reuses
    it. `client` must send the xi-api-key header; by default one is built from
    ELEVENLABS_API_KEY.
    """
    events = read_log(log)
    game_id = events[0].game_id
    out = log.parent / f"{game_id}.audio"
    voices = voices or load_voices()
    highlights = []
    if "highlight" in select and (log.parent / "highlights.json").exists():
        hl = json.loads((log.parent / "highlights.json").read_text(encoding="utf-8"))
        highlights = [h for h in hl if h["game_id"] == game_id]
    clips = build_clips(events, voices, select, highlights)
    missing = {c["file"]: c for c in clips if not (out / c["file"]).exists()}
    in_bank = {f for f, c in missing.items() if c["category"] == "narrator" and (bank / f).exists()}
    todo = [c for f, c in missing.items() if f not in in_bank]
    chars = sum(len(c["text"]) for c in todo)
    print(
        f"{game_id}: {len(clips)} clips, {len(in_bank)} from the bank, "
        f"{len(todo)} to generate, {chars} characters"
    )
    if dry_run:
        return {}
    _check_budget(chars, max_chars)
    if todo:
        client = _client(client)
    out.mkdir(exist_ok=True)
    for f in in_bank:
        shutil.copyfile(bank / f, out / f)
    for c in todo:
        (out / c["file"]).write_bytes(_tts(client, c["voice_id"], c["text"]))
    if new_lines := [c for c in todo if c["category"] == "narrator"]:
        bank.mkdir(parents=True, exist_ok=True)
        for c in new_lines:
            shutil.copyfile(out / c["file"], bank / c["file"])
        _add_to_bank(bank, new_lines)
    seat_voices = {c["speaker"]: c["voice_id"] for c in clips if c["speaker"] != "narrator"}
    index = {
        "game_id": game_id,
        "model_id": MODEL_ID,
        "output_format": OUTPUT_FORMAT,
        "voices": {DAY: voices.narrator.day, NIGHT: voices.narrator.night}
        | dict(sorted(seat_voices.items())),
        "clips": clips,
    }
    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


def _categories(value: str) -> tuple[str, ...]:
    picked = tuple(v.strip() for v in value.split(",") if v.strip())
    if bad := set(picked) - set(OPTIONAL):
        raise argparse.ArgumentTypeError(f"unknown categories {sorted(bad)}; pick from {OPTIONAL}")
    return picked


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="ElevenLabs narration: phrase bank and per-game.")
    p.add_argument("log", type=Path, nargs="?", help="<game_id>.jsonl")
    p.add_argument("--build-bank", action="store_true", help="list (or with --yes, generate) bank")
    p.add_argument("--yes", action="store_true", help="with --build-bank: generate the clips")
    p.add_argument("--select", type=_categories, default=(), help="speech,wolf_chat,highlight")
    p.add_argument("--bank", type=Path, default=BANK, help="phrase bank dir")
    p.add_argument("--max-chars", type=int, default=10_000, help="refuse above this many chars")
    p.add_argument("--dry-run", action="store_true", help="print the estimate, call nothing")
    a = p.parse_args(argv)
    if not a.build_bank and a.log is None:
        p.error("give a game log, or --build-bank")
    if not a.dry_run and (a.yes or not a.build_bank):
        load_dotenv()  # like the CLI; only when a call may be made
    try:
        if a.build_bank:
            build_bank(a.bank, max_chars=a.max_chars, yes=a.yes and not a.dry_run)
        else:
            narrate(a.log, max_chars=a.max_chars, dry_run=a.dry_run, select=a.select, bank=a.bank)
    except (ValueError, RuntimeError) as err:
        sys.exit(str(err))


if __name__ == "__main__":
    main()
