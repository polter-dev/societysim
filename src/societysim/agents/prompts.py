"""Prompt templates. Pure string building: no game state, no I/O.

The system prefix depends only on the seat, its role and the config, so it is byte-identical
on every call for that seat and provider prompt caching applies. Everything that changes
during the game reaches the model through `observation`, a string rendered by
`agents/observation.py` (the only path from state to prompt). Templates never look at state.

Players are shown as "Ava (p3)"; responses must use seat ids.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel

from societysim.agents.schemas import (
    ABSTAIN,
    StatementResponse,
    TargetResponse,
    VoteResponse,
    WolfChatResponse,
)
from societysim.config import ModelSpec, RoleCounts, RuleFlags
from societysim.roles import Role

Action = Literal["statement", "vote", "wolf_chat", "seer", "doctor"]

RESPONSE_MODELS: dict[Action, type[BaseModel]] = {
    "statement": StatementResponse,
    "vote": VoteResponse,
    "wolf_chat": WolfChatResponse,
    "seer": TargetResponse,
    "doctor": TargetResponse,
}

_ROLE_TEXT = {
    Role.WOLF: (
        "You are a Werewolf. You win with the other wolves. Your partners are named in your "
        "observation. Hide what you are: in public, act like a villager."
    ),
    Role.SEER: (
        "You are the Seer, on the villagers' side. Each night you check one player and learn "
        "whether they are a wolf. Your past results are in your observation."
    ),
    Role.DOCTOR: (
        "You are the Doctor, on the villagers' side. Each night you protect one player; if the "
        "wolves attack that player, they survive. Your past protects are in your observation."
    ),
    Role.VILLAGER: (
        "You are a Villager. You have no night action. Find the wolves through discussion "
        "and votes."
    ),
}


def player(seat: str, name: str) -> str:
    """How a player is shown in prompts: "Ava (p3)"."""
    return f"{name} ({seat})"


def _players(players: dict[str, str]) -> str:
    return ", ".join(player(seat, name) for seat, name in players.items())


def _rules(rules: RuleFlags, roles: RoleCounts) -> str:
    doctor = "The Doctor may " + ("" if rules.doctor_self_protect else "not ") + "protect themself"
    if rules.doctor_no_repeat:
        doctor += ", and may not protect the same player two nights in a row"
    lines = [
        f"- Roles: {roles.wolves} Werewolves, {roles.seers} Seer, {roles.doctors} Doctor, "
        f"{roles.villagers} Villagers.",
        "- The game starts at night, then alternates day and night.",
        f"- Night: the wolves talk privately for up to {rules.wolf_chat_rounds} rounds and kill "
        "one non-wolf. The Seer checks one player. The Doctor protects one player. "
        f"{doctor}.",
        "- Day: night deaths are announced"
        + (" and their roles revealed" if rules.reveal_role_on_death else "")
        + f". Then {rules.discussion_rounds} discussion rounds; each living player makes one "
        f"public statement of at most {rules.max_statement_words} words per round.",
        "- Vote: everyone votes at once"
        + (" or abstains" if rules.allow_abstain else "")
        + ". The player with the most votes is eliminated"
        + (" and their role revealed" if rules.reveal_role_on_death else "")
        + ". A tie, or no votes, eliminates no one.",
        "- Villagers win when no wolves are alive. Wolves win when living wolves are at least "
        f"as many as living non-wolves. If no one has won after the day {rules.max_days} vote, "
        "the game is a draw.",
    ]
    if not rules.last_words:
        lines.append("- Dead players say nothing more.")
    return "\n".join(lines)


def system_prompt(seat: str, name: str, role: Role, rules: RuleFlags, roles: RoleCounts) -> str:
    """Static prefix for one seat. Pass resolved role counts (`GameConfig.roles`)."""
    return f"""You are playing Werewolf. You are {player(seat, name)}.

Rules:
{_rules(rules, roles)}

Your role: {_ROLE_TEXT[role]}

How to reply:
- Players are shown as "Name (id)", for example "Ava (p3)". Always refer to players by id \
("p3") in your reply fields.
- Reply with one JSON object with two keys: "private_reasoning" and "action".
- "private_reasoning" is your private scratchpad. It is never shown to anyone: not to other \
players, not to the wolves. Be honest in it.
  - "thoughts": your reasoning.
  - "suspicion": one entry {{"player": id, "p": number}} for every other living player, \
exactly once. "p" is your honest probability (0 to 1) that the player is a wolf. Do not \
include yourself or dead players.
- "action" depends on the phase; each turn tells you its shape.
- Every field is required. Use null where a field does not apply."""


_TEMPLATES: dict[Action, str] = {
    "statement": """It is your turn to speak in the day discussion. Everyone will hear it.

"action" shape:
{{"text": string, "accuse": [id, ...], "claim_role": null | "wolf" | "seer" | "doctor" | \
"villager", "seer_report": null | {{"target": id, "result": "wolf" | "not_wolf", "night": int}}}}
- "text": your public statement, at most {max_words} words. Longer text is cut off.
- "accuse": ids of the players your statement accuses; [] if none.
- "claim_role": the role you claim in this statement, or null.
- "seer_report": if your statement reports a Seer check, the check you report; else null.
Players you can accuse: {targets}.""",
    "vote": """Vote now. All living players vote at the same time.

"action" shape: {{"vote": {vote_options}}}
Players you can vote for: {targets}.""",
    "wolf_chat": """Wolf channel: only the wolves see this message.

"action" shape: {{"message": string, "proposed_target": id}}
- "message": what you say to your fellow wolves.
- "proposed_target": the player you want to kill tonight.
Players you can target: {targets}.""",
    "seer": """Night: choose one player to check. You will learn whether they are a wolf.

"action" shape: {{"target": id}}
Players you can check: {targets}.""",
    "doctor": """Night: choose one player to protect from the wolves tonight.

"action" shape: {{"target": id}}
Players you can protect: {targets}.""",
}


def phase_prompt(
    action: Action,
    observation: str,
    others: dict[str, str],
    targets: dict[str, str],
    rules: RuleFlags,
) -> str:
    """User message for one call.

    observation: rendered by agents/observation.py, inserted verbatim.
    others: {seat: name} of every other living player (the suspicion list).
    targets: {seat: name} of the legal targets for this action.
    """
    vote_options = f'id or "{ABSTAIN}"' if rules.allow_abstain else "id"
    task = _TEMPLATES[action].format(
        max_words=rules.max_statement_words,
        targets=_players(targets),
        vote_options=vote_options,
    )
    return f"""{observation}

{task}

"suspicion" must have exactly one entry for each of: {_players(others)}.
Reply with the JSON object only."""


def schema_prompt(response_model: type[BaseModel]) -> str:
    """JSON schema text for models with `structured: none` (no response_format support)."""
    schema = json.dumps(response_model.model_json_schema(), separators=(",", ":"))
    return f"Your reply must be a JSON object matching this JSON schema:\n{schema}"


def build_messages(
    system: str, user: str, model: ModelSpec, response_model: type[BaseModel]
) -> list[dict[str, Any]]:
    """OpenAI-style messages. Embeds the schema in the user message when the model has no
    structured output, so the system prefix stays the same and cacheable."""
    if model.structured == "none":
        user = f"{user}\n\n{schema_prompt(response_model)}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
