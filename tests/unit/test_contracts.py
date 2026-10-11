import json
from pathlib import Path

import pytest
from fakes import FakeClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from societysim.agents.schemas import (
    StatementResponse,
    SuspicionEntry,
    TargetResponse,
    VoteResponse,
    WolfChatResponse,
)
from societysim.config import BatchConfig, GameConfig, ModelSpec, RoleCounts, load_yaml
from societysim.logging.events import SCHEMA_VERSION, Event, EventType, Label, LabelType
from societysim.roles import Role, Team

ROOT = Path(__file__).parents[2]
SCHEMA = json.loads((ROOT / "schema/events.schema.json").read_text())
EVENT_LINES = (ROOT / "fixtures/sample_game.jsonl").read_text().splitlines()
LABEL_LINES = (ROOT / "fixtures/sample_game.labels.jsonl").read_text().splitlines()
RESPONSES = [StatementResponse, VoteResponse, WolfChatResponse, TargetResponse]


def test_fixture_events_validate_against_schema_and_model():
    validator = Draft202012Validator(SCHEMA)
    for i, line in enumerate(EVENT_LINES):
        validator.validate(json.loads(line))
        event = Event.model_validate_json(line)
        assert event.seq == i


def test_fixture_has_every_event_type_and_one_game():
    events = [Event.model_validate_json(line) for line in EVENT_LINES]
    assert {e.type for e in events} == set(EventType)
    assert len({e.game_id for e in events}) == 1
    assert events[0].type == EventType.GAME_START and events[-1].type == EventType.GAME_END
    assert len(events[0].meta.seats) == 7
    assert [e.t for e in events] == sorted(e.t for e in events)


def test_fixture_labels_point_at_the_labelled_actor():
    validator = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": "#/$defs/Label"})
    events = [Event.model_validate_json(line) for line in EVENT_LINES]
    labels = [Label.model_validate_json(line) for line in LABEL_LINES]
    for line in LABEL_LINES:
        validator.validate(json.loads(line))
    assert {lb.label for lb in labels} == set(LabelType)
    for lb in labels:
        assert events[lb.source_seq].actor == lb.actor


def test_event_rejects_names_and_unknown_fields():
    base = json.loads(EVENT_LINES[18])  # an accusation
    Event.model_validate(base)
    with pytest.raises(ValidationError):
        Event.model_validate({**base, "target": "Ava"})
    with pytest.raises(ValidationError):
        Event.model_validate({**base, "extra": 1})


def test_seat_ids_go_up_to_p9_and_new_lines_are_version_3():
    base = json.loads(EVENT_LINES[18])  # an accusation
    validator = Draft202012Validator(SCHEMA)
    for seat in ("p0", "p9"):
        validator.validate({**base, "actor": seat, "target": seat})
        assert Event.model_validate({**base, "actor": seat}).actor == seat
    for bad in ("p10", "p", "q1"):
        with pytest.raises(ValidationError):
            Event.model_validate({**base, "target": bad})
        assert not validator.is_valid({**base, "target": bad})
    assert Event.model_validate({**base, "schema_version": 2}).schema_version == 2  # still loads
    unversioned = {k: v for k, v in base.items() if k != "schema_version"}
    assert Event.model_validate(unversioned).schema_version == SCHEMA_VERSION == 3
    assert GameConfig(n_players=10).n_players == 10
    with pytest.raises(ValidationError):
        GameConfig(n_players=11)


def test_version_1_line_without_cached_still_loads():
    v1 = json.loads(next(x for x in EVENT_LINES if '"usage"' in x))
    v1["schema_version"] = 1
    del v1["meta"]["usage"]["cached"]
    event = Event.model_validate(v1)
    assert event.schema_version == 1 and event.meta.usage.cached is False
    validator = Draft202012Validator(SCHEMA)
    validator.validate(v1)
    v2 = json.loads(event.model_dump_json())
    v2["schema_version"] = 2
    v2["meta"]["usage"]["cached"] = True
    validator.validate(v2)


def test_event_meta_drops_none_keys():
    line = Event(
        game_id="g", seq=0, t=0, day=0, phase="setup", type="game_start", visibility="narrator"
    ).model_dump_json()
    assert json.loads(line)["meta"] == {}


def _objects(schema: dict):
    """Every object schema inside a JSON schema."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _objects(value)


@pytest.mark.parametrize("model", RESPONSES)
def test_response_schemas_are_closed_and_provider_friendly(model):
    schema = model.model_json_schema()
    text = json.dumps(schema)
    for banned in ('"pattern"', '"minimum"', '"maximum"', '"default"', '"propertyNames"'):
        assert banned not in text
    for obj in _objects(schema):
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])


def test_suspicion_probability_is_checked():
    SuspicionEntry(player="p1", p=0.0)
    with pytest.raises(ValidationError):
        SuspicionEntry(player="p1", p=1.5)


@pytest.mark.parametrize(("n", "villagers"), [(7, 3), (8, 4), (9, 5), (10, 6)])
def test_default_role_counts(n, villagers):
    roles = GameConfig(n_players=n).roles
    assert (roles.wolves, roles.seers, roles.doctors, roles.villagers) == (2, 1, 1, villagers)


def test_three_wolves_and_bad_counts():
    assert GameConfig(n_players=9, roles=RoleCounts(wolves=3)).roles.villagers == 4
    with pytest.raises(ValidationError):
        GameConfig(n_players=7, roles=RoleCounts(villagers=4))
    with pytest.raises(ValidationError):
        GameConfig(n_players=7, models=["a", "b"])


def test_default_rules():
    rules = GameConfig().rules
    assert rules.max_days == 6 and rules.wolf_chat_rounds == 2 and rules.discussion_rounds == 2
    assert rules.doctor_self_protect and rules.doctor_no_repeat and rules.reveal_role_on_death
    assert rules.allow_abstain and not rules.last_words


def test_default_game_is_ten_players():
    cfg = GameConfig()
    assert cfg.n_players == 10
    assert (cfg.roles.wolves, cfg.roles.seers, cfg.roles.doctors, cfg.roles.villagers) == (
        2,
        1,
        1,
        6,
    )
    assert BatchConfig(batch_id="b", n_games=1, models=["a"], budget_usd=1).game.n_players == 10


def test_load_yaml(tmp_path):
    path = tmp_path / "batch.yaml"
    path.write_text(
        "batch_id: smoke\nn_games: 10\nmodels: [qwen]\nbudget_usd: 2\n"
        "game: {n_players: 8, roles: {wolves: 3}, rules: {max_days: 4}}\n"
    )
    batch = load_yaml(path, BatchConfig)
    assert batch.game.roles.villagers == 3 and batch.game.rules.max_days == 4
    path.write_text("n_players: 7\nmodels: [qwen]\nmax_dayz: 3\n")
    with pytest.raises(ValidationError):
        load_yaml(path, GameConfig)
    assert GameConfig(n_players=7, models=["qwen"]).seat_models() == ["qwen"] * 7


def test_roles():
    assert Role.WOLF.team is Team.WOLVES and Role.SEER.team is Team.VILLAGERS


SPEC = ModelSpec(name="fake", litellm_model="openai/fake")


async def test_fake_client_scripted_then_random():
    scripted = {"private_reasoning": {"thoughts": "x", "suspicion": []}, "action": {"vote": "p1"}}
    client = FakeClient(script=[scripted, "not json"], players=["p1", "p2"], seed=1)
    first = await client.complete(SPEC, [], VoteResponse)
    assert first.parsed.action.vote == "p1" and first.error is None
    bad = await client.complete(SPEC, [], VoteResponse)
    assert bad.parsed is None and bad.error and bad.raw == "not json"
    for model in RESPONSES:
        result = await client.complete(SPEC, [], model)
        assert isinstance(result.parsed, model)
        assert {s.player for s in result.parsed.private_reasoning.suspicion} == {"p1", "p2"}
    assert len(client.calls) == 6 and client.calls[0][0] is SPEC
