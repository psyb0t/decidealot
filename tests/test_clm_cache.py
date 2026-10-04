"""Repeated candidate embeddings are bounded, expire, and preserve input order."""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from decidealot.clm_runtime import CLMEmbeddingError, CLMEngine, CLMInputError, EmbeddingBatch


class ProbeEngine(CLMEngine):
    def encode(self, states: list[str], candidates: list[str]) -> EmbeddingBatch:
        return self._cached_embeddings(states, candidates)


def _engine(**kwargs: Any) -> ProbeEngine:
    return ProbeEngine(
        Path("unused.pt"), "https://encoder.example.test", "qwen", None, 5, "cpu", **kwargs
    )


def test_repeated_candidates_are_cached_but_states_are_not(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    calls: list[list[str]] = []

    def encode(texts: list[str]) -> EmbeddingBatch:
        calls.append(texts)
        return EmbeddingBatch([[2.0, *([0.0] * 4095)] for _ in texts], len(texts))

    monkeypatch.setattr(engine, "_request_embeddings", encode)
    first = engine.encode(["state-a"], ["allow", "deny", "allow"])
    second = engine.encode(["state-b"], ["deny", "allow"])
    assert calls == [["state-a", "allow", "deny"], ["state-b"]]
    assert first.input_tokens == 3
    assert second.input_tokens == 1
    assert first.vectors[0][0] == pytest.approx(1)
    assert second.vectors[1] == first.vectors[2]


def test_cache_expiration_eviction_and_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    engine = _engine(cache_entries=1, cache_ttl_seconds=10)
    calls: list[list[str]] = []

    def encode(texts: list[str]) -> EmbeddingBatch:
        calls.append(texts)
        return EmbeddingBatch([[1.0] * 4096 for _ in texts], len(texts))

    monkeypatch.setattr(engine, "_request_embeddings", encode)
    engine.encode(["s"], ["a"])
    engine.encode(["s"], ["a"])
    clock[0] = 10.0
    engine.encode(["s"], ["a"])
    engine.encode(["s"], ["b"])
    engine.encode(["s"], ["a"])
    assert calls == [["s", "a"], ["s"], ["s", "a"], ["s", "b"], ["s", "a"]]
    disabled = _engine(cache_entries=0)
    monkeypatch.setattr(disabled, "_request_embeddings", encode)
    disabled.encode(["s"], ["a"])
    disabled.encode(["s"], ["a"])
    assert calls[-2:] == [["s", "a"], ["s", "a"]]


def test_concurrent_candidate_misses_have_one_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    calls: list[list[str]] = []

    def encode(texts: list[str]) -> EmbeddingBatch:
        calls.append(texts)
        return EmbeddingBatch([[1.0] * 4096 for _ in texts], len(texts))

    monkeypatch.setattr(engine, "_request_embeddings", encode)

    def run(_index: int) -> EmbeddingBatch:
        return engine.encode(["s"], ["a"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert len(results) == 4
    assert sum("a" in call for call in calls) == 1


def test_utf8_limit_is_checked_before_encoder_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(max_text_bytes=4)
    calls: list[list[str]] = []

    def encode(texts: list[str]) -> EmbeddingBatch:
        calls.append(texts)
        return EmbeddingBatch([[1.0] * 4096 for _ in texts], len(texts))

    monkeypatch.setattr(engine, "_request_embeddings", encode)
    engine.encode(["éé"], ["a"])
    with pytest.raises(CLMInputError, match="byte limit"):
        engine.encode(["ééx"], ["a"])
    assert len(calls) == 1


def test_failed_embedding_fill_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    calls: list[list[str]] = []

    def encode(texts: list[str]) -> EmbeddingBatch:
        calls.append(texts)
        if len(calls) == 1:
            return EmbeddingBatch([[float("nan")] * 4096 for _ in texts], 2)
        return EmbeddingBatch([[1.0] * 4096 for _ in texts], 2)

    monkeypatch.setattr(engine, "_request_embeddings", encode)
    with pytest.raises(CLMEmbeddingError):
        engine.encode(["s"], ["a"])
    engine.encode(["s"], ["a"])
    assert calls == [["s", "a"], ["s", "a"]]
