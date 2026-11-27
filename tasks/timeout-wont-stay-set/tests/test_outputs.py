"""Verifier assertions for timeout-wont-stay-set (stacked layers).

The gray-box driver (tests/Verifier.java, run by tests/test.sh) checks three
per-tenant isolation guarantees in order, stopping at the first failure, and
writes a JSON verdict. All imports are stdlib (no --with needed).
"""

import json
from pathlib import Path

RESULT = Path("/tmp/verifier_result.json")


def _load():
    assert RESULT.exists(), f"verifier produced no result at {RESULT} (see /tmp/*.log)"
    return json.loads(RESULT.read_text())


def test_gateway_compiles_and_harness_valid():
    """The gateway compiles, its public API is intact, and the black hole hangs connects."""
    d = _load()
    assert d.get("stage") != "app-compile", "the gateway source under /app/src does not compile"
    assert d.get("stage") != "verifier-compile", (
        "the gateway public API (PoolManager.register/openConnection, "
        "FailoverController.reestablish, FailoverOutcome) was changed, so the verifier no longer compiles against it"
    )
    assert "error" not in d, f"verifier raised: {d.get('error')}"
    assert d.get("harnessValid") is True, f"black hole did not hang / harness invalid: {d}"


def test_layer1_connect_timeout_isolation():
    """L1: every pool's physical connect gives up at its OWN connect timeout, regardless of pool count or registration order."""
    d = _load()
    assert d.get("l1") is True, f"L1 connect-timeout isolation violated: {d.get('l1detail')}"


def test_layer2_retry_budget_isolation():
    """L2: each tenant's failover retries up to its OWN maxRetries, regardless of another tenant's prior failover."""
    d = _load()
    assert d.get("l2") is True, (
        f"L2 retry-budget isolation violated (deeper layers are only evaluated once L1 passes): {d.get('l2detail')}"
    )


def test_layer3_concurrency_cap_isolation():
    """L3: a tenant under its own in-flight cap is not throttled by another tenant saturating its cap."""
    d = _load()
    assert d.get("l3") is True, (
        f"L3 concurrency-cap isolation violated (deeper layers are only evaluated once L1+L2 pass): {d.get('l3detail')}"
    )
