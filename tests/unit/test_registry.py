import pytest
from typer.testing import CliRunner

from societysim.cli import app
from societysim.config import ModelSpec
from societysim.models.client import CompletionResult, ProviderLimit, Usage, litellm_model_name
from societysim.models.registry import DEFAULT_PATH, Ping, Registry, load_registry, probe_model

EXPECTED = {
    "qwen3-235b",
    "llama4-maverick",
    "gemma4-26b",
    "gpt-oss-120b",
    "deepseek-v3.1",
    "gemini-flash-lite",
    "gemini-flash",
    "gpt-5-nano",
    "gpt-5-mini",
    "amd-gpt-oss-20b",
}


def test_shipped_yaml_loads():
    reg = load_registry(DEFAULT_PATH)
    assert {m.name for m in reg.models} == EXPECTED
    assert reg.get("gpt-5-mini").provider == "openai"


def test_gemini_limit_matches_free_tier():
    lim = load_registry(DEFAULT_PATH).limits()
    assert lim["gemini"] == ProviderLimit(concurrency=2, rpm=10)


def test_every_model_provider_has_a_limit():
    reg = load_registry(DEFAULT_PATH)
    assert {m.provider for m in reg.models} <= set(reg.providers)


def test_hf_pin_applies_to_model_string():
    spec = load_registry(DEFAULT_PATH).get("llama4-maverick")
    assert litellm_model_name(spec).startswith("huggingface/novita/meta-llama/")


def test_apply_env_routes_hf_unified_endpoint(monkeypatch):
    for k in ("HOSTED_VLLM_API_BASE", "HOSTED_VLLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HF_TOKEN", "tok")
    reg = load_registry(DEFAULT_PATH)
    reg.apply_env()
    import os

    assert os.environ["HOSTED_VLLM_API_BASE"] == "https://router.huggingface.co/v1"
    assert os.environ["HOSTED_VLLM_API_KEY"] == "tok"
    for name in ("qwen3-235b", "deepseek-v3.1", "gemma4-26b"):
        spec = reg.get(name)
        assert spec.litellm_model.startswith("hosted_vllm/")
        assert spec.litellm_model.endswith(":deepinfra")
        assert litellm_model_name(spec) == spec.litellm_model
        assert spec.input_cost_per_mtok is not None


def test_amd_entry_routes_to_self_hosted_server(monkeypatch):
    from societysim.models.client import request_kwargs

    monkeypatch.setenv("AMD_VLLM_BASE_URL", "https://abc.trycloudflare.com/v1")
    monkeypatch.setenv("AMD_API_KEY", "amd-key")
    reg = load_registry(DEFAULT_PATH)
    spec = reg.get("amd-gpt-oss-20b")
    assert spec.structured == "json_schema"
    assert spec.input_cost_per_mtok == spec.output_cost_per_mtok == 0
    assert reg.limits()[spec.provider].concurrency >= 1
    assert spec.provider == "openai"
    assert request_kwargs(spec, Ping)["model"] == "openai/gpt-oss-20b"
    assert request_kwargs(spec, Ping)["api_base"] == "https://abc.trycloudflare.com/v1"
    assert request_kwargs(spec, Ping)["api_key"] == "amd-key"
    assert "api_base" not in request_kwargs(reg.get("gpt-5-nano"), Ping)
    assert "api_key" not in request_kwargs(reg.get("gpt-5-nano"), Ping)


def test_play_missing_amd_vars_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.delenv("AMD_VLLM_BASE_URL", raising=False)
    monkeypatch.delenv("AMD_API_KEY", raising=False)
    cfg = tmp_path / "amd.yaml"
    cfg.write_text("n_players: 7\nmodels: [amd-gpt-oss-20b]\n")
    res = CliRunner().invoke(app, ["play", "--config", str(cfg), "--agents", "llm"])
    assert res.exit_code == 1
    assert "AMD_API_KEY" in res.output
    assert "AMD_VLLM_BASE_URL" in res.output


def test_doctor_amd_uses_per_model_endpoint(monkeypatch):
    from types import SimpleNamespace

    import litellm

    monkeypatch.setenv("AMD_VLLM_BASE_URL", "https://fake-amd.example/v1")
    monkeypatch.setenv("AMD_API_KEY", "fake-key")
    calls = []

    async def fake_server(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true,"word":"yes"}'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    monkeypatch.setattr(litellm, "acompletion", fake_server)
    res = CliRunner().invoke(app, ["doctor", "--model", "amd-gpt-oss-20b"])
    assert res.exit_code == 0, res.output
    assert len(calls) == 3
    assert all(call["model"] == "openai/gpt-oss-20b" for call in calls)
    assert all(call["api_base"] == "https://fake-amd.example/v1" for call in calls)
    assert all(call["api_key"] == "fake-key" for call in calls)


def test_resolve_match_and_unknown():
    reg = load_registry(DEFAULT_PATH)
    assert [m.name for m in reg.match("gemini-*")] == ["gemini-flash-lite", "gemini-flash"]
    assert [m.name for m in reg.resolve(["gpt-5-nano"])] == ["gpt-5-nano"]
    with pytest.raises(KeyError, match="unknown model"):
        reg.get("nope")


def test_duplicates_and_typos_rejected():
    m = {"name": "a", "litellm_model": "openai/x"}
    with pytest.raises(ValueError, match="duplicate"):
        Registry.model_validate({"models": [m, m]})
    with pytest.raises(ValueError):
        Registry.model_validate({"models": [m], "providrs": {}})


class FakeModeClient:
    """Only `ok_modes` return valid JSON."""

    def __init__(self, ok_modes):
        self.ok_modes = ok_modes
        self.calls = []

    async def complete(self, model, messages, response_model):
        self.calls.append(model.structured)
        usage = Usage(model=model.name, latency_ms=12, cost_usd=0.001)
        if model.structured in self.ok_modes:
            return CompletionResult(parsed=Ping(ok=True, word="hi"), raw="{}", usage=usage)
        return CompletionResult(parsed=None, raw="", error="bad", usage=usage)


async def test_probe_tries_every_mode_configured_first():
    spec = ModelSpec(name="m", litellm_model="openai/x", structured="json_object")
    client = FakeModeClient({"none"})
    res = await probe_model(client, spec)
    assert client.calls == ["json_object", "json_schema", "none"]
    assert {r.mode: r.ok for r in res} == {"json_object": False, "json_schema": False, "none": True}
    assert res[0].latency_ms == 12 and res[0].error == "bad"


def _run_doctor(monkeypatch, ok_modes, *args):
    monkeypatch.setattr(
        "societysim.models.client.LiteLLMClient", lambda **kw: FakeModeClient(ok_modes)
    )
    return CliRunner().invoke(app, ["doctor", *args])


def test_doctor_green_and_red(monkeypatch):
    ok = _run_doctor(monkeypatch, {"json_schema"}, "--model", "gpt-5-*")
    assert ok.exit_code == 0, ok.output
    assert "gpt-5-nano" in ok.output and "gpt-5-mini" in ok.output
    assert "qwen3" not in ok.output

    bad = _run_doctor(monkeypatch, set(), "--model", "gpt-5-nano")
    assert bad.exit_code == 1
    assert "FAIL" in bad.output


def test_ping_schema_forbids_extra_properties():
    assert Ping.model_json_schema()["additionalProperties"] is False


def test_doctor_missing_config():
    res = CliRunner().invoke(app, ["doctor", "--config", "nope.yaml"])
    assert res.exit_code == 1 and "not found" in res.output


def test_doctor_survives_non_ascii_error_on_cp1252(monkeypatch):
    class Bad(FakeModeClient):
        async def complete(self, model, messages, response_model):
            usage = Usage(model=model.name)
            return CompletionResult(parsed=None, raw="", error="bad → 中", usage=usage)

    monkeypatch.setattr("societysim.models.client.LiteLLMClient", lambda **kw: Bad(set()))
    res = CliRunner(charset="cp1252").invoke(app, ["doctor", "--model", "gpt-5-nano"])
    assert res.exit_code == 1
    assert not isinstance(res.exception, UnicodeEncodeError)
    assert "FAIL" in res.output


def test_doctor_no_match(monkeypatch):
    res = _run_doctor(monkeypatch, set(), "--model", "zzz*")
    assert res.exit_code == 1
