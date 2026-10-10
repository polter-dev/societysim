"""Jev (TypeSafe System One) classifies each day statement after the game.

For every `statement` event it asks:
- `accuse_<seat>` (noul) for every other seat: does the speaker accuse that player?
- `claim_role` (choice): which role, if any, the speaker claims.
- `seer_report` (noul), plus `seer_target` and `seer_result` (choice): does the speaker
  report a Seer check, on whom, with what result.

Raw API responses are cached at `<cache_dir>/<game_id>/<seq>.json`, so re-runs make no
calls. The labeler (T3.2) turns `JevResult`s into labels; this module only classifies.

    uv run python -m societysim.analysis.jev fixtures/sample_game.jsonl
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from societysim.logging.events import Event, EventType
from societysim.roles import Role, SeatId, SeerResult

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
DEFAULT_CACHE = Path(".cache/jev")
NO_CLAIM = "none"
RETRY_STATUS = {429, 529}


class JevResult(BaseModel):
    """Jev's reading of one statement. Probabilities are P(yes) in 0..1."""

    model_config = ConfigDict(extra="forbid")

    game_id: str
    seq: int  # seq of the statement event
    actor: SeatId
    accuse: dict[SeatId, float]  # every seat except the actor
    claim_role: Role | None  # Jev's top choice; None = no claim
    claim_role_confidence: float
    seer_report: float
    seer_target: SeatId  # only meaningful when seer_report is high
    seer_result: SeerResult  # same


def build_request(stmt: Event, names: dict[str, str]) -> dict[str, Any]:
    """The /v1/systemone body for one statement. `names` maps seat id -> display name."""
    others = {seat: name for seat, name in names.items() if seat != stmt.actor}
    questions: dict[str, Any] = {
        f"accuse_{seat}": {
            "type": "noul",
            "instructions": f"Does the `speaker` accuse {name} ({seat}) of being a werewolf, "
            "or push to vote them out, in the `statement`?",
        }
        for seat, name in others.items()
    }
    questions["claim_role"] = {
        "type": "choice",
        "instructions": "Which role does the `speaker` claim to have in the `statement`?",
        "criteria": {
            NO_CLAIM: "The speaker does not claim a role.",
            Role.SEER: "Claims to be the Seer.",
            Role.DOCTOR: "Claims to be the Doctor.",
            Role.VILLAGER: "Claims to be a plain Villager.",
            Role.WOLF: "Admits to being a werewolf.",
        },
    }
    questions["seer_report"] = {
        "type": "noul",
        "instructions": "Does the `speaker` report the result of a Seer check on a player "
        "in the `statement`?",
    }
    questions["seer_target"] = {
        "type": "choice",
        "instructions": "If the `speaker` reports a Seer check, which player was checked?",
        "criteria": {seat: name for seat, name in others.items()},
    }
    questions["seer_result"] = {
        "type": "choice",
        "instructions": "If the `speaker` reports a Seer check, what was the result?",
        "criteria": {"wolf": "The player is a werewolf.", "not_wolf": "The player is not."},
    }
    return {
        "model": MODEL,
        "state": {
            "speaker": f"{names[stmt.actor]} ({stmt.actor})",
            "statement": stmt.public_text or "",
            "players": [f"{name} ({seat})" for seat, name in names.items()],
        },
        "questions": questions,
    }


def parse_response(stmt: Event, data: dict[str, Any]) -> JevResult:
    answers = data["answers"]
    claim = answers["claim_role"]
    return JevResult(
        game_id=stmt.game_id,
        seq=stmt.seq,
        actor=stmt.actor,
        accuse={
            k.removeprefix("accuse_"): v["noul"]
            for k, v in answers.items()
            if k.startswith("accuse_")
        },
        claim_role=None if claim["choice"] == NO_CLAIM else claim["choice"],
        claim_role_confidence=claim["confidence"],
        seer_report=answers["seer_report"]["noul"],
        seer_target=answers["seer_target"]["choice"],
        seer_result=answers["seer_result"]["choice"],
    )


async def _post(client: httpx.AsyncClient, body: dict[str, Any], tries: int = 4) -> dict:
    for attempt in range(tries):
        resp = await client.post(API_URL, json=body)
        if resp.status_code not in RETRY_STATUS or attempt == tries - 1:
            resp.raise_for_status()
            return resp.json()
        await asyncio.sleep(2**attempt)
    raise AssertionError("unreachable")


async def classify_game(
    log_path: Path,
    cache_dir: Path = DEFAULT_CACHE,
    client: httpx.AsyncClient | None = None,
    concurrency: int = 8,
) -> list[JevResult]:
    """Classify every statement in a game log. Cached statements make no calls.

    `client` must send the bearer token; by default one is built from TYPESAFE_API_KEY.
    """
    events = [Event.model_validate_json(line) for line in log_path.read_text().splitlines()]
    start = next(e for e in events if e.type == EventType.GAME_START)
    names = {seat: s.name for seat, s in start.meta.seats.items()}
    statements = [e for e in events if e.type == EventType.STATEMENT]
    sem = asyncio.Semaphore(concurrency)
    key = os.environ.get("TYPESAFE_API_KEY", "")
    http = client or httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30)

    async def one(stmt: Event) -> JevResult:
        path = cache_dir / stmt.game_id / f"{stmt.seq}.json"
        if path.exists():
            return parse_response(stmt, json.loads(path.read_text()))
        if client is None and not key:
            raise RuntimeError(f"TYPESAFE_API_KEY is not set and {path} is not cached")
        async with sem:
            data = await _post(http, build_request(stmt, names))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return parse_response(stmt, data)

    try:
        return list(await asyncio.gather(*(one(s) for s in statements)))
    finally:
        if client is None:
            await http.aclose()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    for r in asyncio.run(classify_game(Path(sys.argv[1]))):
        print(r.model_dump_json())
