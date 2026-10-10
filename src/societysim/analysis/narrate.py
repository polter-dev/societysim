"""Offline ElevenLabs narration for a finished game: one mp3 per narrated event.

    uv run --env-file .env python -m societysim.analysis.narrate runs/x/<game_id>.jsonl \
        [--max-chars 10000] [--dry-run]

Writes `<game_id>.audio/<seq>.mp3` and `<game_id>.audio/index.json` next to the log. Only
public events are voiced (villager view): statements in the speaker's seat voice; phase
starts, deaths, eliminations, votes and the game end in the narrator voice. Private
reasoning, wolf chat, night actions and the game_start seat list (it holds roles) are never
voiced. Clips already on disk are skipped, so a rerun only pays for what is missing.

ElevenLabs bills per character. The run refuses to start if the characters still to
generate exceed --max-chars; --dry-run prints the estimate and makes no calls.
The key comes from ELEVENLABS_API_KEY in the environment.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from societysim.analysis.labeler import read_log
from societysim.logging.events import Event, EventType, Visibility

API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
MODEL_ID = "eleven_flash_v2_5"  # cheapest per character
OUTPUT_FORMAT = "mp3_44100_128"  # default format, available on the free tier
NARRATOR_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # George
# ElevenLabs default (premade) voices, one per seat p0..p8.
SEAT_VOICES = [
    "EXAVITQu4vr4xnSDxMaL",  # Sarah
    "CwhRBWXzGAHq8TQ4Fs17",  # Roger
    "FGY2WhTYpPnrIDTdsKH5",  # Laura
    "IKne3meq5aSn9XLyUdCD",  # Charlie
    "Xb7hH8MSUJpSbSDYk0k2",  # Alice
    "N2lVS1w4EtoT3dr4eOWO",  # Callum
    "XrExE9yKIg1WjnnlVkGX",  # Matilda
    "TX3LPaxmHKxFdv7VOQHJ",  # Liam
    "cgSgspJ2msm6clMCkdW9",  # Jessica
]
NARRATED = {
    EventType.PHASE_START,
    EventType.DEATH_REVEAL,
    EventType.ELIMINATION,
    EventType.NO_ELIMINATION,
    EventType.GAME_END,
}


def seat_voice(seat: str) -> str:
    return SEAT_VOICES[int(seat[1:])]


def build_clips(events: list[Event]) -> list[dict]:
    """The clips to voice, in log order: {seq, speaker, voice_id, text}. Public events only."""
    start = next((e for e in events if e.type == EventType.GAME_START), None)
    names = {s: seat.name for s, seat in (start.meta.seats or {}).items()} if start else {}
    clips = []
    for e in events:
        if e.visibility != Visibility.PUBLIC:
            continue
        if e.type == EventType.STATEMENT and e.actor and e.public_text:
            speaker, text = e.actor, e.public_text
        elif e.type == EventType.VOTE and e.actor:
            who = names.get(e.actor, e.actor)
            target = names.get(e.target, e.target) if e.target else None
            speaker, text = (
                "narrator",
                f"{who} votes for {target}." if target else f"{who} abstains.",
            )
        elif e.type in NARRATED and e.public_text:
            speaker, text = "narrator", e.public_text
        else:
            continue
        voice = NARRATOR_VOICE if speaker == "narrator" else seat_voice(speaker)
        clips.append({"seq": e.seq, "speaker": speaker, "voice_id": voice, "text": text})
    return clips


def narrate(
    log: Path, *, max_chars: int, dry_run: bool = False, client: httpx.Client | None = None
) -> dict:
    """Generate missing clips and write index.json. Returns the index.

    Raises ValueError (before any API call) if the characters to generate exceed max_chars.
    `client` must send the xi-api-key header; by default one is built from ELEVENLABS_API_KEY.
    """
    events = read_log(log)
    game_id = events[0].game_id
    out = log.parent / f"{game_id}.audio"
    clips = build_clips(events)
    for c in clips:
        c["file"] = f"{c['seq']}.mp3"
    todo = [c for c in clips if not (out / c["file"]).exists()]
    chars = sum(len(c["text"]) for c in todo)
    print(f"{game_id}: {len(clips)} clips, {len(todo)} to generate, {chars} characters")
    if dry_run:
        return {}
    if chars > max_chars:
        raise ValueError(f"{chars} characters to generate exceeds --max-chars {max_chars}")
    if todo and client is None:
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set")
        client = httpx.Client(headers={"xi-api-key": key}, timeout=60)
    out.mkdir(exist_ok=True)
    for c in todo:
        r = client.post(
            API_URL.format(voice_id=c["voice_id"]),
            params={"output_format": OUTPUT_FORMAT},
            json={"text": c["text"], "model_id": MODEL_ID},
        )
        r.raise_for_status()
        (out / c["file"]).write_bytes(r.content)
    index = {
        "game_id": game_id,
        "model_id": MODEL_ID,
        "output_format": OUTPUT_FORMAT,
        "voices": {"narrator": NARRATOR_VOICE}
        | {s: seat_voice(s) for s in sorted({c["speaker"] for c in clips} - {"narrator"})},
        "clips": clips,
    }
    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    return index


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Generate ElevenLabs narration for one game log.")
    p.add_argument("log", type=Path, help="<game_id>.jsonl")
    p.add_argument("--max-chars", type=int, default=10_000, help="refuse above this many chars")
    p.add_argument("--dry-run", action="store_true", help="print the estimate, call nothing")
    a = p.parse_args(argv)
    try:
        narrate(a.log, max_chars=a.max_chars, dry_run=a.dry_run)
    except (ValueError, RuntimeError) as err:
        sys.exit(str(err))


if __name__ == "__main__":
    main()
