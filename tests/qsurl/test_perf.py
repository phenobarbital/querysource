"""Opt-in parse performance (spec AC21): run with `pytest -m perf`."""
from __future__ import annotations

import statistics
import time
from typing import Callable

import pytest

import querysource.qsurl as qsurl
from querysource.qsurl import HAS_RUST, _fallback

pytestmark = pytest.mark.perf


def _median_ms(fn: Callable[[str], object], src: str, runs: int = 50) -> float:
    """Warm up once, then time ``runs`` calls; return the median duration in milliseconds."""
    fn(src)  # warm up (compiles the Lark grammar / primes any caches)
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        fn(src)
        samples.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(samples)


def _eight_kb_url(corpus: list[dict]) -> str:
    for case in corpus:
        if case["id"] == "eight_kb_url":
            return case["input"]
    raise AssertionError("corpus.json is missing the eight_kb_url case")


@pytest.mark.skipif(not HAS_RUST, reason="qsurl Rust extension not installed")
def test_rust_8kb_under_1ms(corpus):
    src = _eight_kb_url(corpus)
    median = _median_ms(qsurl._rs.parse, src)  # noqa: SLF001 - intentional back-end probe
    assert median < 1.0, f"Rust parse median {median:.3f}ms >= 1ms for an 8KB URL"


def test_lark_8kb_under_100ms(corpus):
    src = _eight_kb_url(corpus)
    median = _median_ms(_fallback.parse, src)
    assert median < 100.0, f"Lark parse median {median:.3f}ms >= 100ms for an 8KB URL"
