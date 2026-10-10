import json
from pathlib import Path

import litellm  # noqa: F401  (it loads .env on import; do that before tests set the env)
import pytest
from pydantic import ValidationError

from societysim.config import ModelSpec
from societysim.engine.state import GameState
from societysim.models.registry import load_registry
from societysim.roles import Role
from societysim.setup import (
    DEFAULT_LINEUP,
    HUMAN,
    GameSetup,
    build_catalog,
    setup_errors,
    setup_json_schema,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(ROOT / "configs/models.yaml")


def roles(setup: GameSetup) -> list[Role]:
    return list(GameState(setup.game_config()).roles.values())


def error(data: dict) -> str:
    with pytest.raises(ValidationError) as exc:
        GameSetup.model_validate(data)
    lines = setup_errors(exc.value)
    assert len(lines) == 1, lines
    return lines[0]


def adv(**kw) -> dict:
    return {"mode": "advanced", **kw}


def test_basic_setup_is_ten_seats_two_wolves_seer_doctor():
    setup = GameSetup.basic()
    cfg = setup.game_config()
    assert setup == GameSetup()  # every default is Basic
    assert cfg.n_players == 10
    assert (cfg.roles.wolves, cfg.roles.seers, cfg.roles.doctors, cfg.roles.villagers) == (
        2,
        1,
        1,
        6,
    )
    assert sorted(roles(setup)).count(Role.WOLF) == 2
    assert HUMAN not in cfg.models
    assert set(cfg.models) == set(DEFAULT_LINEUP)  # 10 seats cover all 7 lineup models
    assert set(DEFAULT_LINEUP) <= {m.name for m in REGISTRY.models}


def test_basic_setup_with_a_human_has_nine_models_and_two_wolves():
    setup = GameSetup.basic(human=True, seed=3)
    models = setup.game_config().models
    assert setup.human_seat == "p0"
    assert models[0] == HUMAN
    assert models.count(HUMAN) == 1 and len(models) == 10
    assert sorted(roles(setup)).count(Role.WOLF) == 2
    # the human can sit anywhere in basic mode
    assert GameSetup(human_seat="p9").game_config().models[9] == HUMAN


def test_resolution_is_deterministic_in_the_seed():
    def models(seed):
        return GameSetup(seed=seed).game_config().models

    assert models(5) == models(5)
    assert len({tuple(models(s)) for s in range(10)}) > 1  # shuffle depends on the seed
    assert GameSetup(seed=5).game_config() == GameSetup(seed=5).game_config()


def test_in_order_rotation_repeats_the_pool_around_the_human():
    setup = GameSetup.model_validate(
        adv(
            n_players=7,
            model_pool=["gemma4-26b", "qwen3-235b"],
            rotation="in_order",
            human_seat="p1",
        )
    )
    assert setup.game_config().models == [
        "gemma4-26b",
        HUMAN,
        "qwen3-235b",
        "gemma4-26b",
        "qwen3-235b",
        "gemma4-26b",
        "qwen3-235b",
    ]


def test_advanced_seats_roles_and_wolves_resolve():
    seats = [{"seat": f"p{i}", "model": "gemma4-26b"} for i in range(8)]
    seats[5]["model"] = HUMAN
    seats.reverse()  # order in the list doesn't matter
    setup = GameSetup.model_validate(
        adv(n_players=8, n_wolves=3, special_roles={"seer": True, "doctor": False}, seats=seats)
    )
    cfg = setup.game_config()
    assert setup.human_seat == "p5"
    assert cfg.models == ["gemma4-26b"] * 5 + [HUMAN] + ["gemma4-26b"] * 2
    assert (cfg.roles.wolves, cfg.roles.seers, cfg.roles.doctors, cfg.roles.villagers) == (
        3,
        1,
        0,
        4,
    )
    assert Role.DOCTOR not in roles(setup)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"n_wolves": 3}, "n_wolves is fixed in basic mode"),
        ({"model_pool": ["gemma4-26b"]}, "model_pool is fixed in basic mode"),
        (adv(n_players=6), "n_players: Input should be greater than or equal to 7"),
        (adv(n_players=11), "n_players: Input should be less than or equal to 10"),
        (adv(n_wolves=5), "5 wolves need more than 5 villagers; use at most 4 wolves"),
        (adv(n_players=7, n_wolves=4), "use at most 3 wolves for 7 players"),
        (adv(n_wolves=0), "n_wolves: Input should be greater than or equal to 1"),
        (adv(n_players=7, human_seat="p7"), "human_seat p7 is not one of p0..p6"),
        (adv(human_seat="p10"), "human_seat: String should match pattern"),
        (adv(model_pool=["nope"]), "unknown model 'nope' at model_pool; known: amd-gpt-oss-20b"),
        (adv(model_pool=[]), "model_pool: List should have at least 1 item"),
        (adv(model_pool=["gemma4-26b", HUMAN]), "model_pool can't hold 'human'; use human_seat"),
        (adv(rotation="random"), "rotation: Input should be 'in_order' or 'shuffle'"),
        (adv(mode="expert"), "mode: Input should be 'basic' or 'advanced'"),
        (adv(colour="red"), "colour: Extra inputs are not permitted"),
    ],
)
def test_validation_errors(data, message):
    assert message in error(data)


def seats(n: int, **override) -> list[dict]:
    out = {f"p{i}": "gemma4-26b" for i in range(n)} | override
    return [{"seat": s, "model": m} for s, m in out.items()]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (adv(seats=seats(10), model_pool=["gemma4-26b"]), "give seats or model_pool, not both"),
        (adv(n_players=7, seats=seats(6)), "seats must list p0..p6 once each, got p0, p1"),
        (adv(n_players=7, seats=seats(8)), "seats must list p0..p6 once each"),
        (adv(n_players=7, seats=seats(6) + seats(1)), "seats must list p0..p6 once each"),
        (adv(seats=seats(10, p3="nope")), "unknown model 'nope' at p3"),
        (adv(seats=seats(10, p3=HUMAN, p4=HUMAN)), "at most one human seat, got p3, p4"),
        (adv(seats=seats(10, p3=HUMAN), human_seat="p4"), "human_seat is p4 but seats puts"),
        (adv(seats=seats(10), human_seat="p4"), "human_seat p4: set that seat's model to 'human'"),
        (adv(seats=[{"seat": "x1", "model": "gemma4-26b"}]), "seats.0.seat: String should match"),
    ],
)
def test_seat_validation_errors(data, message):
    assert message in error(data)


def test_model_names_can_come_from_the_validation_context():
    data = adv(model_pool=["mine"])
    assert (
        GameSetup.model_validate(data, context={"models": {"mine"}}).game_config().models
        == ["mine"] * 10
    )


def test_game_setup_schema_file_is_current():
    on_disk = json.loads((ROOT / "schema/game_setup.schema.json").read_text())
    assert on_disk == setup_json_schema()
    assert on_disk["properties"]["n_players"] == {
        "default": 10,
        "maximum": 10,
        "minimum": 7,
        "title": "N Players",
        "type": "integer",
    }


def test_catalog_lists_every_registry_model(monkeypatch):
    monkeypatch.delenv("AMD_VLLM_BASE_URL", raising=False)
    cat = build_catalog(REGISTRY.models)
    by_name = {m.name: m for m in cat.models}
    assert list(by_name) == [m.name for m in REGISTRY.models]
    assert cat.default_lineup == DEFAULT_LINEUP and cat.human == HUMAN
    qwen, amd, gemini = by_name["qwen3-235b"], by_name["amd-gpt-oss-20b"], by_name["gemini-flash"]
    assert (qwen.display_name, qwen.provider, qwen.sponsor) == (
        "Qwen3-235B-A22B-Instruct-2507",
        "huggingface",
        "huggingface",
    )
    assert (qwen.price_tier, qwen.structured, qwen.structured_ok) == ("low", "json_object", True)
    assert qwen.available and qwen.unavailable_reason is None
    assert (amd.provider, amd.sponsor, amd.price_tier) == ("amd", "amd", "free")
    assert not amd.available
    assert "AMD_VLLM_BASE_URL" in amd.unavailable_reason
    assert (gemini.provider, gemini.sponsor, gemini.price_tier) == ("google", None, "high")
    assert by_name["gpt-5-mini"].provider == "openai"
    assert by_name["gpt-5-mini"].price_tier == "medium"


def test_catalog_availability_follows_env_and_server_check(monkeypatch):
    amd = next(m for m in REGISTRY.models if m.name == "amd-gpt-oss-20b")
    monkeypatch.setenv("AMD_VLLM_BASE_URL", "https://secret-tunnel.example/v1")
    assert build_catalog([amd]).models[0].available

    monkeypatch.setattr(
        "societysim.models.registry.check_served_model",
        lambda spec: "GET https://secret-tunnel.example/v1/models failed",
    )
    entry = build_catalog([amd], check=True).models[0]
    assert not entry.available
    assert "secret-tunnel" not in entry.model_dump_json()


def test_catalog_marks_unpriced_and_unstructured_models():
    spec = ModelSpec(name="x", litellm_model="openai/not-a-real-model", structured="none")
    entry = build_catalog([spec]).models[0]
    assert (entry.price_tier, entry.structured_ok, entry.provider) == ("unknown", False, "openai")


def test_data_contract_examples_are_valid_setups():
    from jsonschema import Draft202012Validator

    doc = (ROOT / "docs/DATA_CONTRACT.md").read_text(encoding="utf-8")
    section = doc.split("## Game setup\n", 1)[1].split("### Models catalog", 1)[0]
    examples = [json.loads(b.split("```", 1)[0]) for b in section.split("```json\n")[1:]]
    assert len(examples) == 3
    validator = Draft202012Validator(setup_json_schema())
    for example in examples:
        assert not list(validator.iter_errors(example))
        GameSetup.model_validate(example).game_config()
    assert "schema/game_setup.schema.json" in doc
    assert f"Contract version: {setup_json_schema()['x-contract-version']}\n" in doc
