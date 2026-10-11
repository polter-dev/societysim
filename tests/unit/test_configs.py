import re
from collections import Counter
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from societysim.batch.assign import assign_games
from societysim.batch.runner import pair_games, plan
from societysim.cli import app
from societysim.config import BatchConfig, GameConfig, RoleCounts, load_yaml
from societysim.models.registry import load_registry

CONFIGS = Path(__file__).resolve().parents[2] / "configs"
NAMES = [
    "smoke_batch",
    "selfplay_baseline",
    "selfplay_baseline_10p",
    "mixed_v1",
    "mixed_v2_10p",
    "mixed_v2_10p_gpt",
]
HF_PROVIDERS = {"huggingface", "hosted_vllm"}


def load(name: str) -> BatchConfig:
    return load_yaml(CONFIGS / f"{name}.yaml", BatchConfig)


@pytest.mark.parametrize("name", NAMES)
def test_loads_and_models_exist(name):
    cfg = load(name)
    text = (CONFIGS / f"{name}.yaml").read_text()
    assert cfg.batch_id == name
    assert "\nseed:" in text and "\nbudget_usd:" in text
    reg = load_registry(CONFIGS / "models.yaml")
    assert {s.name for s in reg.resolve(sorted(set(cfg.models)))} == set(cfg.models)


@pytest.mark.parametrize("name", NAMES)
def test_assignments(name):
    cfg = load(name)
    games = assign_games(cfg)
    assert len(games) == cfg.n_games
    assert len({g.game_id for g in games}) == cfg.n_games


def test_smoke_is_cheap_hf_only():
    cfg = load("smoke_batch")
    assert cfg.n_games == 10
    reg = load_registry(CONFIGS / "models.yaml")
    specs = reg.resolve(cfg.models)
    assert {s.provider for s in specs} <= HF_PROVIDERS
    assert max(s.output_cost_per_mtok for s in specs) <= 1.0


@pytest.mark.parametrize("name", ["selfplay_baseline", "selfplay_baseline_10p"])
def test_selfplay_ten_games_per_model(name):
    cfg = load(name)
    assert cfg.mode == "selfplay"
    games = assign_games(cfg)
    counts = Counter(g.models[0] for g in games)
    assert set(counts) == set(cfg.models)
    assert set(counts.values()) == {10}
    assert all(len(set(g.models)) == 1 for g in games)


def test_selfplay_10p_ten_players():
    cfg = load("selfplay_baseline_10p")
    assert cfg.game.n_players == 10 and cfg.n_games == 90 and cfg.budget_usd == 12.0


def test_mixed_v1_rotation_and_gpt_subset():
    cfg = load("mixed_v1")
    assert cfg.mode == "mixed"
    assert 90 <= cfg.n_games <= 110
    games = assign_games(cfg)
    appearances = Counter(m for g in games for m in set(g.models))
    gpt = [m for m in cfg.models if m.startswith("gpt-5")]
    assert gpt
    for m in gpt:
        assert 0 < appearances[m] <= cfg.model_game_limits[m] <= 10
    others = [m for m in cfg.models if m not in gpt]
    assert all(appearances[m] > 3 * max(appearances[g] for g in gpt) for m in others)


def test_mixed_v2_10p_ten_seats_nine_models():
    cfg = load("mixed_v2_10p")
    assert cfg.game.n_players == 10 and len(set(cfg.models)) == 9
    assert cfg.budget_usd <= 15
    assert all(len(g.models) == 10 for g in assign_games(cfg))


def wolf_listener_pairs(cfg: BatchConfig, salt: str | None = None) -> Counter[tuple[str, str]]:
    """Games per (wolf model, listener model): one count per game for each such pair."""
    return pair_games(plan(cfg, registry_specs(cfg), salt=salt))


def registry_specs(cfg: BatchConfig):
    return {s.name: s for s in load_registry(CONFIGS / "models.yaml").resolve(cfg.models)}


def test_mixed_v2_10p_gpt_pair_coverage():
    cfg = load("mixed_v2_10p_gpt")
    assert cfg.game.n_players == 10 and cfg.budget_usd <= 6
    gpt = [m for m in cfg.models if m.startswith("gpt-5")]
    assert len(gpt) == 2 and not cfg.model_game_limits
    pairs = wolf_listener_pairs(cfg)
    gpt_pairs = [(a, b) for a in cfg.models for b in cfg.models if a in gpt or b in gpt]
    assert len(gpt_pairs) == 28
    assert min(pairs[p] for p in gpt_pairs) >= 5, {p: pairs[p] for p in gpt_pairs if pairs[p] < 5}
    # The main batch leaves these pairs thin; this one exists to fix that.
    main = wolf_listener_pairs(load("mixed_v2_10p"))
    assert min(main[p] for p in gpt_pairs) < 5


def test_human_vs_ai_10p_config():
    cfg = load_yaml(CONFIGS / "human_vs_ai_10p.yaml", GameConfig)
    assert cfg.n_players == 10 and len(cfg.models) == 10
    assert len(set(cfg.models[1:])) == 9  # p1..p9 are nine different models; p0 is the human
    reg = load_registry(CONFIGS / "models.yaml")
    assert {s.name for s in reg.resolve(sorted(set(cfg.models)))} == set(cfg.models)


@pytest.mark.parametrize("name", NAMES)
def test_dry_run_estimate_under_budget(name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a stray runs/ would land here, not in the repo
    cfg = load(name)
    result = CliRunner().invoke(
        app,
        [
            "batch",
            "--dry-run",
            "--config",
            str(CONFIGS / f"{name}.yaml"),
            "--registry",
            str(CONFIGS / "models.yaml"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "runs").exists()
    assert cfg.max_concurrency == 8
    match = re.search(r"(\d+) games, estimated \$([\d.]+)", result.output)
    assert match, result.output
    assert int(match[1]) == cfg.n_games
    if name in ("selfplay_baseline", "mixed_v1", "mixed_v2_10p"):
        # The caps come from measured T5.1 costs; the price-based dry-run prior is ~4x too
        # conservative, so its estimate legitimately exceeds the cap here (mixed_v1: $20 vs $10).
        return
    assert float(match[2]) < cfg.budget_usd


# Read off each portrait in viewer/public/avatars/ (#400). gpt-5-mini and gpt-5-nano are
# androgynous children; amd-strix-gpt-oss-20b reuses the amd-gpt-oss-20b portrait.
AVATARS = {
    "qwen3-235b": "masculine",
    "llama4-maverick": "masculine",
    "gemma4-26b": "feminine",
    "gpt-oss-120b": "masculine",
    "deepseek-v3.1": "masculine",
    "gemini-flash-lite": "feminine",
    "gemini-flash": "feminine",
    "amd-gpt-oss-20b": "masculine",
    "amd-strix-gpt-oss-20b": "masculine",
    "gpt-5-nano": "neutral",
    "gpt-5-mini": "neutral",
}


def test_every_model_says_how_its_avatar_reads():
    raw = yaml.safe_load((CONFIGS / "models.yaml").read_text())["models"]
    assert all("avatar_presentation" in m for m in raw)  # explicit, never the default
    reg = load_registry(CONFIGS / "models.yaml")
    assert {m.name: m.avatar_presentation for m in reg.models} == AVATARS
    portraits = Path(__file__).resolve().parents[2] / "viewer/public/avatars"
    assert {p.stem for p in portraits.glob("*.webp")} - {"human"} <= set(AVATARS)


@pytest.mark.parametrize("wolves", [4, 7])
def test_wolves_must_be_fewer_than_non_wolves(wolves):
    with pytest.raises(ValueError, match="wolves need more than"):
        GameConfig(n_players=7, roles=RoleCounts(wolves=wolves, seers=0, doctors=0))


def test_max_wolves_still_loads():
    assert GameConfig(n_players=7, roles=RoleCounts(wolves=3)).roles.villagers == 2


def test_last_words_true_is_rejected():
    # #429: the engine has no last-words step, so the flag must not load as true.
    with pytest.raises(ValueError, match="last_words: true is not supported"):
        GameConfig.model_validate({"rules": {"last_words": True}})
    assert GameConfig.model_validate({"rules": {"last_words": False}}).rules.last_words is False


def test_dry_run_warns_when_salt_thins_gpt_pairs(tmp_path, monkeypatch):
    """#490: the unsalted plan has every GPT pair at 5+ games; a salt re-deals roles and can
    thin them. The dry run must count the plan it would run, salt included, and warn."""
    monkeypatch.chdir(tmp_path)
    cfg = load("mixed_v2_10p_gpt")
    gpt = [m for m in cfg.models if m.startswith("gpt-5")]
    gpt_pairs = [(a, b) for a in cfg.models for b in cfg.models if a in gpt or b in gpt]
    salt = next(
        s
        for s in (f"salt-{i}" for i in range(200))
        if min(wolf_listener_pairs(cfg, s)[p] for p in gpt_pairs) < 5
    )
    args = [
        "batch",
        "--dry-run",
        "--config",
        str(CONFIGS / "mixed_v2_10p_gpt.yaml"),
        "--registry",
        str(CONFIGS / "models.yaml"),
    ]
    env = {"PYTHON_DOTENV_DISABLED": "1"}
    clean = CliRunner().invoke(app, args, env={**env, "SOCIETYSIM_SEED_SALT": ""})
    assert clean.exit_code == 0, clean.output
    assert "games per (wolf model, listener model):" in clean.output
    salted = CliRunner().invoke(app, args, env={**env, "SOCIETYSIM_SEED_SALT": salt})
    assert salted.exit_code == 0, salted.output
    assert "games per (wolf model, listener model) (salted):" in salted.output
    assert "fewer than 5 games" in salted.output
    assert "SOCIETYSIM_SEED_SALT" in salted.output
    assert salt not in salted.output
    counts = wolf_listener_pairs(cfg, salt)
    thin = next(p for p in gpt_pairs if counts[p] < 5)
    assert f"{thin[0]}>{thin[1]}={counts[thin]}" in salted.output
