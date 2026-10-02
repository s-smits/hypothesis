import pytest


@pytest.fixture(autouse=True)
def results_dir(tmp_path, monkeypatch):
    """Keep each test's node cache out of the repo and away from other tests."""
    monkeypatch.setenv("NODE_DAG_RESULTS", str(tmp_path))
    return tmp_path
