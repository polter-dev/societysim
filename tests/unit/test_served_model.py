"""The amd-* entry against a fake OpenAI-compatible server that 404s unknown model ids, the
way vLLM does. Real HTTP through LiteLLM, so prefix stripping on the `openai/` route shows up."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from societysim.cli import app
from societysim.models.client import LiteLLMClient
from societysim.models.registry import (
    DEFAULT_PATH,
    DOCTOR_MESSAGES,
    Ping,
    check_served_model,
    check_served_models,
    load_registry,
)

KEY = "amd-test-key-0123456789abcdef0123456789"


class FakeVLLM(ThreadingHTTPServer):
    def __init__(self, served):
        super().__init__(("127.0.0.1", 0), Handler)
        self.served = served
        self.chat_models = []  # `model` field of every chat completion request
        self.models_gets = 0  # GET /v1/models requests

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_address[1]}/v1"


class Handler(BaseHTTPRequestHandler):
    server: FakeVLLM

    def log_message(self, *args):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        if self.headers.get("Authorization") == f"Bearer {KEY}":
            return True
        self._send(401, {"error": {"message": "Unauthorized", "code": 401}})
        return False

    def do_GET(self):
        if self._authed() and self.path == "/v1/models":
            self.server.models_gets += 1
            self._send(200, {"object": "list", "data": [{"id": m} for m in self.server.served]})

    def do_POST(self):
        if not self._authed():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = body["model"]
        self.server.chat_models.append(model)
        if model not in self.server.served:
            msg = f"The model `{model}` does not exist."
            self._send(404, {"error": {"message": msg, "type": "NotFoundError", "code": 404}})
            return
        self._send(
            200,
            {
                "id": "c1",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"ok":true,"word":"yes"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            },
        )


@pytest.fixture
def vllm(monkeypatch):
    """Serves what serve.sh serves today: the HF id as --served-model-name."""
    server = FakeVLLM(["openai/gpt-oss-20b"])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.setenv("AMD_VLLM_BASE_URL", server.base)
    monkeypatch.setenv("AMD_API_KEY", KEY)
    yield server
    server.shutdown()
    server.server_close()


def amd_spec(**update):
    return load_registry(DEFAULT_PATH).get("amd-gpt-oss-20b").model_copy(update=update)


async def test_shipped_amd_entry_sends_the_served_id(vllm):
    result = await LiteLLMClient(max_retries=0).complete(amd_spec(), DOCTOR_MESSAGES, Ping)
    assert result.error is None
    assert result.parsed == Ping(ok=True, word="yes")
    assert vllm.chat_models == ["openai/gpt-oss-20b"]
    assert check_served_model(amd_spec()) is None


async def test_stripped_prefix_404s_and_preflight_names_the_fix(vllm):
    stale = amd_spec(litellm_model="openai/gpt-oss-20b")  # the post-#115 entry
    result = await LiteLLMClient(max_retries=0).complete(stale, DOCTOR_MESSAGES, Ping)
    assert result.parsed is None
    assert "does not exist" in result.error
    assert vllm.chat_models == ["gpt-oss-20b"]
    error = check_served_model(stale)
    assert "'gpt-oss-20b'" in error and "openai/gpt-oss-20b" in error


def test_preflight_reports_bad_key(vllm, monkeypatch):
    monkeypatch.setenv("AMD_API_KEY", "wrong")
    assert "401" in check_served_model(amd_spec())
    assert check_served_model(load_registry(DEFAULT_PATH).get("gpt-5-nano")) is None


def test_doctor_green_against_server(vllm):
    res = CliRunner().invoke(app, ["doctor", "--model", "amd-gpt-oss-20b"])
    assert res.exit_code == 0, res.output
    assert "amd-gpt-oss-20b: ok" in res.output
    assert vllm.chat_models == ["openai/gpt-oss-20b"] * 3


def test_doctor_fails_fast_on_unserved_id(vllm):
    vllm.served = ["gpt-oss-20b"]  # e.g. a server restarted with a short alias
    res = CliRunner().invoke(app, ["doctor", "--model", "amd-gpt-oss-20b"])
    assert res.exit_code == 1
    assert "FAIL amd-gpt-oss-20b: " in res.output
    assert "does not serve 'openai/gpt-oss-20b'" in res.output
    assert vllm.chat_models == []


def test_play_fails_before_game_one(vllm, tmp_path):
    vllm.served = ["gpt-oss-20b"]
    cfg = tmp_path / "amd.yaml"
    cfg.write_text("n_players: 7\nmodels: [amd-gpt-oss-20b]\n")
    res = CliRunner().invoke(app, ["play", "--config", str(cfg), "--agents", "llm"])
    assert res.exit_code == 1
    assert "does not serve" in res.output
    assert vllm.chat_models == []


def test_seven_seats_check_served_once_and_report_once(vllm, tmp_path):
    vllm.served = ["gpt-oss-20b"]
    cfg = tmp_path / "amd.yaml"
    cfg.write_text("n_players: 7\nmodels: [amd-gpt-oss-20b]\n")
    res = CliRunner().invoke(app, ["play", "--config", str(cfg), "--agents", "llm"])
    assert res.exit_code == 1
    assert vllm.models_gets == 1
    assert res.output.count("does not serve") == 1


def test_check_served_models_once_per_distinct_pair(vllm):
    spec = amd_spec()
    other = spec.model_copy(update={"name": "amd-alias"})
    results = check_served_models([spec] * 7 + [other])
    assert results == {(spec.api_base, spec.litellm_model): None}
    assert vllm.models_gets == 1


def test_batch_fails_before_game_one(vllm, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # amd-* uses AMD_API_KEY only
    vllm.served = ["gpt-oss-20b"]
    cfg = tmp_path / "batch.yaml"
    cfg.write_text(
        '{"batch_id": "amd", "n_games": 1, "models": ["amd-gpt-oss-20b"], "budget_usd": 1}'
    )
    args = [
        "batch",
        "--config",
        str(cfg),
        "--registry",
        str(Path(__file__).resolve().parents[2] / DEFAULT_PATH),
    ]
    res = CliRunner().invoke(app, [*args, "--agents", "llm"])
    assert res.exit_code == 1, res.output
    assert "does not serve" in res.output
    assert vllm.chat_models == []
    assert not (tmp_path / "runs").exists()
