"""Command-line entry point. Subcommands are added by their tickets (play, batch, doctor, ...)."""

import asyncio
import os
import re
import secrets
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

# BatchConfig.batch_id's pattern: one folder name under runs/, never empty or a path.
BATCH_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")

app = typer.Typer(no_args_is_help=True, help="societysim: LLM Werewolf research harness")


def _echo(message: object = "", **kwargs) -> None:
    """typer.echo that never dies on text the console can't encode (Windows cp1252 pipes).

    Provider errors and model names can hold arrows, CJK text and the like. Unencodable
    characters turn into "?" instead of raising UnicodeEncodeError.
    """
    text = str(message)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    typer.echo(text.encode(enc, "replace").decode(enc), **kwargs)


class AgentMode(StrEnum):
    SCRIPTED = "scripted"
    LLM = "llm"


@app.callback()
def main() -> None:
    """Keeps typer in multi-command mode while only one subcommand exists."""


@app.command()
def version() -> None:
    """Print the package version."""
    from societysim import __version__

    _echo(__version__)


def _require_served(specs) -> None:
    """Exit before game 1 if a self-hosted server doesn't list the model id we would send."""
    from societysim.models.registry import check_served_models

    errors = [e for e in check_served_models(specs).values() if e]
    for e in errors:
        _echo(e)
    if errors:
        raise typer.Exit(1)


NeonFlag = Annotated[
    bool,
    typer.Option("--neon", help="Also stream events to Neon live (needs NEON_DATABASE_URL)."),
]


def _neon_connect(neon: bool):
    """With --neon, create the Neon tables and return the connect function for NeonSink.

    Any Neon problem (no NEON_DATABASE_URL, unreachable) is a warning: the run goes on without it.
    """
    if not neon:
        return None
    from societysim.logging.sinks import neon as neon_sink

    async def setup():
        conn = await neon_sink.connect_neon()
        try:
            await neon_sink.apply_schema(conn)
        finally:
            await conn.close()

    try:
        asyncio.run(setup())
    except Exception as exc:  # the message may hold the DSN, so print only the type
        _echo(f"warning: Neon unavailable ({type(exc).__name__}); not streaming live", err=True)
        return None
    return neon_sink.connect_neon


class Preset(StrEnum):
    BASIC = "basic"


@app.command()
def catalog(
    out: Annotated[Path, typer.Option("--out", help="Directory for models_catalog.json.")] = Path(
        "."
    ),
    registry: Annotated[Path, typer.Option("--registry", help="Model registry file.")] = Path(
        "configs/models.yaml"
    ),
    check: Annotated[
        bool, typer.Option("--check", help="Also ask each self-hosted server what it serves.")
    ] = False,
) -> None:
    """Write models_catalog.json: every model the settings screen can offer.

    A self-hosted model whose base URL variable (environment or .env) is unset is marked
    unavailable; --check also marks it unavailable when its server doesn't serve it.
    """
    from societysim.models.registry import load_registry
    from societysim.setup import build_catalog

    if not registry.is_file():
        _echo(f"config file not found: {registry}")
        raise typer.Exit(1)
    load_dotenv()
    cat = build_catalog(load_registry(registry).models, check=check)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "models_catalog.json"
    path.write_text(cat.model_dump_json(indent=2) + "\n", encoding="utf-8")
    n_off = sum(not m.available for m in cat.models)
    _echo(f"wrote {path} ({len(cat.models)} models, {n_off} unavailable)")


def _load_setup(path: Path | None, human_seat: str | None, seed: int | None):
    """GameSetup from --preset basic (path None) or a --setup file, with CLI overrides."""
    import yaml
    from pydantic import ValidationError

    from societysim.setup import GameSetup, setup_errors

    data = {"mode": "basic"}
    if path is not None:
        if not path.is_file():
            _echo(f"setup file not found: {path}")
            raise typer.Exit(1)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}  # YAML reads JSON too
        if not isinstance(data, dict):
            _echo(f"setup file must hold an object: {path}")
            raise typer.Exit(1)
    if human_seat is not None:
        data["human_seat"] = human_seat
    if seed is not None:
        data["seed"] = seed
    try:
        return GameSetup.model_validate(data)
    except ValidationError as exc:
        for line in setup_errors(exc):
            _echo(f"invalid setup: {line}")
        raise typer.Exit(1) from None


@app.command()
def play(
    config: Annotated[Path | None, typer.Option("--config", help="Game config YAML file.")] = None,
    preset: Annotated[
        Preset | None, typer.Option("--preset", help="Play a preset: basic = 10 seats, 2 wolves.")
    ] = None,
    setup: Annotated[
        Path | None,
        typer.Option("--setup", help="GameSetup JSON or YAML (schema/game_setup.schema.json)."),
    ] = None,
    agents: Annotated[AgentMode, typer.Option("--agents", help="Agent type.")] = AgentMode.SCRIPTED,
    seed: Annotated[int | None, typer.Option("--seed", help="Override the config seed.")] = None,
    batch_id: Annotated[
        str | None, typer.Option("--batch-id", help="Output directory name; default: config stem.")
    ] = None,
    neon: NeonFlag = False,
    human_seat: Annotated[
        str | None,
        typer.Option("--human-seat", help="Play this seat yourself, e.g. p3. Logged as 'human'."),
    ] = None,
) -> None:
    """Play one game and write its event log and run metadata."""
    from societysim.agents import human
    from societysim.agents.llm_agent import llm_agents
    from societysim.agents.scripted import RandomAgent
    from societysim.config import GameConfig, load_yaml
    from societysim.logging.sinks.jsonl import JsonlSink
    from societysim.logging.sinks.neon import NeonSink
    from societysim.models.registry import load_registry
    from societysim.orchestrator import run_game

    if sum(x is not None for x in (config, preset, setup)) != 1:
        _echo("give exactly one of --config, --preset or --setup")
        raise typer.Exit(1)
    if config is None:
        game_setup = _load_setup(setup, human_seat, seed)
        cfg, human_seat = game_setup.game_config(), game_setup.human_seat
        stem = setup.stem if setup is not None else preset.value
    elif not config.is_file():
        _echo(f"config file not found: {config}")
        raise typer.Exit(1)
    else:
        cfg, stem = load_yaml(config, GameConfig), config.stem
        if seed is not None:
            cfg = cfg.model_copy(update={"seed": seed})
        elif "seed" not in cfg.model_fields_set:
            cfg = cfg.model_copy(update={"seed": secrets.randbits(53)})
    if batch_id is not None and not BATCH_ID.fullmatch(batch_id):
        _echo(f"invalid --batch-id {batch_id!r}: use letters, digits, '.', '_' or '-'")
        raise typer.Exit(1)
    batch_id = batch_id if batch_id is not None else stem
    game_id = f"{batch_id}-{secrets.token_hex(8)}"
    if config is not None and human_seat is not None:
        models = cfg.seat_models()
        seats = [f"p{i}" for i in range(cfg.n_players)]
        if human_seat not in seats or not models:
            _echo(f"--human-seat must be one of {', '.join(seats)} with models in the config")
            raise typer.Exit(1)
        models[seats.index(human_seat)] = human.MODEL
        cfg = cfg.model_copy(update={"models": models})

    load_dotenv()
    registry_path = Path(__file__).resolve().parents[2] / "configs/models.yaml"
    reg = load_registry(registry_path)
    reg.apply_env()
    try:
        specs = reg.resolve([m for m in cfg.seat_models() if m != human.MODEL])
    except KeyError as exc:
        _echo(exc.args[0])
        raise typer.Exit(1) from None

    if agents is AgentMode.SCRIPTED:
        make_agent = RandomAgent
    else:
        provider_keys = {
            "huggingface": ("HF_TOKEN", "HUGGINGFACE_API_KEY"),
            "hosted_vllm": ("HOSTED_VLLM_API_KEY",),
            "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            "openai": ("OPENAI_API_KEY",),
        }
        missing = []
        for spec in specs:
            keys = (spec.api_key_env,) if spec.api_key_env else provider_keys.get(spec.provider, ())
            if keys and not any(os.environ.get(key) for key in keys):
                missing.append(" or ".join(keys))
            try:
                spec.resolved_api_base()
            except ValueError as exc:
                missing.append(str(exc))
        missing = list(dict.fromkeys(missing))
        if missing:
            _echo(f"missing provider key: {', '.join(missing)}")
            raise typer.Exit(1)
        _require_served(specs)

        from societysim.models.client import LiteLLMClient

        client = LiteLLMClient(limits=reg.limits())
        make_agent = llm_agents(client, {s.name: s for s in specs})

    sinks = [JsonlSink(batch_id)]
    if connect := _neon_connect(neon):
        sinks.append(NeonSink(batch_id, connect=connect, agents=agents.value))
    if human_seat is not None:
        make_agent = human.with_human(make_agent, human_seat)
        sinks = [human.HumanSink(sink, human_seat) for sink in sinks]
    try:
        game = asyncio.run(
            run_game(
                cfg,
                make_agent,
                sinks,
                game_id,
                colors={s.name: s.color for s in specs},
                presentations={s.name: s.avatar_presentation for s in specs},
            )
        )
    except (EOFError, KeyboardInterrupt):
        _echo(f"\ngame abandoned; partial log: {Path('runs') / batch_id / f'{game_id}.jsonl'}")
        raise typer.Exit(130) from None
    from societysim.batch.runner import _tag_agents

    _tag_agents(Path("runs") / batch_id / f"{game_id}.run_meta.json", agents.value)
    _echo(Path("runs") / batch_id / f"{game_id}.jsonl")
    _echo(f"winner: {game.winner}")


def _hm(seconds: float, *, eta: bool = False) -> str:
    m = round(seconds / 60) if eta else int(seconds // 60)
    if eta:
        return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"
    return f"{m}m{int(seconds % 60):02d}s"


def _progress_line(e: dict) -> str:
    money = f"spent ${e['spent']:.2f} of ${e['budget']:.2f}"
    if e["event"] == "stopped":
        why = {"provider_down": "provider down", "invalid_output": "invalid output"}.get(
            e["reason"], "budget"
        )
        return f"{why}: no new games ({money}); waiting for {e['running']} running"
    if e["event"] == "failed":
        end = f"failed: {str(e['reason'])[:80]}"
    else:
        end = f"finished ({e['winner'] or 'no winner'})"
    eta = (
        "ETA estimating…" if e["eta_seconds"] is None else f"ETA ~{_hm(e['eta_seconds'], eta=True)}"
    )
    return (
        f"[{e['done']}/{e['total']}] {e['game_id']} {end} in {_hm(e['seconds'])} | {money} "
        f"| {e['running']} running | {eta}"
    )


@app.command()
def batch(
    config: Annotated[Path, typer.Option("--config", help="Batch config YAML file.")],
    agents: Annotated[AgentMode, typer.Option("--agents", help="Agent type.")] = AgentMode.SCRIPTED,
    registry: Annotated[Path, typer.Option("--registry", help="Model registry file.")] = Path(
        "configs/models.yaml"
    ),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the game plan and cost estimate; run nothing.")
    ] = False,
    neon: NeonFlag = False,
) -> None:
    """Play a batch of games in parallel into runs/<batch_id>/. Re-run to resume.

    Stops starting games when the budget would be exceeded; Ctrl-C stops cleanly.
    """
    from societysim.batch.runner import BatchMismatch, plan, run_batch
    from societysim.config import BatchConfig, load_yaml
    from societysim.models.registry import load_registry

    for path in (config, registry):
        if not path.is_file():
            _echo(f"config file not found: {path}")
            raise typer.Exit(1)
    cfg = load_yaml(config, BatchConfig)
    load_dotenv()
    salt = os.environ.get("SOCIETYSIM_SEED_SALT") or None
    reg = load_registry(registry)
    reg.apply_env()
    try:
        specs = {s.name: s for s in reg.resolve(sorted(set(cfg.models)))}
    except KeyError as exc:
        _echo(exc.args[0])
        raise typer.Exit(1) from None

    if dry_run:
        from societysim.batch.runner import MIN_PAIR_GAMES, pair_games, prior_seat_cost

        for name, spec in specs.items():
            if prior_seat_cost(spec) == 0:
                _echo(f"warning: {name} is unpriced; the budget guard can't see its cost")
        rows = plan(cfg, specs, salt=salt)
        for row in rows:
            seats = " ".join(f"{seat}={model}/{role}" for seat, model, role in row["seats"])
            _echo(f"{row['game_id']} seed={row['seed']} ${row['est_usd']:.4f} {seats}")
        total = sum(row["est_usd"] for row in rows)
        _echo(
            f"{len(rows)} games, estimated ${total:.2f} (price-based guess) "
            f"against a ${cfg.budget_usd:.2f} budget"
        )
        # Roles depend on the salt, so this counts the plan that would actually run.
        pairs = pair_games(rows)
        models = sorted(set(cfg.models))
        _echo(f"games per (wolf model, listener model){' (salted)' if salt else ''}:")
        for w in models:
            _echo(f"  wolf {w}: " + " ".join(f"{o}={pairs[w, o]}" for o in models))
        thin = [(w, o) for w in models for o in models if pairs[w, o] < MIN_PAIR_GAMES]
        if thin:
            shown = ", ".join(f"{w}>{o}={pairs[w, o]}" for w, o in thin[:12])
            more = f" (+{len(thin) - 12} more)" if len(thin) > 12 else ""
            _echo(
                f"warning: {len(thin)} of {len(models) ** 2} (wolf, listener) pairs have fewer "
                f"than {MIN_PAIR_GAMES} games: {shown}{more}"
            )
            if salt:
                _echo(
                    "the salt changes the deal; if this config promises pair coverage, "
                    "set a different SOCIETYSIM_SEED_SALT and dry-run again before starting"
                )
        return

    client = None
    if agents is AgentMode.LLM:
        provider_keys = {
            "huggingface": ("HF_TOKEN", "HUGGINGFACE_API_KEY"),
            "hosted_vllm": ("HOSTED_VLLM_API_KEY",),
            "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            "openai": ("OPENAI_API_KEY",),
        }
        # Self-hosted entries carry their own key (checked by _require_served below).
        providers = {spec.provider for spec in specs.values() if not spec.api_key_env}
        missing = [
            " or ".join(keys)
            for provider, keys in provider_keys.items()
            if provider in providers and not any(os.environ.get(key) for key in keys)
        ]
        if missing:
            _echo(f"missing provider key: {', '.join(missing)}")
            raise typer.Exit(1)
        _require_served(specs.values())

        from societysim.models.client import LiteLLMClient

        client = LiteLLMClient(limits=reg.limits())

    try:
        result = asyncio.run(
            run_batch(
                cfg,
                specs,
                client,
                salt=salt,
                provider_concurrency={p: c.concurrency for p, c in reg.providers.items()},
                neon_connect=_neon_connect(neon),
                on_progress=lambda e: _echo(_progress_line(e)),
            )
        )
    except KeyboardInterrupt:
        _echo(f"interrupted; re-run to resume: runs/{cfg.batch_id}")
        raise typer.Exit(130) from None
    except BatchMismatch as exc:
        _echo(str(exc))
        raise typer.Exit(1) from None
    _echo(Path("runs") / cfg.batch_id)
    _echo(
        f"{result['status']} ({agents.value}): {len(result['finished'])} played, "
        f"{len(result['skipped'])} skipped, "
        f"{len(result['not_started'])} not started; "
        f"spent ${result['spent_usd']:.4f} of ${cfg.budget_usd:.2f}"
    )
    if lost := result["write_errors"]:
        _echo(
            f"write errors: {sum(lost.values())} events missing from {len(lost)} game logs "
            f"({', '.join(lost)}); see run_meta.json"
        )
    if result["failed"]:
        _echo(f"failed games: {', '.join(result['failed'])}")
        for msg in result["provider_down"]:
            _echo(f"provider down: {msg}")
        if result["provider_down"]:
            out = Path("runs") / cfg.batch_id
            _echo(f"no new games started; re-run once it is back to resume: {out}")
        raise typer.Exit(1)
    if result["status"] == "stopped_invalid":
        _echo(f"stopped for invalid output: {result['invalid_stop']}")
        _echo(
            f"to continue, raise max_invalid_rate (now {cfg.max_invalid_rate:.1%}) in the "
            f"batch config and re-run to resume: {Path('runs') / cfg.batch_id}"
        )
        raise typer.Exit(2)


@app.command()
def doctor(
    model: Annotated[
        str, typer.Option("--model", help="Glob of model names from models.yaml.")
    ] = "*",
    config: Annotated[Path, typer.Option("--config", help="Model registry file.")] = Path(
        "configs/models.yaml"
    ),
) -> None:
    """Ping each model with a tiny structured call in every structured mode.

    Prints which modes work with latency and cost. Exits 1 if any model has no working mode
    or if its configured `structured` mode fails.
    """
    from societysim.models.client import LiteLLMClient, estimate_cost
    from societysim.models.registry import (
        check_served_models,
        load_registry,
        probe_model,
        served_key,
    )

    load_dotenv()
    if not config.is_file():
        _echo(f"config file not found: {config}")
        raise typer.Exit(1)
    reg = load_registry(config)
    reg.apply_env()
    specs = reg.match(model)
    if not specs:
        _echo(f"no model matches {model!r}")
        raise typer.Exit(1)
    client = LiteLLMClient(limits=reg.limits())

    failed = False
    reachable = []
    served = check_served_models(specs)
    for spec in specs:
        if error := served[served_key(spec)]:
            failed = True
            _echo(f"FAIL {error}")  # the error already names the model
        else:
            reachable.append(spec)
    specs = reachable

    async def run():
        return await asyncio.gather(*(probe_model(client, s) for s in specs))

    for spec, results in zip(specs, asyncio.run(run()), strict=True):
        working = [r.mode for r in results if r.ok]
        configured_ok = any(r.mode == spec.structured and r.ok for r in results)
        status = "ok" if configured_ok else ("WORKS ONLY WITH " if working else "FAIL")
        if not configured_ok:
            failed = True
        _echo(f"{spec.name}: {status}{', '.join(working) if not configured_ok else ''}")
        for r in results:
            mark = "ok  " if r.ok else "fail"
            detail = f"{r.latency_ms} ms, ${r.cost_usd:.5f}" if r.ok else (r.error or "")[:120]
            _echo(f"    {mark} {r.mode:<12} {detail}")
        if estimate_cost(spec, 1_000_000, 1_000_000) == 0:
            _echo(
                f"    note: no price for {spec.name}; its cost reads $0 and the batch budget "
                "can't limit it. Set input/output_cost_per_mtok in models.yaml unless it's free."
            )
    if failed:
        raise typer.Exit(1)


class JevFailed(Exception):
    """Jev could not classify a game; the message is safe to print (no key, no URL)."""


@app.command()
def label(
    batch_dir: Path,
    no_jev: bool = typer.Option(
        False,
        "--no-jev",
        help="Make no Jev calls. Jev labels still come from the cache; statements without a "
        "cache entry get none.",
    ),
    jev_cache: Annotated[
        Path | None,
        typer.Option("--jev-cache", help="Jev response cache. Default: BATCH_DIR/.cache/jev."),
    ] = None,
) -> None:
    """Write <game_id>.labels.jsonl next to each game log in BATCH_DIR.

    With TYPESAFE_API_KEY set, also classify statements with Jev (cached in
    BATCH_DIR/.cache/jev) and write the self-tag vs Jev agreement table to
    BATCH_DIR/agreement.json. If Jev fails (bad key, no connection, malformed response),
    the automatic labels are still written and no agreement table is left behind.

    Without the key, or with --no-jev, no call is ever made, but Jev labels and the
    agreement table are still built from every statement already in the cache. Statements
    with no cache entry get no Jev label; the output counts them. If nothing is cached,
    the labels are written without Jev and agreement.json is removed.
    """
    import httpx

    from societysim.analysis import jev
    from societysim.analysis.labeler import _scripted, jev_verdicts, label_batch, read_log
    from societysim.logging.events import EventType

    if not batch_dir.is_dir():
        _echo(f"not a directory: {batch_dir}")
        raise typer.Exit(1)
    if not any(p for p in batch_dir.glob("*.jsonl") if not p.name.endswith(".labels.jsonl")):
        _echo(f"no game logs in {batch_dir}")
        raise typer.Exit(1)

    def without_jev(why: str) -> None:
        (batch_dir / "agreement.json").unlink(missing_ok=True)  # a stale table would mislead
        label_batch(batch_dir)
        _echo(f"Jev skipped ({why}); no self-tag vs Jev agreement")

    load_dotenv()
    cache = jev_cache.resolve() if jev_cache else jev.cache_dir_for(batch_dir)
    if no_jev or not os.environ.get("TYPESAFE_API_KEY"):
        why = "--no-jev" if no_jev else "TYPESAFE_API_KEY not set"
        hits, misses = [0], [0]

        def no_network(request: httpx.Request) -> httpx.Response:
            raise AssertionError("label made a Jev call without a key or with --no-jev")

        def cached_jev_for(events):
            start = next(e for e in events if e.type is EventType.GAME_START)
            stmts = [e for e in events if e.type is EventType.STATEMENT]
            try:
                cached = [
                    e
                    for e in stmts
                    if jev.cached_verdict(cache, e.game_id, e.seq, e.actor, e.public_text or "")
                    is not None
                ]
                hits[0] += len(cached)
                misses[0] += len(stmts) - len(cached)
                if not cached:
                    return {}
                client = httpx.AsyncClient(transport=httpx.MockTransport(no_network))
                got = asyncio.run(
                    jev.classify_events([start, *cached], cache_dir=cache, client=client)
                )
            except (KeyError, TypeError, ValueError):  # a cache file that doesn't parse
                raise JevFailed("malformed cached response") from None
            return jev_verdicts(got)

        def any_cached() -> tuple[bool, int]:
            """Whether any statement Jev would read is cached, and how many statements
            that is (finished, non-scripted games only, as label_batch picks them)."""
            n = 0
            for path in sorted(batch_dir.glob("*.jsonl")):
                if path.name.endswith(".labels.jsonl") or _scripted(path):
                    continue
                events = read_log(path)
                if not any(e.type is EventType.GAME_END for e in events):
                    continue
                for e in events:
                    if e.type is EventType.STATEMENT:
                        n += 1
                        cached = jev.cached_verdict(
                            cache, e.game_id, e.seq, e.actor, e.public_text or ""
                        )
                        if cached is not None:
                            return True, n
            return False, n

        # Nothing cached: label once without Jev (no "sending" line, no all-zero table).
        try:
            found, n = any_cached()
        except (KeyError, TypeError, ValueError):  # a cache file that doesn't parse
            without_jev(f"{why}; cache unusable, malformed cached response")
            return
        if not found:
            without_jev(why)
            if n:
                _echo(f"{n} statements have no cached Jev response in {cache}")
            return
        try:
            label_batch(batch_dir, jev_for=cached_jev_for)
        except JevFailed as e:
            without_jev(f"{why}; cache unusable, {e}")
            return
        n_labels = sum(
            line.count('"source":"jev"')
            for p in batch_dir.glob("*.labels.jsonl")
            for line in p.read_text(encoding="utf-8").splitlines()
        )
        _echo(
            f"Jev from cache only ({why}, no calls): {n_labels} Jev labels from "
            f"{hits[0]} cached statements; {misses[0]} statements had no cache entry in "
            f"{cache} and were skipped"
        )
        return

    def jev_for(events):
        try:
            return jev_verdicts(asyncio.run(jev.classify_events(events, cache_dir=cache)))
        except httpx.HTTPStatusError as e:
            raise JevFailed(f"HTTP {e.response.status_code}") from None
        except httpx.HTTPError as e:
            raise JevFailed(type(e).__name__) from None
        except (KeyError, TypeError, ValueError) as e:  # ValidationError is a ValueError
            raise JevFailed(f"malformed response, {type(e).__name__}") from None

    try:
        label_batch(batch_dir, jev_for=jev_for)
    except JevFailed as e:
        without_jev(f"Jev failed: {e}")


@app.command("sync-neon")
def sync_neon(
    batch_dir: Annotated[
        Path | None, typer.Argument(help="Batch directory, e.g. runs/<batch>.")
    ] = None,
    delete: Annotated[
        list[str] | None, typer.Option("--delete", help="Delete this game id (repeatable).")
    ] = None,
    prune: Annotated[
        bool,
        typer.Option(
            "--prune",
            help="List partial, fixture and --batch test games. Deletes nothing without --apply.",
        ),
    ] = False,
    migrate: Annotated[
        bool,
        typer.Option(
            "--migrate",
            help="Print neon/migrations/*.sql. Runs them only with --apply (needs Seth and "
            "Marcus's OK on the live database).",
        ),
    ] = False,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="With --prune: delete the listed games. With --migrate: run."),
    ] = False,
    older_than_hours: Annotated[
        float, typer.Option("--older-than-hours", help="With --prune: partial-game age cutoff.")
    ] = 6,
    batch: Annotated[
        list[str] | None,
        typer.Option("--batch", help="With --prune: also prune this test batch id (repeatable)."),
    ] = None,
) -> None:
    """Backfill BATCH_DIR into Neon, or --delete, --prune, --migrate. Needs NEON_DATABASE_URL."""
    from societysim.logging.sinks.neon import (
        PruneBlocked,
        apply_migrations,
        apply_schema,
        connect_neon,
        delete_games,
        find_prunable,
        list_migrations,
        prune_games,
        sync_batch,
    )

    load_dotenv()
    if migrate:
        if batch_dir is not None or delete or prune or batch:
            _echo("--migrate takes no BATCH_DIR, --delete, --prune or --batch")
            raise typer.Exit(1)
        migrations = list_migrations()
        for name, sql in migrations:
            _echo(f"-- {name}\n{sql.rstrip()}")
        if not apply:
            _echo("dry run: nothing was run. Re-run with --apply to run these.")
            return
        if not os.environ.get("NEON_DATABASE_URL"):
            _echo("NEON_DATABASE_URL is not set")
            raise typer.Exit(1)

        async def migrate_db():
            conn = await connect_neon()
            try:
                await apply_migrations(conn)
            finally:
                await conn.close()

        asyncio.run(migrate_db())
        _echo(f"ran {len(migrations)} migrations")
        return
    if prune:
        if batch_dir is not None or delete:
            _echo("--prune takes no BATCH_DIR and no --delete")
            raise typer.Exit(1)
    elif apply or batch:
        _echo("--apply only works with --prune or --migrate, --batch only with --prune")
        raise typer.Exit(1)
    elif delete:
        if batch_dir is not None:
            _echo("give either BATCH_DIR or --delete, not both")
            raise typer.Exit(1)
    elif batch_dir is None:
        _echo("give a batch dir to sync, or --delete GAME_ID, or --prune")
        raise typer.Exit(1)
    elif not batch_dir.is_dir():
        _echo(f"not a directory: {batch_dir}")
        raise typer.Exit(1)
    if not os.environ.get("NEON_DATABASE_URL"):
        _echo("NEON_DATABASE_URL is not set")
        raise typer.Exit(1)

    async def run():
        conn = await connect_neon()
        try:
            if prune:
                found = await find_prunable(
                    conn, older_than_hours=older_than_hours, batch_ids=batch
                )
                if apply:
                    await prune_games(conn, [g["game_id"] for g in found])
                return found
            if delete:
                return await delete_games(conn, delete)
            await apply_schema(conn)
            return await sync_batch(batch_dir, conn)
        finally:
            await conn.close()

    try:
        n = asyncio.run(run())
    except PruneBlocked as e:
        _echo(f"refusing to prune: {e}")
        raise typer.Exit(1) from e
    if prune:
        for g in n:
            _echo(
                f"{g['game_id']}  batch={g['batch_id']}  status={g['status']}  "
                f"{g['events']} events, {g['labels']} labels  ({g['reason']})"
            )
        events, labels = sum(g["events"] for g in n), sum(g["labels"] for g in n)
        verb = "deleted" if apply else "would delete"
        _echo(f"{verb} {len(n)} games, {events} events, {labels} labels")
        if not apply:
            _echo("dry run: nothing was deleted. Re-run with --apply to delete.")
        return
    if delete:
        _echo(f"deleted {len(n['deleted'])} games, {n['events']} events, {n['labels']} labels")
        if n["missing"]:
            _echo(f"not found: {', '.join(n['missing'])}")
        return
    _echo(f"synced {n['games']} games, {n['events']} events, {n['labels']} labels")


@app.command()
def report(
    batch: Annotated[Path, typer.Argument(help="Batch directory, e.g. runs/<batch>.")],
) -> None:
    """Write report.md, metrics.json, charts, leaderboard.json and index.json to <batch>/report."""
    from societysim.analysis.report import write_report

    if not batch.is_dir():
        _echo(f"not a directory: {batch}")
        raise typer.Exit(1)
    try:
        out = write_report(batch)
    except ValueError as e:
        if not str(e).startswith("no game logs"):
            _echo(str(e))
            raise typer.Exit(1) from e
        # No finished games: every non-empty log is unfinished.
        n = sum(
            1
            for p in batch.glob("*.jsonl")
            if not p.name.endswith(".labels.jsonl") and p.stat().st_size
        )
        _echo(f"no finished games in {batch} ({n} unfinished skipped)" if n else str(e))
        raise typer.Exit(1) from e
    _echo(f"wrote {out}")


@app.command()
def highlights(
    batch: Annotated[Path, typer.Argument(help="Batch directory, e.g. runs/<batch>.")],
    top: Annotated[int, typer.Option("--top", min=1, help="How many moments to keep.")] = 10,
    out: Annotated[
        Path | None, typer.Option("--out", help="Output file. Default: <batch>/highlights.json.")
    ] = None,
) -> None:
    """Print the batch's best demo moments and write them to <batch>/highlights.json."""
    from societysim.analysis.highlights import write_highlights

    if not batch.is_dir():
        _echo(f"not a directory: {batch}")
        raise typer.Exit(1)
    out = out or batch / "highlights.json"
    moments = write_highlights(batch, top, out)
    _echo(f"{'score':>5}  {'type':<20} {'game':<20} {'day':>3}  {'seq':<9} {'actor':<16} target")
    for m in moments:
        seq = f"{m['seq'][0]}-{m['seq'][1]}"
        actor = f"{m['actor']} {m['actor_model']}"
        target = f"{m['target']} {m['target_model']}" if m["target"] else "-"
        _echo(
            f"{m['score']:>5}  {m['type']:<20} {m['game_id']:<20} {m['day']:>3}  {seq:<9} "
            f"{actor:<16} {target}\n       {m['description']}\n       {m['link']}"
        )
    _echo(f"wrote {out} ({len(moments)} moments)")


@app.command()
def publish(
    batch_dir: Path,
    repo: Annotated[
        str | None,
        typer.Option(
            "--repo", help="Hugging Face dataset, org/name. Default: $SOCIETYSIM_HF_DATASET."
        ),
    ] = None,
    private: Annotated[
        bool, typer.Option("--private/--public", help="Visibility if the repo is created.")
    ] = True,
    force: Annotated[
        bool,
        typer.Option("--force", help="Publish even if the batch is still running."),
    ] = False,
) -> None:
    """Push BATCH_DIR (runs/<batch>) logs, labels and a dataset card to a HF Dataset.

    Only finished games (with a game_end) are published; unfinished ones are skipped, even with
    --force.

    Needs HF_TOKEN with write access. Safe to re-run: files are named per game, so a re-upload
    overwrites them. Run `societysim label` first to include labels.
    """
    from societysim.analysis.publish import publish_batch

    load_dotenv()
    repo = repo or os.environ.get("SOCIETYSIM_HF_DATASET")
    if not repo:
        _echo("no dataset repo: pass --repo org/name or set SOCIETYSIM_HF_DATASET")
        raise typer.Exit(1)
    token = os.environ.get("HF_TOKEN")
    if not token:
        _echo("HF_TOKEN is not set")
        raise typer.Exit(1)
    if not batch_dir.is_dir():
        _echo(f"not a directory: {batch_dir}")
        raise typer.Exit(1)
    try:
        n = publish_batch(batch_dir, repo, token, private=private, force=force)
    except ValueError as e:
        _echo(str(e))
        raise typer.Exit(1) from None
    skipped = f" ({n['skipped']} unfinished skipped)" if n["skipped"] else ""
    _echo(
        f"published {n['games']} games, {n['events']} events, {n['labels']} labels to {repo}"
        f"{skipped}"
    )


if __name__ == "__main__":
    app()
