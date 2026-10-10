# societysim

LLM agents from different providers play Werewolf against each other. We log every move, including each agent's private reasoning, and use the logs to measure how well each model lies, spots lies, persuades and cooperates. A web viewer replays any game.

Status: early setup. The full plan is in [docs/PLAN.md](docs/PLAN.md).

## Quick start

```bash
uv sync
uv run pytest
uv run societysim --help
```

Copy `.env.example` to `.env` and fill in the keys you have.
