"""CLM's external embedding boundary and TypeSafe answer shaping."""

from __future__ import annotations

import base64
import builtins
import json
import math
import struct
import sys
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

import decidealot.clm_runtime as clm_runtime
from decidealot.clm_runtime import (
    CLMEmbeddingError,
    CLMEngine,
    CLMInputError,
    CLMRuntimeError,
    EmbeddingBatch,
    answer_from_logits,
    create_application,
    decode_embedding_response,
    question_candidates,
    request_embeddings,
    run_clm,
    state_text,
    to_text,
)

_embedding_dimensions = 4096


def _embedding(value: float) -> list[float]:
    return [value] * _embedding_dimensions


def _encoded_embedding(value: float) -> str:
    return base64.b64encode(struct.pack("<4096f", *_embedding(value))).decode("ascii")


def test_embedding_response_accepts_out_of_order_float_and_base64_vectors() -> None:
    encoded = _encoded_embedding(0.25)

    embeddings = decode_embedding_response(
        {
            "data": [
                {"index": 1, "embedding": encoded},
                {"index": 0, "embedding": _embedding(0.5)},
            ],
            "usage": {"prompt_tokens": 7},
        },
        expected_count=2,
    )

    assert embeddings.vectors[0][0] == 0.5
    assert embeddings.vectors[1][0] == 0.25
    assert embeddings.input_tokens == 7


@pytest.mark.parametrize(
    "body",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": _embedding(1.0)}]},
        {
            "data": [
                {"index": 0, "embedding": _embedding(1.0)},
                {"index": 0, "embedding": _embedding(1.0)},
            ]
        },
        {
            "data": [
                {"index": 0, "embedding": _embedding(1.0)[:-1]},
                {"index": 1, "embedding": _embedding(1.0)},
            ]
        },
        {
            "data": [
                {"index": 0, "embedding": "not-base64"},
                {"index": 1, "embedding": _embedding(1.0)},
            ]
        },
    ],
)
def test_embedding_response_rejects_incomplete_or_invalid_vectors(body: dict[str, object]) -> None:
    with pytest.raises(CLMEmbeddingError):
        decode_embedding_response(body, expected_count=2)


def test_clm_renders_structured_state_and_noul_candidates_like_the_reference_layout() -> None:
    rendered_state = state_text({"operation": "delete"}, "Is this safe?")
    keys, candidates = question_candidates(
        {
            "type": "noul",
            "instructions": "Is this safe?",
            "criteria": None,
        }
    )

    assert rendered_state == "operation: delete\n\nIs this safe?"
    assert keys == ["false", "true"]
    assert candidates == [
        "false: No. This is false: Is this safe?",
        "true: Yes. This is true: Is this safe?",
    ]


def test_clm_projects_choice_score_and_noul_answers_to_the_typesafe_shapes() -> None:
    choice = answer_from_logits(
        {"type": "choice", "criteria": {"allow": "Safe", "deny": "Unsafe"}},
        ["allow", "deny"],
        [4.0, 1.0],
    )
    score = answer_from_logits(
        {"type": "score", "criteria": ["low", "high"]},
        ["0", "1"],
        [0.0, 2.0],
    )
    noul = answer_from_logits(
        {"type": "noul", "criteria": None},
        ["false", "true"],
        [1.0, 0.0],
    )

    assert choice["type"] == "choice"
    assert choice["choice"] == "allow"
    assert set(choice["probabilities"]) == {"allow", "deny"}
    assert score["type"] == "score"
    assert score["legend"] == {"0": "low", "1": "high"}
    assert noul == {"type": "noul", "noul": pytest.approx(0.2689414214)}


def test_clm_requests_base64_embeddings_with_authentication_and_validates_the_response() -> None:
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            status_code=200,
            json={
                "data": [
                    {"index": 1, "embedding": _encoded_embedding(0.25)},
                    {"index": 0, "embedding": _encoded_embedding(0.5)},
                ],
                "usage": {"prompt_tokens": 11},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        embeddings = request_embeddings(
            client=client,
            url="https://embeddings.example.test/v1/embeddings",
            model="qwen3-8b",
            texts=["state", "candidate"],
            api_key="test-secret",
        )

    assert captured_request is not None
    assert captured_request.headers["authorization"] == "Bearer test-secret"
    assert json.loads(captured_request.content) == {
        "model": "qwen3-8b",
        "input": ["state", "candidate"],
        "encoding_format": "base64",
    }
    assert embeddings.input_tokens == 11
    assert embeddings.vectors[0][0] == 0.5
    assert embeddings.vectors[1][0] == 0.25


def test_clm_batches_all_state_vectors_before_candidate_vectors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = CLMEngine(
        checkpoint_path=tmp_path / "CLM_v0.1-8B.pt",
        embeddings_url="https://embeddings.example.test/v1/embeddings",
        embeddings_model="qwen3-8b",
        embeddings_api_key=None,
        timeout_seconds=10.0,
        device="cpu",
    )
    captured_texts: list[str] = []

    def fake_request_embeddings(texts: list[str]) -> EmbeddingBatch:
        captured_texts.extend(texts)
        return EmbeddingBatch(vectors=[_embedding(0.0)] * len(texts), input_tokens=0)

    def fake_answer_embeddings(*_arguments: object) -> dict[str, Any]:
        return {"result": {"type": "noul", "noul": 0.5}}

    monkeypatch.setattr(engine, "_request_embeddings", fake_request_embeddings)
    monkeypatch.setattr(engine, "_ensure_heads", lambda: None)
    monkeypatch.setattr(engine, "_answer_embeddings", fake_answer_embeddings)

    response = engine.answer(
        {
            "model": "clm",
            "state": {"action": "write"},
            "questions": {
                "first": {
                    "type": "choice",
                    "instructions": "Pick one.",
                    "criteria": {"allow": "Allow", "deny": "Deny"},
                },
                "second": {
                    "type": "noul",
                    "instructions": "Is this safe?",
                    "criteria": None,
                },
            },
        }
    )

    assert captured_texts == [
        "action: write\n\nPick one.",
        "action: write\n\nIs this safe?",
        "Allow",
        "Deny",
        "false: No. This is false: Is this safe?",
        "true: Yes. This is true: Is this safe?",
    ]
    assert response["answers"] == {"result": {"type": "noul", "noul": 0.5}}


def test_clm_renders_choice_score_and_nested_values_and_rejects_invalid_questions() -> None:
    assert question_candidates({"type": "noul"}) == (
        ["false", "true"],
        ["false: false", "true: true"],
    )
    assert to_text({"outer": ["first", {"inner": True}], "none": None}) == (
        "outer:\n  - first\n  -\n    inner: true\n\nnone: "
    )
    assert question_candidates(
        {
            "type": "choice",
            "criteria": {"allow": {"description": "Safe"}, "deny": ""},
        }
    ) == (["allow", "deny"], ["description: Safe", "deny"])
    assert question_candidates({"type": "score", "criteria": ["low", {"level": "high"}]}) == (
        ["0", "1"],
        ["low", "level: high"],
    )

    with pytest.raises(CLMInputError):
        to_text(float("inf"))
    with pytest.raises(CLMInputError):
        question_candidates({"type": "choice", "criteria": {}})
    with pytest.raises(CLMInputError):
        question_candidates({"type": "score", "criteria": []})
    with pytest.raises(CLMInputError):
        question_candidates({"type": "unsupported", "criteria": None})


def test_clm_rejects_invalid_logits_and_embeddings_transport_failures() -> None:
    with pytest.raises(CLMRuntimeError):
        answer_from_logits(
            {"type": "choice", "criteria": {"allow": "Safe", "deny": "Unsafe"}},
            ["allow", "deny"],
            [float("nan"), 0.0],
        )
    with pytest.raises(CLMInputError):
        answer_from_logits({"type": "choice"}, ["allow"], [1.0, 0.0])

    def rejected(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=401)

    with httpx.Client(transport=httpx.MockTransport(rejected)) as client:
        with pytest.raises(CLMEmbeddingError, match="rejected"):
            request_embeddings(client, "https://embeddings.example.test", "qwen", ["text"], None)

    def unavailable(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as client:
        with pytest.raises(CLMEmbeddingError, match="unavailable"):
            request_embeddings(client, "https://embeddings.example.test", "qwen", ["text"], None)


@pytest.mark.parametrize(
    ("body", "expected_count"),
    [
        ([], 1),
        ({"data": "not a list"}, 1),
        ({"data": ["not an object"]}, 1),
        ({"data": [{"index": True, "embedding": _embedding(1.0)}]}, 1),
        ({"data": [{"index": 0, "embedding": [True] * _embedding_dimensions}]}, 1),
        ({"data": [{"index": 0, "embedding": [float("nan")] * _embedding_dimensions}]}, 1),
        ({"data": [{"index": 0, "embedding": None}]}, 1),
    ],
)
def test_clm_rejects_all_other_invalid_embedding_response_shapes(
    body: object,
    expected_count: int,
) -> None:
    with pytest.raises(CLMEmbeddingError):
        decode_embedding_response(body, expected_count)


def test_clm_maps_embeddings_timeouts_and_invalid_json_to_stable_errors() -> None:
    def timed_out(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with httpx.Client(transport=httpx.MockTransport(timed_out)) as client:
        with pytest.raises(CLMEmbeddingError, match="timed out"):
            request_embeddings(client, "https://embeddings.example.test", "qwen", ["text"], None)

    def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, content=b"not json")

    with httpx.Client(transport=httpx.MockTransport(malformed)) as client:
        with pytest.raises(CLMEmbeddingError, match="invalid JSON"):
            request_embeddings(client, "https://embeddings.example.test", "qwen", ["text"], None)


class _FakeTensor:
    def __init__(self, values: Any) -> None:
        self.values = values

    def __getitem__(self, index: Any) -> _FakeTensor:
        return _FakeTensor(self.values[index])

    def __rmul__(self, factor: float) -> _FakeTensor:
        return _FakeTensor([factor * value for value in self.values])

    def tolist(self) -> Any:
        return self.values


class _FakeScalar:
    def __init__(self, value: float) -> None:
        self.value = value

    def float(self) -> _FakeScalar:
        return self

    def exp(self) -> _FakeScalar:
        return self

    def __float__(self) -> builtins.float:
        return self.value


class _FakeModule:
    def __init__(self, *_arguments: Any) -> None:
        self.device: str | None = None
        self.loaded_state: Mapping[str, object] | None = None

    def __call__(self, tensor: Any) -> Any:
        return self.forward(tensor)

    def forward(self, tensor: Any) -> Any:
        return tensor

    def eval(self) -> _FakeModule:
        return self

    def to(self, device: str) -> _FakeModule:
        self.device = device
        return self

    def load_state_dict(self, state: Mapping[str, object]) -> None:
        self.loaded_state = state


class _FakeModuleList(list[Any]):
    pass


class _FakeTorch:
    float32 = object()

    @staticmethod
    def no_grad() -> Any:
        return nullcontext()

    @staticmethod
    def tensor(values: Any, **_kwargs: Any) -> _FakeTensor:
        return _FakeTensor(values)

    @staticmethod
    def matmul(left: _FakeTensor, right: _FakeTensor) -> _FakeTensor:
        similarities = [
            sum(value * reference for value, reference in zip(row, right.values, strict=True))
            for row in left.values
        ]
        return _FakeTensor(similarities)


def _fake_torch_modules(checkpoint: Mapping[str, object]) -> dict[str, object]:
    fake_torch = _FakeTorch()
    fake_torch.load = lambda *_arguments, **_kwargs: checkpoint  # type: ignore[attr-defined]
    fake_torch.as_tensor = lambda _value: _FakeScalar(4.0)  # type: ignore[attr-defined]
    fake_nn = SimpleNamespace(
        Module=_FakeModule,
        Linear=_FakeModule,
        ModuleList=_FakeModuleList,
        LayerNorm=_FakeModule,
        Identity=_FakeModule,
        GELU=_FakeModule,
        ReLU=_FakeModule,
        SiLU=_FakeModule,
    )

    def normalize(value: Any, **_kwargs: Any) -> Any:
        return value

    return {
        "torch": fake_torch,
        "torch.nn": fake_nn,
        "torch.nn.functional": SimpleNamespace(normalize=normalize),
    }


def _fake_clm_checkpoint() -> dict[str, object]:
    return {
        "cfg": {
            "width": 4,
            "depth": 3,
            "hidden_size": _embedding_dimensions,
            "activation": "gelu",
            "layernorm": True,
            "residual": True,
        },
        "projection_dim": 2,
        "state_head": {"weight": "state"},
        "action_head": {"weight": "action"},
        "logit_scale": 1,
    }


def test_clm_loads_and_reuses_the_verified_projection_heads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    modules = _fake_torch_modules(_fake_clm_checkpoint())
    monkeypatch.setattr(clm_runtime, "import_module", modules.__getitem__)
    engine: Any = CLMEngine(
        checkpoint_path=tmp_path / "head.pt",
        embeddings_url="https://embeddings.example.test",
        embeddings_model="qwen3-8b",
        embeddings_api_key=None,
        timeout_seconds=5.0,
        device="cuda",
    )

    engine._ensure_heads()
    state_head = engine._state_head
    engine._ensure_heads()

    assert state_head is engine._state_head
    assert state_head is not None
    assert state_head.loaded_state == {"weight": "state"}
    assert state_head.device == "cuda"
    assert engine._scale == 4.0
    assert state_head(["vector"]) == ["vector", "vector"]


@pytest.mark.parametrize("temperature", [1.0, 0.5, 2.0, 1e-300])
def test_clm_engine_projects_a_real_multi_question_batch_with_the_loaded_head_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    temperature: float,
) -> None:
    modules = _fake_torch_modules(_fake_clm_checkpoint())
    monkeypatch.setattr(clm_runtime, "import_module", modules.__getitem__)
    engine: Any = CLMEngine(
        checkpoint_path=tmp_path / "head.pt",
        embeddings_url="https://embeddings.example.test",
        embeddings_model="qwen3-8b",
        embeddings_api_key=None,
        timeout_seconds=5.0,
        device="cpu",
    )
    engine._state_head = _FakeModule()
    engine._action_head = _FakeModule()

    def vector(first: float, second: float) -> list[float]:
        return [first, second, *([0.0] * (_embedding_dimensions - 2))]

    def request_embeddings_for_projection(_texts: list[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=[
                vector(1.0, 0.0),
                vector(0.0, 1.0),
                vector(1.0, 0.0),
                vector(0.0, 1.0),
                vector(1.0, 0.0),
                vector(0.0, 1.0),
            ],
            input_tokens=13,
        )

    def heads_are_ready() -> None:
        return None

    monkeypatch.setattr(engine, "_request_embeddings", request_embeddings_for_projection)
    monkeypatch.setattr(engine, "_ensure_heads", heads_are_ready)

    response = engine.answer(
        {
            "model": "clm-0.1-8b",
            "temperature": temperature,
            "state": "Review this action.",
            "questions": {
                "route": {
                    "type": "choice",
                    "criteria": {"allow": "Allow", "deny": "Deny"},
                },
                "safe": {"type": "noul", "criteria": None},
            },
        }
    )

    assert response["model"] == "clm-0.1-8b"
    assert response["usage"] == {"input_tokens": 13, "output_tokens": 0}
    assert response["answers"]["route"]["choice"] == "allow"
    assert response["answers"]["safe"]["noul"] > 0.5
    assert response["answers"]["route"]["probabilities"]["allow"] == pytest.approx(
        1 / (1 + math.exp(-1 / temperature))
    )


def test_clm_loopback_application_exposes_health_and_maps_provider_failures() -> None:
    class Engine:
        def __init__(self, error: Exception | None = None) -> None:
            self.error = error

        def answer(self, _payload: Mapping[str, object]) -> dict[str, Any]:
            if self.error is not None:
                raise self.error
            return {
                "model": "clm",
                "answers": {},
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }

    with TestClient(create_application(cast(CLMEngine, Engine()))) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert (
            client.post("/v1/systemone", json={"model": "clm", "questions": {}}).status_code == 200
        )
        assert client.post("/v1/systemone", json="not an object").status_code == 422

    cases = (
        (CLMInputError("bad input"), 422, "bad input"),
        (CLMEmbeddingError("offline"), 503, "configured embeddings endpoint is unavailable"),
        (CLMRuntimeError("bad head"), 503, "local CLM provider is unavailable"),
    )
    for error, expected_status, expected_detail in cases:
        with TestClient(create_application(cast(CLMEngine, Engine(error)))) as client:
            response = client.post("/v1/systemone", json={"model": "clm", "questions": {}})
        assert response.status_code == expected_status
        assert response.json()["detail"] == expected_detail


def test_clm_runtime_reads_required_environment_and_starts_the_private_loopback_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class Engine:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("CLM_EMBEDDINGS_URL", "https://embeddings.example.test/v1/embeddings")
    monkeypatch.setenv("CLM_EMBEDDINGS_MODEL", "qwen3-8b")
    monkeypatch.setenv("CLM_EMBEDDINGS_API_KEY", "test-key")
    monkeypatch.setenv("CLM_EMBEDDINGS_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("CLM_HOST", "127.0.0.2")
    monkeypatch.setenv("CLM_PORT", "8019")
    monkeypatch.setenv("CLM_DEVICE", "cuda")
    monkeypatch.setattr(clm_runtime, "CLMEngine", Engine)

    def create_fake_application(engine: Any) -> dict[str, Any]:
        return {"engine": engine}

    def run_uvicorn(application: Any, host: str, port: int) -> None:
        captured.update({"application": application, "host": host, "port": port})

    monkeypatch.setattr(clm_runtime, "create_application", create_fake_application)
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=run_uvicorn),
    )

    run_clm(tmp_path / "CLM_v0.1-8B.pt")

    assert captured["checkpoint_path"] == tmp_path / "CLM_v0.1-8B.pt"
    assert captured["timeout_seconds"] == 7.5
    assert captured["device"] == "cuda"
    assert captured["host"] == "127.0.0.2"
    assert captured["port"] == 8019


def test_clm_runtime_rejects_missing_or_invalid_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLM_EMBEDDINGS_URL", raising=False)
    with pytest.raises(CLMRuntimeError, match="CLM_EMBEDDINGS_URL"):
        run_clm(Path("head.pt"))

    monkeypatch.setenv("CLM_EMBEDDINGS_URL", "https://embeddings.example.test")
    monkeypatch.setenv("CLM_EMBEDDINGS_MODEL", "qwen3-8b")
    monkeypatch.setenv("CLM_EMBEDDINGS_TIMEOUT_SECONDS", "zero")
    with pytest.raises(CLMRuntimeError, match="positive number"):
        run_clm(Path("head.pt"))
