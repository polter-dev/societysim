"""LiteLLMClient with `litellm.acompletion` mocked. No network."""

import asyncio
import json
from types import SimpleNamespace

import litellm
import pytest

from societysim.agents.schemas import VoteResponse
from societysim.config import ModelSpec
from societysim.models.client import (
    LiteLLMClient,
    ProviderLimit,
    _Limiter,
    estimate_cost,
    extract_json,
    limit_key,
    litellm_model_name,
    request_kwargs,
)

VOTE = {
    "private_reasoning": {"thoughts": "hm", "suspicion": [{"player": "p1", "p": 0.4}]},
    "action": {"vote": "p1"},
}
MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def spec(**kw) -> ModelSpec:
    defaults = {"litellm_model": "openai/gpt-x", "input_cost_per_mtok": 1.0}
    return ModelSpec(name="m", **(defaults | {"output_cost_per_mtok": 2.0} | kw))


def reply(content: str | None, prompt_tokens: int = 100, completion_tokens: int = 20):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def rate_limited():
    return litellm.exceptions.RateLimitError("slow down", llm_provider="openai", model="gpt-x")


def server_error():
    return litellm.exceptions.InternalServerError("boom", llm_provider="openai", model="gpt-x")


class FakeLiteLLM:
    """Stands in for litellm.acompletion: plays `outcomes` (replies or exceptions) in order."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class Sleeps:
    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, s: float) -> None:
        self.delays.append(s)


@pytest.fixture
def sleeps():
    return Sleeps()


@pytest.fixture
def client(sleeps):
    return LiteLLMClient(sleep=sleeps, backoff_s=1.0)


def mock(monkeypatch, *outcomes) -> FakeLiteLLM:
    fake = FakeLiteLLM(*outcomes)
    monkeypatch.setattr(litellm, "acompletion", fake)
    return fake


# --- structured modes ---------------------------------------------------------------------


async def test_json_schema_mode_sends_strict_schema(monkeypatch, client):
    fake = mock(monkeypatch, reply(json.dumps(VOTE)))
    result = await client.complete(spec(structured="json_schema"), MESSAGES, VoteResponse)

    assert isinstance(result.parsed, VoteResponse)
    assert result.parsed.action.vote == "p1"
    assert result.error is None
    call = fake.calls[0]
    assert call["messages"] == MESSAGES
    fmt = call["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "VoteResponse"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == VoteResponse.model_json_schema()


async def test_json_object_mode(monkeypatch, client):
    fake = mock(monkeypatch, reply(json.dumps(VOTE)))
    result = await client.complete(spec(structured="json_object"), MESSAGES, VoteResponse)

    assert result.parsed is not None
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


async def test_none_mode_sends_no_response_format_and_parses_fenced_reply(monkeypatch, client):
    fake = mock(monkeypatch, reply(f"Sure!\n```json\n{json.dumps(VOTE)}\n```"))
    result = await client.complete(spec(structured="none"), MESSAGES, VoteResponse)

    assert "response_format" not in fake.calls[0]
    assert isinstance(result.parsed, VoteResponse)


async def test_parse_failure_returns_error_not_raise(monkeypatch, client):
    mock(monkeypatch, reply('{"action": {"vote": "p1"}}'))
    result = await client.complete(spec(), MESSAGES, VoteResponse)

    assert result.parsed is None
    assert result.error and "private_reasoning" in result.error
    assert result.raw == '{"action": {"vote": "p1"}}'
    assert result.usage.input_tokens == 100  # tokens were still spent


async def test_empty_reply_is_an_error(monkeypatch, client):
    mock(monkeypatch, reply(None))
    result = await client.complete(spec(), MESSAGES, VoteResponse)
    assert result.parsed is None
    assert result.error == "empty reply"


def test_extract_json():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Here: {"a": {"b": 2}} done') == '{"a": {"b": 2}}'
    assert extract_json("no json") == "no json"


# --- per-model settings -------------------------------------------------------------------


async def test_per_model_settings_are_sent(monkeypatch, client):
    fake = mock(monkeypatch, reply(json.dumps(VOTE)))
    s = spec(temperature=0.2, max_tokens=333, reasoning_effort="minimal")
    await client.complete(s, MESSAGES, VoteResponse)

    call = fake.calls[0]
    assert call["model"] == "openai/gpt-x"
    assert call["temperature"] == 0.2
    assert call["max_tokens"] == 333
    assert call["reasoning_effort"] == "minimal"


@pytest.mark.parametrize(
    ("litellm_model", "sent"),
    [
        ("gemini/gemini-3.8-flash", False),
        ("gemini/gemini-3.5-flash-lite", False),
        ("gemini/gemini-4.0-pro", False),
        ("gemini/gemini-2.5-flash", True),
        ("openai/gpt-5-nano", True),
        ("hosted_vllm/Qwen/Qwen3-235B-A22B:deepinfra", True),
    ],
)
def test_temperature_not_sent_to_gemini_3_plus(litellm_model, sent):
    kw = request_kwargs(spec(litellm_model=litellm_model), VoteResponse)
    assert ("temperature" in kw) is sent
    assert not {"top_p", "top_k"} & kw.keys()


async def test_gemini_3_call_omits_temperature(monkeypatch, client):
    fake = mock(monkeypatch, reply(json.dumps(VOTE)))
    result = await client.complete(
        spec(litellm_model="gemini/gemini-3.8-flash", temperature=0.2), MESSAGES, VoteResponse
    )
    assert result.parsed is not None
    assert "temperature" not in fake.calls[0]
    assert fake.calls[0]["drop_params"] is True


async def test_reasoning_effort_omitted_when_unset(monkeypatch, client):
    fake = mock(monkeypatch, reply(json.dumps(VOTE)))
    await client.complete(spec(), MESSAGES, VoteResponse)
    assert "reasoning_effort" not in fake.calls[0]


async def test_per_model_endpoint_and_key_do_not_leak(monkeypatch, client):
    monkeypatch.setenv("AMD_VLLM_BASE_URL", "https://amd.example/v1")
    monkeypatch.setenv("AMD_API_KEY", "amd-secret")
    fake = mock(monkeypatch, reply(json.dumps(VOTE)), reply(json.dumps(VOTE)))
    amd = spec(api_base="${AMD_VLLM_BASE_URL}", api_key_env="AMD_API_KEY")
    await client.complete(amd, MESSAGES, VoteResponse)
    await client.complete(spec(), MESSAGES, VoteResponse)
    assert fake.calls[0]["api_base"] == "https://amd.example/v1"
    assert fake.calls[0]["api_key"] == "amd-secret"
    assert "api_base" not in fake.calls[1]
    assert "api_key" not in fake.calls[1]


async def test_missing_per_model_env_returns_safe_error(monkeypatch, client):
    monkeypatch.delenv("AMD_VLLM_BASE_URL", raising=False)
    monkeypatch.delenv("AMD_API_KEY", raising=False)
    model = spec(api_base="${AMD_VLLM_BASE_URL}", api_key_env="AMD_API_KEY")
    result = await client.complete(model, MESSAGES, VoteResponse)
    assert result.parsed is None and "AMD_VLLM_BASE_URL" in result.error


def test_literal_api_base(monkeypatch):
    monkeypatch.setenv("AMD_API_KEY", "fake-key")
    kwargs = request_kwargs(
        spec(api_base="https://local.example/v1", api_key_env="AMD_API_KEY"), VoteResponse
    )
    assert kwargs["api_base"] == "https://local.example/v1"


@pytest.mark.parametrize(
    ("model", "provider", "expected"),
    [
        ("huggingface/together/Qwen/Qwen3", "novita", "huggingface/novita/Qwen/Qwen3"),
        ("huggingface/Qwen/Qwen3", "novita", "huggingface/novita/Qwen/Qwen3"),
        ("huggingface/together/Qwen/Qwen3", None, "huggingface/together/Qwen/Qwen3"),
        ("gemini/gemini-2.5-flash", "novita", "gemini/gemini-2.5-flash"),
    ],
)
def test_hf_provider_pin(model, provider, expected):
    assert litellm_model_name(ModelSpec(name="m", litellm_model=model, hf_provider=provider)) == (
        expected
    )


# --- usage and cost -----------------------------------------------------------------------


def test_cost_uses_price_overrides():
    s = spec(input_cost_per_mtok=3.0, output_cost_per_mtok=15.0)
    assert estimate_cost(s, 1_000_000, 100_000) == pytest.approx(3.0 + 1.5)


def test_cost_falls_back_to_litellm_prices_without_cache_discount(monkeypatch):
    seen = {}

    def fake_cost_per_token(**kw):
        seen.update(kw)
        return 0.25, 0.5

    monkeypatch.setattr(litellm, "cost_per_token", fake_cost_per_token)
    s = ModelSpec(name="m", litellm_model="openai/gpt-x", output_cost_per_mtok=10.0)
    assert estimate_cost(s, 1000, 100_000) == pytest.approx(0.25 + 1.0)
    assert seen == {"model": "openai/gpt-x", "prompt_tokens": 1000, "completion_tokens": 100_000}


def test_unknown_model_costs_zero(monkeypatch):
    def boom(**kw):
        raise ValueError("not mapped")

    monkeypatch.setattr(litellm, "cost_per_token", boom)
    assert estimate_cost(ModelSpec(name="m", litellm_model="huggingface/x/y/z"), 10, 10) == 0.0


async def test_usage_fields(monkeypatch, client):
    mock(monkeypatch, reply(json.dumps(VOTE), prompt_tokens=2000, completion_tokens=500))
    result = await client.complete(spec(), MESSAGES, VoteResponse)

    u = result.usage
    assert u.model == "m"
    assert (u.input_tokens, u.output_tokens, u.attempts) == (2000, 500, 1)
    assert u.cost_usd == pytest.approx(2000 * 1.0 / 1e6 + 500 * 2.0 / 1e6)
    assert u.latency_ms >= 0


# --- retries ------------------------------------------------------------------------------


async def test_retries_on_429_then_succeeds(monkeypatch, client, sleeps):
    fake = mock(monkeypatch, rate_limited(), server_error(), reply(json.dumps(VOTE)))
    result = await client.complete(spec(), MESSAGES, VoteResponse)

    assert result.parsed is not None
    assert result.usage.attempts == 3
    assert len(fake.calls) == 3
    backoffs = sleeps.delays
    assert len(backoffs) == 2
    assert 0.5 <= backoffs[0] <= 1.0 and 1.0 <= backoffs[1] <= 2.0  # exponential, jittered


async def test_retries_exhausted_returns_error(monkeypatch, sleeps):
    client = LiteLLMClient(sleep=sleeps, max_retries=2)
    mock(monkeypatch, rate_limited(), rate_limited(), rate_limited())
    result = await client.complete(spec(), MESSAGES, VoteResponse)

    assert result.parsed is None
    assert result.error and result.error.startswith("RateLimitError")
    assert result.usage.attempts == 3


async def test_bad_request_is_not_retried(monkeypatch, client):
    err = litellm.exceptions.BadRequestError("bad schema", model="gpt-x", llm_provider="openai")
    fake = mock(monkeypatch, err)
    result = await client.complete(spec(), MESSAGES, VoteResponse)

    assert len(fake.calls) == 1
    assert result.parsed is None
    assert result.error and "BadRequestError" in result.error


async def test_empty_choices_is_an_error_not_indexerror(monkeypatch, client):
    resp = SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=5, completion_tokens=0))
    mock(monkeypatch, resp)
    result = await client.complete(spec(), MESSAGES, VoteResponse)

    assert result.parsed is None
    assert result.error and "no choices" in result.error


async def test_none_choices_is_an_error(monkeypatch, client):
    mock(monkeypatch, SimpleNamespace(choices=None, usage=None))
    result = await client.complete(spec(), MESSAGES, VoteResponse)

    assert result.parsed is None and result.error


async def test_auth_error_fails_fast_and_names_env_var(monkeypatch, client, sleeps):
    secret = "sk-SECRET123"
    err = litellm.exceptions.AuthenticationError(
        f"Incorrect API key provided: {secret}", llm_provider="openai", model="gpt-x"
    )
    fake = mock(monkeypatch, err, reply(json.dumps(VOTE)))
    result = await client.complete(spec(), MESSAGES, VoteResponse)

    assert len(fake.calls) == 1 and result.usage.attempts == 1
    assert sleeps.delays == []
    assert result.parsed is None
    assert result.error and "OPENAI_API_KEY" in result.error
    assert secret not in result.error


async def test_missing_credentials_error_fails_fast(monkeypatch, client, sleeps):
    err = RuntimeError("The api_key client option must be set or GEMINI_API_KEY is unset")
    fake = mock(monkeypatch, err)
    gemini = spec(litellm_model="gemini/gemini-2.5-flash")
    result = await client.complete(gemini, MESSAGES, VoteResponse)

    assert len(fake.calls) == 1 and sleeps.delays == []
    assert result.error and "GEMINI_API_KEY" in result.error
    assert "client option must be set" not in result.error


async def test_hosted_vllm_auth_error_names_hf_token(monkeypatch, client):
    err = litellm.exceptions.AuthenticationError("bad key", llm_provider="hosted_vllm", model="m")
    mock(monkeypatch, err)
    result = await client.complete(
        spec(litellm_model="hosted_vllm/some/model"), MESSAGES, VoteResponse
    )

    assert result.error and "HF_TOKEN" in result.error
    assert "HOSTED_VLLM_API_KEY" not in result.error


# --- rate limits --------------------------------------------------------------------------


def test_default_limits():
    c = LiteLLMClient()
    assert c.limits["gemini"].concurrency == 4
    assert c.limits["huggingface"].concurrency == 8
    assert c.limits["openai"].concurrency == 8


async def test_concurrency_limit_per_provider(monkeypatch):
    client = LiteLLMClient(limits={"openai": ProviderLimit(concurrency=2, rpm=10_000)})
    in_flight = peak = 0

    async def slow(**kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return reply(json.dumps(VOTE))

    monkeypatch.setattr(litellm, "acompletion", slow)
    await asyncio.gather(*(client.complete(spec(), MESSAGES, VoteResponse) for _ in range(6)))
    assert peak == 2


HF_BASE = "https://router.huggingface.co/v1"


async def test_hf_router_routes_share_one_in_flight_limit(monkeypatch):
    monkeypatch.setenv("HOSTED_VLLM_API_BASE", HF_BASE)
    client = LiteLLMClient(limits={"huggingface": ProviderLimit(concurrency=3, rpm=10_000)})
    in_flight = peak = 0

    async def slow(**kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return reply(json.dumps(VOTE))

    monkeypatch.setattr(litellm, "acompletion", slow)
    hf = spec(litellm_model="huggingface/org/model")
    vllm = spec(litellm_model="hosted_vllm/org/model:deepinfra")
    await asyncio.gather(
        *(client.complete(s, MESSAGES, VoteResponse) for s in [hf, vllm] * 6),
    )
    assert peak == 3


def test_limit_key(monkeypatch):
    monkeypatch.delenv("HOSTED_VLLM_API_BASE", raising=False)
    vllm = spec(litellm_model="hosted_vllm/org/model")
    assert limit_key(vllm) == "hosted_vllm"  # no HF router configured
    monkeypatch.setenv("HOSTED_VLLM_API_BASE", HF_BASE)
    assert limit_key(vllm) == "huggingface"
    assert limit_key(spec(litellm_model="huggingface/org/model")) == "huggingface"
    assert limit_key(spec()) == "openai"
    own = spec(litellm_model="hosted_vllm/org/model", api_base="https://my.box/v1")
    assert limit_key(own) == "hosted_vllm"  # its own endpoint keeps its own limit


async def test_token_bucket_waits_for_refill():
    now = [0.0]
    waits: list[float] = []

    async def sleep(s):
        waits.append(s)
        now[0] += s

    limiter = _Limiter(ProviderLimit(concurrency=2, rpm=60), clock=lambda: now[0], sleep=sleep)
    for _ in range(4):
        await limiter.take_token()

    # burst of 2, then one token per second at 60 rpm
    assert waits == [pytest.approx(1.0), pytest.approx(1.0)]
    assert now[0] == pytest.approx(2.0)
