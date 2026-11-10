from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from repomentor.experiment_ledger import RequestLedger
from repomentor.harness import HarnessError


def test_request_cap_is_transactional_and_preserves_ambiguous_dispatches(tmp_path):
    store = RequestLedger(tmp_path / "requests.sqlite", limit=3, experiment="test")
    settings = SimpleNamespace(backend="ollama", baseUrl="http://127.0.0.1:11435/v1", config_id="test")

    def attempt(i):
        try:
            store.reserve(settings, str(i), {"request": i}, {"fits": True, "context_window": 8192})
            return True
        except HarnessError as exc:
            assert exc.code == "suite_budget"
            return False

    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(attempt, range(8))) == 3
    restored = RequestLedger(tmp_path / "requests.sqlite", limit=3, experiment="test")
    assert len(restored.rows()) == 3
    assert not attempt(9)


def test_ledger_rejects_remote_and_overbudget_without_reserving(tmp_path):
    store = RequestLedger(tmp_path / "requests.sqlite", limit=3, experiment="test")
    settings = SimpleNamespace(backend="generic", baseUrl="https://example.com", config_id="test")
    with pytest.raises(HarnessError, match="remote"):
        store.reserve(settings, "x", {}, {"fits": True, "context_window": 8192})
    assert not store.rows()


@pytest.mark.parametrize("window", [16384, 40960])
def test_larger_window_is_opt_in_and_pilot_cap_shares_global_ledger(tmp_path, window):
    path = tmp_path / "requests.sqlite"
    settings = SimpleNamespace(
        backend="ollama", baseUrl="http://127.0.0.1:11435/v1", config_id="local", context_window=window
    )
    budget = {"fits": True, "context_window": window, "total": window - 1000}
    old = RequestLedger(path, limit=3, experiment="test")
    with pytest.raises(HarnessError, match="authorized"):
        old.reserve(settings, "quality-v1/x", {}, budget)
    new = RequestLedger(
        path,
        limit=3,
        experiment="test",
        context_windows=(8192, window),
        batch_prefix="quality-v1",
        batch_limit=1,
    )
    with pytest.raises(HarnessError, match="outside"):
        new.reserve(settings, "another/x", {}, budget)
    new.reserve(settings, "quality-v1/x", {}, budget)
    with pytest.raises(HarnessError, match="diagnostic"):
        new.reserve(settings, "quality-v1/y", {}, budget)
    assert len(old.rows()) == 1
